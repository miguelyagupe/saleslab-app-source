"""Atomic, scoped close-preparation snapshots. The caller owns the transaction."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select

from models import WorkspaceMember
from saleslab_v2.analyst.persistence import PersistedFinancialEvidence
from saleslab_v2.domain.enums import LanguageCode, ModuleType, StageStatus
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun, StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    CalculationTraceModel,
    MetricObservationModel,
    StageResultModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.contracts import PIPELINE_VERSION

from .contracts import MONTH_END_VERSION, InvalidMonthEnd, MonthEndResult, identifier
from .evaluator import evaluate


async def verify_metrics(session, run_id, workspace_id, calculations):
    rows = (
        await session.scalars(
            select(MetricObservationModel)
            .where(MetricObservationModel.run_id == run_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    traces = (
        await session.scalars(
            select(CalculationTraceModel)
            .where(CalculationTraceModel.run_id == run_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    if {r.id for r in rows} != {o.observation_id for o, _ in calculations} or {
        t.id for t in traces
    } != {o.calculation_trace_id for o, _ in calculations}:
        raise InvalidMonthEnd("close metric/trace set differs")
    indexed, indexed_traces = {r.id: r for r in rows}, {t.id: t for t in traces}
    for observation, trace in calculations:
        row, stored = (
            indexed[observation.observation_id],
            indexed_traces[observation.calculation_trace_id],
        )
        period = observation.period
        expected = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "metric_id": observation.metric_id,
            "metric_definition_version": observation.metric_definition_version,
            "value": observation.value,
            "rounded_value": observation.rounded_value,
            "unit": observation.unit,
            "currency": observation.currency,
            "period": {
                "start": period.start_date.isoformat(),
                "end": period.end_date.isoformat(),
                "granularity": period.granularity,
                "fiscal_year": period.fiscal_year,
                "comparison_basis": period.comparison_basis,
                "fiscal_year_end_month": period.fiscal_year_end_month,
            },
            "scope": observation.scope,
            "dimensions": [list(d) for d in observation.dimensions],
            "source_references": list(observation.source_references),
            "provenance": [list(p) for p in observation.provenance],
            "status": "calculated",
            "calculation_trace_id": observation.calculation_trace_id,
            "calculation_trace_hash": trace.trace_hash,
        }
        expected_trace = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "metric_id": trace.metric_id,
            "metric_definition_version": trace.metric_definition_version,
            "canonical_expression": trace.canonical_expression,
            "inputs": trace.payload["inputs"],
            "exact_output": trace.payload["exact_output"],
            "rounded_output": trace.payload["rounded_output"],
            "rounding_policy": trace.rounding_policy,
            "rounding_version": trace.rounding_version,
            "engine_version": trace.engine_version,
            "trace_hash": trace.trace_hash,
        }
        if any(getattr(row, k) != v for k, v in expected.items()) or any(
            getattr(stored, k) != v for k, v in expected_trace.items()
        ):
            raise InvalidMonthEnd("close calculation content differs")


class FinancialMonthEndCloser:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    async def execute(self, request, *, attempt_key):
        request = request.snapshot()
        identifier(attempt_key)
        settings = self.settings
        if not (
            settings.enabled
            and settings.persist_stages
            and settings.financial_month_end_enabled
            and str(request.workspace_id) in settings.internal_workspaces
        ):
            raise PermissionError(
                "internal financial month-end preparation is disabled"
            )
        async with self.session.begin_nested():
            member = await self.session.scalar(
                select(WorkspaceMember.id).where(
                    WorkspaceMember.workspace_id == request.workspace_id,
                    WorkspaceMember.user_id == request.user_id,
                    WorkspaceMember.role.in_(("owner", "manager")),
                )
            )
            if member is None:
                raise PermissionError("actor is not authorized for close preparation")
            source_run = await self.session.scalar(
                select(AnalysisRunModel)
                .where(
                    AnalysisRunModel.id == request.pipeline_run_id,
                    AnalysisRunModel.workspace_id == request.workspace_id,
                    AnalysisRunModel.pipeline_version == PIPELINE_VERSION,
                )
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
            if source_run is None:
                raise PermissionError("financial pipeline run is outside the workspace")
            checkpoint, stages, _ = await PersistedFinancialEvidence(self.session).load(
                source_run
            )
            refs = (
                [r for p in request.comparisons for r in (p.current, p.prior)]
                + [
                    r
                    for m in request.movements
                    for r in (m.current, m.prior)
                    if r is not None
                ]
                + [r for c in request.confirmations for r in c.evidence]
            )
            if any(str(r.source_id) not in source_run.source_ids for r in refs):
                raise PermissionError("close evidence must belong to the source run")
            saved_language = source_run.language_context
            language = LanguageContext(
                tuple(LanguageCode(v) for v in saved_language["source_languages"]),
                LanguageCode(saved_language["analysis_language"]),
                LanguageCode(saved_language["report_language"]),
                LocaleTag(saved_language["format_locale"]),
                saved_language.get("accounting_context"),
            )
            input_payload = {
                "month_end_version": MONTH_END_VERSION,
                "request": request.payload(),
                "source_stage_hashes": {n: s.output_hash for n, s in stages.items()},
                "checkpoint_hash": semantic_hash(checkpoint),
                "language": language.to_dict(),
            }
            if len(json.dumps(input_payload, ensure_ascii=True).encode()) > 2_000_000:
                raise InvalidMonthEnd("month-end input exceeds bound")
            digest = semantic_hash(input_payload)
            candidate = AnalysisRun(
                request.workspace_id,
                request.user_id,
                ModuleType.FINANCIAL,
                "1",
                MONTH_END_VERSION,
                language,
                tuple(UUID(s) for s in source_run.source_ids),
                (digest,),
                "internal-financial-month-end",
                attempt_key,
            )
            repo = V2Repository(self.session, request.workspace_id, persist_stages=True)
            stored, created = await repo.get_or_create_run(candidate)
            stored = await self.session.scalar(
                select(AnalysisRunModel)
                .where(AnalysisRunModel.id == stored.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
            names = ("financial_month_end_input", "financial_month_end_result")
            ids = {n: uuid5(TRACE_NAMESPACE, f"{n}:{stored.id}") for n in names}
            payload, calculations = evaluate(
                request,
                stages["normalization"].payload,
                checkpoint,
                run_id=stored.id,
                language=language.report_language.value,
            )
            if not created:
                saved = (
                    await self.session.scalars(
                        select(StageResultModel)
                        .where(StageResultModel.run_id == stored.id)
                        .execution_options(populate_existing=True)
                    )
                ).all()
                if (
                    len(saved) != 2
                    or stored.workflow_status != "completed"
                    or stored.completed_at is None
                ):
                    raise InvalidMonthEnd("incomplete close preparation replay")
                by_name = {s.stage_name: s for s in saved}
                for name, expected in zip(names, (input_payload, payload)):
                    stage = by_name.get(name)
                    if (
                        stage is None
                        or stage.id != ids[name]
                        or stage.stage_version != MONTH_END_VERSION
                        or stage.status != "completed"
                        or stage.input_hash != digest
                        or stage.output_hash != semantic_hash(stage.payload)
                        or stage.payload != expected
                    ):
                        raise IdempotencyConflict("close preparation evidence differs")
                await verify_metrics(
                    self.session, stored.id, request.workspace_id, calculations
                )
                return MonthEndResult(stored.id, deepcopy(payload), True)
            for observation, trace in calculations:
                await repo.persist_metric(stored.id, observation, trace)
            now = datetime.now(UTC)
            for name, data in zip(names, (input_payload, payload)):
                await repo.persist_stage(
                    StageResult(
                        stored.id,
                        name,
                        MONTH_END_VERSION,
                        StageStatus.COMPLETED,
                        digest,
                        semantic_hash(data),
                        "1",
                        data,
                        stage_result_id=ids[name],
                        started_at=now,
                        completed_at=now,
                    )
                )
            stored.workflow_status, stored.started_at, stored.completed_at = (
                "completed",
                now,
                now,
            )
            await self.session.flush()
            return MonthEndResult(stored.id, deepcopy(payload), False)
