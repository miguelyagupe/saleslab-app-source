"""Strict model output and deterministic binding to verified evidence."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.metrics.trace import TRACE_NAMESPACE

ANALYST_VERSION = "financial-analyst-v1"
SCHEMA_VERSION = "1"
SUPPORTED_CAPABILITIES = frozenset(("summarize", "calculate", "compare"))
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TOKEN = re.compile(r"\{\{([0-9a-f-]{36})\}\}")


class FindingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: Annotated[Text, Field(max_length=160)]
    statement: Annotated[Text, Field(max_length=2000)]
    claim_type: Literal["fact", "calculation"]
    capability: Literal["summarize", "calculate", "compare"]
    evidence_ids: Annotated[list[str], Field(min_length=1, max_length=10)]


class AnalystDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1"]
    language: Literal["pt", "en", "es", "fr"]
    findings: Annotated[list[FindingDraft], Field(min_length=1, max_length=20)]


class InvalidAnalystOutput(ValueError):
    """Untrusted model output cannot become an analyst draft."""


class AnalystUnavailable(RuntimeError):
    """Provider failed or returned an incomplete structured response."""


class FinancialModel(Protocol):
    @property
    def configuration(self) -> dict: ...

    async def generate(self, context: dict) -> dict: ...


@dataclass(frozen=True)
class AnalystResult:
    run_id: UUID
    payload: dict
    replayed: bool


def bind_findings(raw, context):
    """The model selects evidence; it cannot write values, scores or provenance."""
    try:
        if len(json.dumps(raw, allow_nan=False)) > 128_000:
            raise ValueError("output too large")
        draft = AnalystDraft.model_validate(raw)
        if draft.language != context["language"]:
            raise ValueError("wrong output language")
        index = {e["evidence_id"]: e for e in context["evidence"]}
        result, seen = [], set()
        for finding in draft.findings:
            ids = finding.evidence_ids
            if len(set(ids)) != len(ids) or not set(ids) <= index.keys():
                raise ValueError("unknown or duplicate evidence")
            evidence = [index[i] for i in ids]
            if finding.capability not in context["allowed_capabilities"]:
                raise ValueError("capability not authorized")
            expected_type = (
                "fact" if finding.capability == "summarize" else "calculation"
            )
            if finding.claim_type != expected_type or any(
                e["capability"] != finding.capability for e in evidence
            ):
                raise ValueError("claim does not match evidence capability")
            if (
                len({semantic_hash(e["period"]) for e in evidence}) != 1
                or len({semantic_hash([e["scope"], e["dimensions"]]) for e in evidence})
                != 1
            ):
                raise ValueError("mixed periods or scopes")
            tokens = TOKEN.findall(finding.statement)
            # Numeric values may only be cited through bound placeholders. The
            # renderer later formats canonical values; the model does no arithmetic.
            plain = TOKEN.sub("", finding.statement)
            if not tokens or set(tokens) != set(ids):
                raise ValueError("each cited value must use its evidence placeholder")
            if any(c.isnumeric() for c in plain + finding.title) or any(
                c in plain + finding.title for c in ("{", "}")
            ):
                raise ValueError("unbound numeric value or malformed placeholder")
            item = finding.model_dump()
            fingerprint = semantic_hash(item)
            if fingerprint in seen:
                raise ValueError("duplicate finding")
            seen.add(fingerprint)
            result.append(
                {
                    "finding_id": str(
                        uuid5(
                            TRACE_NAMESPACE,
                            f"finding:{context['run_id']}:{fingerprint}",
                        )
                    ),
                    "run_id": context["run_id"],
                    "module": "financial",
                    "title": finding.title,
                    "statement_template": finding.statement,
                    "claim_type": finding.claim_type,
                    "causal_status": "observed",
                    "capability": finding.capability,
                    "capability_decision_id": context["decision_ids"][
                        finding.capability
                    ],
                    "evidence_ids": ids,
                    "value_bindings": {i: deepcopy(index[i]) for i in ids},
                    "metric_observation_ids": sorted(
                        {
                            e["observation_id"]
                            for e in evidence
                            if e["type"] == "calculated"
                        }
                    ),
                    "counterevidence_ids": [],
                    "missing_data_ids": [
                        r["request_id"] for r in context["missing_data_requests"]
                    ],
                    "assumptions": [],
                    "scope": {
                        "scope": evidence[0]["scope"],
                        "dimensions": evidence[0]["dimensions"],
                        "period": evidence[0]["period"],
                    },
                    "confidence_score": None,
                    "materiality_score": None,
                    "analyst_version": ANALYST_VERSION,
                    "status": "draft",
                    "requires_independent_audit": True,
                }
            )
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidAnalystOutput(
            "analyst output failed schema/evidence/capability validation"
        ) from exc
