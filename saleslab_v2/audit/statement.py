"""Independent deterministic re-execution and full draft contract checks."""

from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.analyst.contracts import InvalidAnalystOutput, bind_findings
from saleslab_v2.domain.idempotency import canonicalize, semantic_hash
from saleslab_v2.pipeline.contracts import STAGES
from saleslab_v2.pipeline.evaluator import evaluate
from saleslab_v2.pipeline.replay import restore_request

from .contracts import audit_check


def verify_draft(draft, saved_input, context):
    """Rebind every field; even self-consistent invented bindings are rejected."""
    model_context = saved_input["context"]
    base_context = {k: v for k, v in model_context.items() if k != "revision"}
    if base_context != context or draft["context_hash"] != semantic_hash(model_context):
        raise InvalidCheckpoint(
            "analyst input differs from current deterministic evidence"
        )
    if (
        draft["status"] != "draft"
        or draft["audited"] is not False
        or draft["requires_independent_audit"] is not True
    ):
        raise InvalidCheckpoint("analyst is not an unaudited draft")
    if (
        draft["run_id"] != context["run_id"]
        or draft["checkpoint_hash"] != context["checkpoint_hash"]
        or draft["configuration"] != saved_input["configuration"]
        or draft["attempt_key"] != saved_input["attempt_key"]
    ):
        raise InvalidCheckpoint("analyst envelope binding mismatch")
    expected_fields = {
        "status",
        "run_id",
        "checkpoint_hash",
        "context_hash",
        "configuration",
        "attempt_key",
        "language",
        "data_confidence",
        "financial_snapshot",
        "reconciliations",
        "findings",
        "forecasts",
        "recommendations",
        "missing_data_requests",
        "source_index",
        "requires_independent_audit",
        "audited",
    }
    if (
        set(draft) != expected_fields
        or draft["forecasts"] != []
        or draft["recommendations"] != []
    ):
        raise InvalidCheckpoint("unsupported analyst output fields")
    for key, source_key in (
        ("language", "language"),
        ("data_confidence", "quality"),
        ("financial_snapshot", "evidence"),
        ("reconciliations", "reconciliations"),
        ("missing_data_requests", "missing_data_requests"),
        ("source_index", "sources"),
    ):
        if draft[key] != context[source_key]:
            raise InvalidCheckpoint("analyst snapshot differs from original evidence")
    raw = {
        "schema_version": "1",
        "language": draft["language"],
        "findings": [
            {
                "title": f["title"],
                "statement": f["statement_template"],
                "claim_type": f["claim_type"],
                "capability": f["capability"],
                "evidence_ids": f["evidence_ids"],
            }
            for f in draft["findings"]
        ],
    }
    if bind_findings(raw, context) != draft["findings"]:
        raise InvalidCheckpoint("analyst finding fields, provenance or gates differ")


def statement_checks(run, stages, draft, saved_input, context, *, audit_id):
    """Recompute F-02–F-05 using original parsed rows, policies and timestamp."""
    checks = []
    try:
        request = restore_request(stages["normalization"].payload["request"], run=run)
        if request.fingerprint != stages["checkpoint"].input_hash:
            raise ValueError("restored semantic request differs")
        replay = evaluate(request, run.id, run.started_at)
        for name, payload in zip(STAGES[:-1], replay.payloads):
            if canonicalize(payload) != stages[name].payload:
                raise ValueError("deterministic re-execution differs: " + name)
        if not replay.ready:
            raise ValueError("current deterministic engine blocks this request")
        checks.extend(
            audit_check(
                audit_id,
                family,
                "pass",
                "audit." + family + "_verified",
                refs=(str(run.id),),
            )
            for family in ("reconciliation", "capability")
        )
        verify_draft(draft, saved_input, context)
        checks.extend(
            audit_check(
                audit_id,
                family,
                "pass",
                "audit." + family + "_verified",
                refs=tuple(f["finding_id"] for f in draft["findings"]),
            )
            for family in ("schema", "traceability", "recommendation")
        )
    except (ValueError, TypeError, KeyError, ArithmeticError, InvalidAnalystOutput):
        checks.append(
            audit_check(
                audit_id,
                "statement",
                "block",
                "audit.deterministic_validation_failed",
                refs=(str(run.id),),
                remediation="Restore the original source/trace/gate contracts or create a corrected run.",
            )
        )
    return checks
