"""Pure assembly from independently verified evidence and an exact F-07 approval."""

from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid5

from saleslab_v2.audit.contracts import (
    CRITIC_CHECKS,
    assemble_result,
    bind_critique,
)
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.i18n.catalog import TerminologyRegistry
from saleslab_v2.metrics.trace import TRACE_NAMESPACE

from .contracts import COMPILER_VERSION, RENDER_VERSION, InvalidReport, ReportResult


def verify_approval(audit, draft, context, *, deterministic_checks, revision_number):
    """Rebuild all semantic checks and aggregate verdict; a status flag is not authority."""
    try:
        if (
            audit["status"] not in {"approved", "approved_with_warnings"}
            or audit["retryable"] is not False
            or audit["checkpoint_hash"] != context["checkpoint_hash"]
            or audit["draft_hash"] != semantic_hash(draft)
            or audit["run_id"] != context["run_id"]
            or audit["revision_number"] != revision_number
            or audit["critic_configuration"]["role"] != "independent_critic"
            or not deterministic_checks
            or any(c["status"] != "pass" for c in deterministic_checks)
            or audit["checks"][: len(deterministic_checks)] != deterministic_checks
        ):
            raise ValueError("audit is not an exact approval")
        semantic = audit["checks"][len(deterministic_checks) :]
        raw = {
            "schema_version": "1",
            "language": context["language"],
            "findings": [
                {
                    "finding_id": finding["finding_id"],
                    "checks": [
                        {
                            "check_type": c["check_type"],
                            "outcome": c["status"],
                            "message": c["message"],
                            "remediation": c["remediation"],
                        }
                        for c in semantic
                        if c["related_object_ids"] == [finding["finding_id"]]
                    ],
                }
                for finding in draft["findings"]
            ],
        }
        bound, _ = bind_critique(
            raw, draft, audit_id=UUID(audit["audit_id"]), language=context["language"]
        )
        # Preserve the critic's original order without trusting any check metadata.
        if len(bound) != len(semantic) or {semantic_hash(c) for c in bound} != {
            semantic_hash(c) for c in semantic
        }:
            raise ValueError("semantic checks contain unexpected fields or objects")
        expected = assemble_result(
            audit_id=UUID(audit["audit_id"]),
            run_id=UUID(context["run_id"]),
            draft_stage_id=UUID(audit["draft_stage_id"]),
            draft_hash=semantic_hash(draft),
            checkpoint_hash=context["checkpoint_hash"],
            checks=audit["checks"],
            finding_ids={f["finding_id"] for f in draft["findings"]},
            configuration=audit["critic_configuration"],
            audited_at=datetime.fromisoformat(audit["audited_at"]),
            revision_number=revision_number,
        )
        if audit != expected:
            raise ValueError("audit aggregate differs from its checks")
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidReport(
            "a complete, exact independent approval is required"
        ) from exc


def compile_report(
    *,
    run,
    context,
    draft,
    audit,
    checkpoint,
    generated_at,
    locale,
    render_version=RENDER_VERSION,
):
    """Caller verifies persisted authority; no free text or new financial judgment here."""
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise InvalidReport("report timestamp must be timezone-aware")
    report_id = uuid5(TRACE_NAMESPACE, f"report:{run.id}:{COMPILER_VERSION}")
    findings = deepcopy(draft["findings"])
    if (
        audit["status"] not in {"approved", "approved_with_warnings"}
        or set(audit["approved_object_ids"]) != {f["finding_id"] for f in findings}
        or audit["rejected_object_ids"]
        or draft["forecasts"]
        or draft["recommendations"]
    ):
        raise InvalidReport("only the complete approved factual draft can be compiled")
    for finding in findings:
        finding.update(status="audited", requires_independent_audit=False)
    quality = context["quality"]
    limitations = [
        {"limitation_id": c["check_id"], "kind": "audit_warning", "detail": deepcopy(c)}
        for c in audit["checks"]
        if c["status"] == "warning"
    ] + [
        {"limitation_id": i["issue_id"], "kind": "quality_issue", "detail": deepcopy(i)}
        for i in quality["issues"]
    ]
    periods = {semantic_hash(e["period"]): e["period"] for e in context["evidence"]}
    payload = {
        "report_id": str(report_id),
        "run_id": str(run.id),
        "workspace_id": str(run.workspace_id),
        "schema_version": "1",
        "module": "financial",
        "generated_at": generated_at.isoformat(),
        "compiler_version": COMPILER_VERSION,
        "report_version": "1",
        "render_version": render_version,
        "language": context["language"],
        "locale": locale,
        "company_profile": None,
        "analysis_periods": [periods[k] for k in sorted(periods)],
        "comparison_basis": sorted({p["comparison_basis"] for p in periods.values()}),
        "executive_summary": {
            "headline_key": "financial_report",
            "verified_fact_ids": [f["finding_id"] for f in findings],
            # Materiality has not been measured; do not invent a ranking.
            "material_finding_ids": [],
            "limitation_ids": [i["limitation_id"] for i in limitations],
            "next_action_ids": [],
        },
        "coverage_assessment": deepcopy(quality["coverage"]),
        "capability_decisions": deepcopy(quality["decisions"]),
        "data_quality_issues": deepcopy(quality["issues"]),
        "metric_observations": deepcopy(checkpoint["metrics"]),
        "evidence": deepcopy(context["evidence"]),
        "findings": findings,
        "forecasts": [],
        "recommendations": [],
        "reconciliations": deepcopy(context["reconciliations"]),
        "audit_result": deepcopy(audit),
        "missing_data_requests": deepcopy(context["missing_data_requests"]),
        "limitations": limitations,
        "methodology": {
            "pipeline_version": run.pipeline_version,
            "analyst_version": findings[0]["analyst_version"],
            "auditor_version": audit["auditor_version"],
            "terminology_version": TerminologyRegistry.version,
            "statement_check_families": sorted(CRITIC_CHECKS),
            "policy_key": "factual_only",
            "materiality_ranked": False,
            "live_model_accuracy_certified": False,
        },
        "source_index": deepcopy(context["sources"]),
        "approval_binding": {
            "audit_id": audit["audit_id"],
            "audit_hash": semantic_hash(audit),
            "draft_stage_id": audit["draft_stage_id"],
            "draft_hash": audit["draft_hash"],
            "checkpoint_hash": context["checkpoint_hash"],
        },
        "display_preferences": {
            "rounding": (
                "canonical_exact"
                if render_version == "financial-render-v1"
                else "trace_policy"
            ),
            "scale": "raw",
        },
    }
    if "working_capital_definitions" in context:
        payload["methodology"]["working_capital_definitions"] = deepcopy(
            context["working_capital_definitions"]
        )
    if "roic_sources" in context:
        payload["methodology"]["roic_sources"] = deepcopy(context["roic_sources"])
    if "capex_definitions" in context:
        payload["methodology"]["capex_definitions"] = deepcopy(
            context["capex_definitions"]
        )
    if "debt_definitions" in context:
        payload["methodology"]["debt_definitions"] = deepcopy(
            context["debt_definitions"]
        )
    if "growth_definitions" in context:
        payload["methodology"]["growth_definitions"] = deepcopy(
            context["growth_definitions"]
        )
    if "evidence_selection" in context:
        payload["methodology"]["evidence_selection"] = deepcopy(
            context["evidence_selection"]
        )
    terminology_versions = context.get("terminology_versions")
    if terminology_versions:
        if len(terminology_versions) == 1:
            payload["methodology"]["terminology_version"] = terminology_versions[0]
        else:
            payload["methodology"].pop("terminology_version")
            payload["methodology"]["terminology_versions"] = list(terminology_versions)
    return ReportResult.model_validate(payload).model_dump()
