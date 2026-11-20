"""Deterministic synthetic F-08 goldens; no proprietary documents or paid calls."""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid5

from audit_fixtures import critic_output, draft_fixture
from pipeline_fixtures import request, with_growth

from saleslab_v2.audit.contracts import assemble_result, bind_critique
from saleslab_v2.audit.statement import statement_checks
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.pipeline.contracts import PIPELINE_VERSION
from saleslab_v2.reports.compiler import compile_report, verify_approval

FIXED_TIME = datetime(2026, 10, 22, 7, tzinfo=UTC)
RUN_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_TIME


def report_fixture(language="pt", *, warning=False, growth=True):
    value = request(language=language)
    from dataclasses import replace

    value = replace(
        value,
        language=replace(
            value.language,
            report_language=type(value.language.report_language)(language),
        ),
    )
    if growth:
        value = with_growth(value)
    with patch("analyst_fixtures.uuid4", return_value=RUN_ID), patch(
        "analyst_fixtures.datetime", FixedDatetime
    ):
        run, stages, context, saved, draft = draft_fixture(value)
    run.pipeline_version = PIPELINE_VERSION
    audit_id = uuid5(TRACE_NAMESPACE, f"audit:{run.id}:initial")
    checks = statement_checks(run, stages, draft, saved, context, audit_id=audit_id)
    raw = critic_output({"draft": draft, "evidence_context": context})
    if warning:
        raw["findings"][0]["checks"][0].update(
            outcome="warning",
            message="Scope remains limited to supplied statements.",
            remediation="Obtain more detailed source data before extending conclusions.",
        )
    semantic, _ = bind_critique(raw, draft, audit_id=audit_id, language=language)
    audit = assemble_result(
        audit_id=audit_id,
        run_id=run.id,
        draft_stage_id=uuid5(TRACE_NAMESPACE, f"analyst:{run.id}:initial"),
        draft_hash=semantic_hash(draft),
        checkpoint_hash=context["checkpoint_hash"],
        checks=checks + semantic,
        finding_ids={f["finding_id"] for f in draft["findings"]},
        configuration={
            "role": "independent_critic",
            "provider": "test",
            "model": "critic-v1",
        },
        audited_at=FIXED_TIME,
        revision_number=0,
    )
    verify_approval(
        audit, draft, context, deterministic_checks=checks, revision_number=0
    )
    report = compile_report(
        run=run,
        context=context,
        draft=draft,
        audit=audit,
        checkpoint=stages["checkpoint"].payload,
        generated_at=FIXED_TIME,
        locale=value.language.format_locale.value,
        render_version="financial-render-v1",
    )
    return report, (run, stages, context, saved, draft, audit, checks)
