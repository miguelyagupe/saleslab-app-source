"""Shared read-only verification of persisted workspace/run financial evidence."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.persistence.models import (
    CalculationTraceModel,
    CapabilityDecisionModel,
    CoverageAssessmentModel,
    DataQualityIssueModel,
    MetricObservationModel,
    ReconciliationResultModel,
    SourceDocumentModel,
    StageResultModel,
)
from saleslab_v2.pipeline.contracts import PIPELINE_VERSION, STAGES

from .context import InvalidCheckpoint, build_context, unpack, verify_checkpoint


class PersistedFinancialEvidence:
    def __init__(self, session):
        self.session = session

    async def _scoped(self, model, run):
        return list(
            (
                await self.session.scalars(
                    select(model)
                    .where(
                        model.workspace_id == run.workspace_id,
                        model.run_id == run.id,
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def _verify_records(self, run, checkpoint, stages):
        """Check live scoped tables too: a stale stage cannot hide gate changes."""
        quality = unpack(checkpoint["quality"])
        gates = {str(d.id): d for d in await self._scoped(CapabilityDecisionModel, run)}
        if len(gates) != len(quality["decisions"]):
            raise InvalidCheckpoint("persisted capability matrix differs")
        for d in quality["decisions"]:
            stored = gates.get(d["decision_id"])
            if stored is None or any(
                getattr(stored, key) != d[key]
                for key in (
                    "capability",
                    "decision_status",
                    "reason_codes",
                    "supporting_issue_ids",
                    "requirements_to_unlock",
                    "rule_version",
                )
            ):
                raise InvalidCheckpoint("persisted capability decision differs")
        coverage = await self._scoped(CoverageAssessmentModel, run)
        expected = quality["coverage"]
        if (
            len(coverage) != 1
            or str(coverage[0].id) != expected["assessment_id"]
            or any(
                getattr(coverage[0], key) != expected[key]
                for key in (
                    "module",
                    "required_inputs",
                    "present_inputs",
                    "missing_inputs",
                    "ambiguous_inputs",
                    "quality_level",
                    "score_components",
                    "assessment_rule_version",
                )
            )
        ):
            raise InvalidCheckpoint("persisted coverage differs")
        issues = {str(i.id): i for i in await self._scoped(DataQualityIssueModel, run)}
        if set(issues) != {i["issue_id"] for i in quality["issues"]}:
            raise InvalidCheckpoint("persisted issues differ")
        for issue in quality["issues"]:
            if any(
                getattr(issues[issue["issue_id"]], key) != issue[key]
                for key in (
                    "code",
                    "severity",
                    "category",
                    "message_key",
                    "affected_refs",
                    "expected",
                    "observed",
                    "remediation_key",
                    "blocking_capabilities",
                )
            ):
                raise InvalidCheckpoint("persisted issue content differs")
        recs = await self._scoped(ReconciliationResultModel, run)
        if sorted(semantic_hash(r.result) for r in recs) != sorted(
            semantic_hash(r) for r in checkpoint["reconciliations"]
        ):
            raise InvalidCheckpoint("persisted reconciliation differs")
        if any(
            getattr(r, key) != r.result[key]
            for r in recs
            for key in (
                "rule_id",
                "rule_version",
                "policy_id",
                "policy_version",
                "policy_fingerprint",
                "input_fingerprint",
                "result_hash",
                "status",
            )
        ):
            raise InvalidCheckpoint("persisted reconciliation status/identity differs")
        metrics = {
            str(m.id): m for m in await self._scoped(MetricObservationModel, run)
        }
        traces = {str(t.id): t for t in await self._scoped(CalculationTraceModel, run)}
        expected_ids = {
            unpack(m["observation"]["observation_id"]) for m in checkpoint["metrics"]
        }
        if set(metrics) != expected_ids or len(traces) != len(metrics):
            raise InvalidCheckpoint("persisted calculations differ")
        for metric in unpack(checkpoint["metrics"]):
            observation, trace = metric["observation"], metric["trace"]
            stored = metrics.get(observation["observation_id"])
            stored_trace = traces.get(observation["calculation_trace_id"])
            if stored is None or stored_trace is None:
                raise InvalidCheckpoint("calculation belongs to another run")
            period = observation["period"]
            expected_period = {
                "start": period["start_date"],
                "end": period["end_date"],
                **{
                    k: period[k]
                    for k in (
                        "granularity",
                        "fiscal_year",
                        "comparison_basis",
                        "fiscal_year_end_month",
                    )
                },
            }
            values = {
                k: observation[k]
                for k in (
                    "metric_id",
                    "metric_definition_version",
                    "unit",
                    "currency",
                    "scope",
                    "dimensions",
                    "provenance",
                    "source_references",
                    "calculation_trace_hash",
                    "status",
                )
            }
            values.update(
                value=Decimal(observation["value"]),
                rounded_value=(
                    Decimal(observation["rounded_value"])
                    if observation["rounded_value"] is not None
                    else None
                ),
                period=expected_period,
                calculation_trace_id=UUID(observation["calculation_trace_id"]),
            )
            if (
                any(getattr(stored, k) != v for k, v in values.items())
                or any(
                    getattr(stored_trace, k) != trace[k]
                    for k in (
                        "metric_id",
                        "metric_definition_version",
                        "canonical_expression",
                        "inputs",
                        "exact_output",
                        "rounded_output",
                        "rounding_policy",
                        "rounding_version",
                        "engine_version",
                    )
                )
                or stored_trace.trace_hash != observation["calculation_trace_hash"]
            ):
                raise InvalidCheckpoint("persisted calculation content differs")
        sources = list(
            (
                await self.session.scalars(
                    select(SourceDocumentModel)
                    .where(
                        SourceDocumentModel.workspace_id == run.workspace_id,
                        SourceDocumentModel.id.in_([UUID(s) for s in run.source_ids]),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        documents = {
            unpack(s["document"]["source_id"]): unpack(s["document"])
            for s in stages["normalization"].payload["request"]["sources"]
        }
        if {str(s.id) for s in sources} != set(documents):
            raise InvalidCheckpoint("persisted sources differ")
        for source in sources:
            expected = documents[str(source.id)]
            if (
                any(
                    getattr(source, k) != expected[k]
                    for k in (
                        "filename",
                        "content_hash",
                        "parser_version",
                        "trust_level",
                        "source_format",
                    )
                )
                or source.language_metadata != expected["languages"]
            ):
                raise InvalidCheckpoint("persisted source metadata differs")
        return sources

    async def load(self, run):
        rows = list(
            (
                await self.session.scalars(
                    select(StageResultModel)
                    .where(
                        StageResultModel.run_id == run.id,
                        StageResultModel.stage_version == PIPELINE_VERSION,
                        StageResultModel.stage_name.in_(STAGES),
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if len(rows) != len(STAGES):
            raise InvalidCheckpoint("pipeline stage count differs")
        stages = {s.stage_name: s for s in rows}
        checkpoint = verify_checkpoint(run, stages)
        sources = await self._verify_records(run, checkpoint, stages)
        context = (
            build_context(run, checkpoint, stages, sources)
            if checkpoint["ready"]
            else None
        )
        return checkpoint, stages, context
