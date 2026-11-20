"""Verify the immutable deterministic checkpoint and build scoped evidence."""

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid5

from saleslab_v2.domain.financial import NormalizedObservation
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.domain.normalization import canonical_decimal
from saleslab_v2.domain.value_objects import Period
from saleslab_v2.metrics.trace import TRACE_NAMESPACE, canonical_json
from saleslab_v2.pipeline.adapters import normalized_id
from saleslab_v2.pipeline.contracts import STAGES

from .contracts import SUPPORTED_CAPABILITIES


class InvalidCheckpoint(ValueError):
    """Persisted evidence is incomplete, inconsistent or outside its run."""


def unpack(value):
    """Project existing typed canonical JSON into model-safe JSON, not floats."""
    if isinstance(value, dict):
        if len(value) == 1 and next(iter(value)).startswith("$"):
            key = next(iter(value))
            if key not in {"$uuid", "$enum", "$date", "$datetime", "$decimal"}:
                raise InvalidCheckpoint("unknown canonical type")
            return value[key]
        return {k: unpack(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unpack(v) for v in value]
    return value


def verify_checkpoint(run, stages):
    """Stage hashes and semantic bindings are checked before any model call."""
    try:
        if set(stages) != set(STAGES):
            raise ValueError("missing pipeline stages")
        for stage in stages.values():
            if (
                stage.run_id != run.id
                or stage.status != "completed"
                or stage.output_hash != semantic_hash(stage.payload)
            ):
                raise ValueError("stage integrity mismatch")
        checkpoint = stages["checkpoint"]
        payload = checkpoint.payload
        request = stages["normalization"].payload["request"]
        request_hash = semantic_hash(request)
        if (
            checkpoint.input_hash != request_hash
            or payload["request_hash"] != request_hash
            or payload["run_id"] != str(run.id)
            or request_hash not in run.input_hashes
            or any(
                unpack(s["document"]["workspace_id"]) != str(run.workspace_id)
                for s in request["sources"]
            )
            or unpack(request["language"])
            != {k: v for k, v in run.language_context.items() if k != "format_locale"}
        ):
            raise ValueError("checkpoint request binding mismatch")
        previous_hash = request_hash
        for name in STAGES[:-1]:
            stage = stages[name]
            if (
                stage.input_hash != previous_hash
                or payload["stage_hashes"][name] != stage.output_hash
            ):
                raise ValueError("stage chain mismatch")
            previous_hash = stage.output_hash
        if (
            payload["metrics"] != stages["metrics"].payload["metrics"]
            or payload["reconciliations"] != stages["reconciliation"].payload["results"]
            or payload["quality"] != stages["quality_gates"].payload
            or payload["requested_capabilities"] != unpack(request["capabilities"])
        ):
            raise ValueError("checkpoint result mismatch")
        decisions = unpack(payload["quality"]["decisions"])
        capabilities = {
            "summarize",
            "calculate",
            "compare",
            "diagnose",
            "infer_cause",
            "forecast",
            "quantify_impact",
            "recommend_reversible",
            "recommend_material",
        }
        if (
            len(decisions) != 9
            or {d["capability"] for d in decisions} != capabilities
            or any(d["run_id"] != str(run.id) for d in decisions)
        ):
            raise ValueError("invalid capability matrix")
        ready = all(
            d["decision_status"] == "allowed"
            for d in decisions
            if d["capability"] in payload["requested_capabilities"]
        )
        if payload["ready"] is not ready or (
            ready and payload["workflow_status"] != "calculating"
        ):
            raise ValueError("invalid ready checkpoint")
        return deepcopy(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCheckpoint("deterministic checkpoint validation failed") from exc


def _normalized(raw):
    value = unpack(raw)
    value["source_document_id"] = UUID(value["source_document_id"])
    for key in ("value", "numerator", "denominator", "confidence"):
        if value[key] is not None:
            value[key] = Decimal(value[key])
    period = value["period"]
    period["start_date"], period["end_date"] = date.fromisoformat(
        period["start_date"]
    ), date.fromisoformat(period["end_date"])
    value["period"] = Period(**period)
    value["issues"] = ()
    value["dimensions"] = tuple(tuple(d) for d in value["dimensions"])
    return NormalizedObservation(**value)


def build_context(run, checkpoint, stages, sources):
    try:
        quality = unpack(checkpoint["quality"])
        decisions = {d["capability"]: d for d in quality["decisions"]}
        allowed = sorted(
            c
            for c in checkpoint["requested_capabilities"]
            if c in SUPPORTED_CAPABILITIES
            and decisions[c]["decision_status"] == "allowed"
        )
        source_index = {
            str(s.id): {
                "source_id": str(s.id),
                "filename": s.filename,
                "content_hash": s.content_hash,
                "parser_version": s.parser_version,
                "languages": s.language_metadata,
            }
            for s in sources
        }
        if set(source_index) != set(run.source_ids):
            raise ValueError("missing scoped sources")
        normalized, source_refs = {}, {}
        for raw in stages["normalization"].payload["observations"]:
            if (
                raw["normalization_status"] != "normalized"
                or raw["value"] is None
                or raw["issues"]
            ):
                continue
            observation = _normalized(raw)
            sid = str(observation.source_document_id)
            if (
                sid not in source_index
                or source_index[sid]["content_hash"] != observation.source_content_hash
            ):
                raise ValueError("source provenance mismatch")
            oid = str(normalized_id(observation))
            normalized[oid] = unpack(raw)
            source_refs[oid] = [
                {
                    "source_id": sid,
                    "content_hash": observation.source_content_hash,
                    "location": observation.source_reference,
                }
            ]
        metric_items = {
            unpack(m["observation"]["observation_id"]): unpack(m)
            for m in checkpoint["metrics"]
        }

        selection = unpack(stages["normalization"].payload["request"]).get(
            "analyst_metric_keys"
        )
        metric_keys = {m["key"] for m in metric_items.values()}
        if selection is not None and (
            not isinstance(selection, list)
            or any(not isinstance(key, str) for key in selection)
            or not set(selection) <= metric_keys
        ):
            raise ValueError("invalid analyst metric selection")

        def refs(oid, trail=()):
            if oid in trail:
                raise ValueError("cyclic metric trace")
            if oid in source_refs:
                return source_refs[oid]
            metric = metric_items[oid]
            collected = {}
            for item in metric["trace"]["inputs"]:
                for ref in refs(item["observation_id"], (*trail, oid)):
                    collected[semantic_hash(ref)] = ref
            source_refs[oid] = [collected[k] for k in sorted(collected)]
            return source_refs[oid]

        evidence = []
        if "summarize" in allowed:
            for oid, observation in normalized.items():
                evidence.append(
                    {
                        "evidence_id": str(
                            uuid5(TRACE_NAMESPACE, f"evidence:{run.id}:{oid}")
                        ),
                        "type": "direct",
                        "observation_id": oid,
                        "concept_id": observation["concept_id"],
                        "value": canonical_decimal(Decimal(observation["value"])),
                        "unit": observation["unit"],
                        "currency": observation["currency"],
                        "period": observation["period"],
                        "scope": observation["scope"],
                        "dimensions": observation["dimensions"],
                        "source_references": source_refs[oid],
                        "calculation_trace_id": None,
                        "capability": "summarize",
                    }
                )
        for oid, metric in metric_items.items():
            if selection is not None and metric["key"] not in selection:
                continue
            observation, trace = metric["observation"], metric["trace"]
            trace_hash = sha256(canonical_json(trace).encode()).hexdigest()
            if (
                observation["calculation_trace_hash"] != trace_hash
                or observation["calculation_trace_id"]
                != str(
                    uuid5(TRACE_NAMESPACE, f"{run.workspace_id}:{run.id}:{trace_hash}")
                )
                or oid
                != str(
                    uuid5(
                        TRACE_NAMESPACE,
                        f"observation:{run.workspace_id}:{run.id}:{trace_hash}",
                    )
                )
            ):
                raise ValueError("calculation identity mismatch")
            references = refs(oid)
            capability = (
                "compare"
                if observation["metric_id"].startswith("revenue_growth_")
                else "calculate"
            )
            if capability not in allowed:
                continue
            evidence.append(
                {
                    "evidence_id": str(
                        uuid5(TRACE_NAMESPACE, f"evidence:{run.id}:{oid}")
                    ),
                    "type": "calculated",
                    "observation_id": oid,
                    "concept_id": observation["metric_id"],
                    "value": canonical_decimal(Decimal(observation["value"])),
                    "unit": observation["unit"],
                    "currency": observation["currency"],
                    "period": observation["period"],
                    "scope": observation["scope"],
                    "dimensions": observation["dimensions"],
                    "source_references": references,
                    "calculation_trace_id": observation["calculation_trace_id"],
                    "trace": trace,
                    "capability": capability,
                }
            )
        gaps = {}
        for decision in quality["decisions"]:
            if decision["decision_status"] != "allowed":
                for requirement in decision["requirements_to_unlock"]:
                    gaps.setdefault(requirement, []).append(decision["capability"])
        context = {
            "run_id": str(run.id),
            "workspace_id": str(run.workspace_id),
            "checkpoint_hash": stages["checkpoint"].output_hash,
            "language": run.language_context["report_language"],
            "quality": quality,
            "allowed_capabilities": allowed,
            "decision_ids": {c: decisions[c]["decision_id"] for c in allowed},
            "evidence": sorted(evidence, key=lambda e: e["evidence_id"]),
            "sources": [source_index[k] for k in sorted(source_index)],
            "reconciliations": unpack(checkpoint["reconciliations"]),
            "missing_data_requests": [
                {
                    "request_id": str(uuid5(TRACE_NAMESPACE, f"missing:{run.id}:{r}")),
                    "requirement_code": r,
                    "unlocks_capabilities": sorted(caps),
                }
                for r, caps in sorted(gaps.items())
            ],
            "requires_independent_audit": True,
        }
        if selection is not None:
            context["evidence_selection"] = {
                "version": "metric-step-allowlist-v1",
                "included_keys": sorted(set(selection)),
                "excluded_keys": sorted(metric_keys - set(selection)),
            }
        terminology_versions = sorted(
            {
                raw["terminology_version"]
                for raw in stages["normalization"].payload["observations"]
            }
        )
        # Preserve the exact legacy analyst/audit context. New source catalogs
        # carry their actual versions into approval-bound report methodology.
        if terminology_versions and terminology_versions != [
            "financial-terminology-v1"
        ]:
            context["terminology_versions"] = terminology_versions
        definitions = []
        debt_definitions = []
        capex_definitions = []
        working_capital_definitions = []
        raw_request = unpack(stages["normalization"].payload["request"])
        roic_sources = []
        for batch in raw_request["sources"]:
            if batch["metadata"].get("roic_binding") is not None:
                value = json.loads(batch["metadata"]["roic_binding"])
                roic_sources.append(
                    {
                        "source_id": batch["document"]["source_id"],
                        "content_hash": batch["document"]["content_hash"],
                        "binding_hash": value["binding"]["binding_hash"],
                        "evidence": value["binding"]["evidence"],
                        "confirmation": "user_confirmed_source_quote",
                        "basis": "reported_nopat_and_explicit_period_average_not_annualized",
                    }
                )
            if batch["metadata"].get("terminology_version", "1") not in {
                "6",
                "7",
                "8",
                "9",
                "10",
            }:
                continue
            document = batch["document"]
            for row in batch["rows"]:
                if row.get("definition_quote") is not None:
                    from saleslab_v2.i18n.financial_v7 import DEBT_LABELS

                    is_debt = any(
                        o["concept_id"] in DEBT_LABELS
                        and str(o["source_document_id"]) == str(document["source_id"])
                        and o["source_reference"] == row["source_reference"]
                        for o in normalized.values()
                    )
                    from saleslab_v2.i18n.financial_v8 import (
                        CAPEX_LABELS,
                        CASH_CAPEX_CONCEPTS,
                    )

                    has_cash_capex = any(
                        o["concept_id"] in CASH_CAPEX_CONCEPTS
                        and str(o["source_document_id"]) == str(document["source_id"])
                        for o in normalized.values()
                    )
                    is_capex = batch["metadata"].get("terminology_version") in {
                        "8",
                        "9",
                        "10",
                    } and any(
                        (
                            o["concept_id"] in CAPEX_LABELS
                            or (has_cash_capex and o["concept_id"] == "capex")
                        )
                        and str(o["source_document_id"]) == str(document["source_id"])
                        and o["source_reference"] == row["source_reference"]
                        for o in normalized.values()
                    )
                    from saleslab_v2.i18n.financial_v10 import (
                        WORKING_CAPITAL_COMPONENTS,
                        WORKING_CAPITAL_LABELS,
                    )

                    is_wc = batch["metadata"].get(
                        "terminology_version"
                    ) == "10" and any(
                        o["concept_id"]
                        in {*WORKING_CAPITAL_LABELS, *WORKING_CAPITAL_COMPONENTS}
                        and str(o["source_document_id"]) == str(document["source_id"])
                        and o["source_reference"] == row["source_reference"]
                        for o in normalized.values()
                    )
                    collection = (
                        working_capital_definitions
                        if is_wc
                        else (
                            capex_definitions
                            if is_capex
                            else debt_definitions if is_debt else definitions
                        )
                    )
                    collection.append(
                        {
                            "source_id": document["source_id"],
                            "content_hash": document["content_hash"],
                            "location": row["source_reference"],
                            "definition_quote": row["definition_quote"],
                            "confirmation": "user_confirmed_source_quote",
                        }
                    )
        if working_capital_definitions:
            context["working_capital_definitions"] = sorted(
                working_capital_definitions,
                key=lambda r: (r["source_id"], r["location"]),
            )
        if roic_sources:
            context["roic_sources"] = roic_sources
        if definitions:
            context["growth_definitions"] = sorted(
                definitions, key=lambda r: (r["source_id"], r["location"])
            )
        if debt_definitions:
            context["debt_definitions"] = sorted(
                debt_definitions, key=lambda r: (r["source_id"], r["location"])
            )
        if capex_definitions:
            context["capex_definitions"] = sorted(
                capex_definitions, key=lambda r: (r["source_id"], r["location"])
            )
        if len(json.dumps(context, ensure_ascii=True).encode()) > 256_000:
            raise ValueError("analyst context exceeds bounded input size")
        return context
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InvalidCheckpoint(
            "cannot build a complete, scoped analyst evidence context"
        ) from exc
