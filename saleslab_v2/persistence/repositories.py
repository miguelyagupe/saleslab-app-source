import json
from decimal import Decimal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from saleslab_v2.domain.enums import Capability, Severity
from saleslab_v2.domain.quality import DataQualityIssue
from saleslab_v2.domain.idempotency import IdempotencyConflict, canonicalize
from saleslab_v2.domain.runs import AnalysisRun, Failure, StageResult
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.metrics.engine import MetricObservation
from saleslab_v2.metrics.trace import CalculationTrace
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.quality.capabilities import decide_capabilities
from saleslab_v2.quality.coverage import validate_coverage
from saleslab_v2.quality.issues import canonical_issue_payload, scoped_issue_id, validate_issue_identities
from saleslab_v2.reconciliation.contracts import (
    CapabilityEvidence,
    ReconciliationInput,
    ReconciliationResult,
    TolerancePolicy,
)
from saleslab_v2.reconciliation.engine import ReconciliationEngine, _RANK
from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1


QUALITY_ISSUE_NAMESPACE = uuid5(TRACE_NAMESPACE, "quality-issue")


def _quality_issue_id(workspace_id: UUID, run_id: UUID, issue) -> UUID:
    """Derive a replay-stable issue identity scoped to one quality-gate run."""
    return scoped_issue_id(workspace_id, run_id, issue)

from .models import (
    AnalysisRunModel,
    CalculationTraceModel,
    MetricObservationModel,
    ReconciliationResultModel,
    DataQualityIssueModel,
    CoverageAssessmentModel,
    CapabilityDecisionModel,
    SourceDocumentModel,
    StageResultModel,
)


def failure_json(failure: Failure | None) -> dict | None:
    if failure is None:
        return None
    return {
        "code": failure.code,
        "message": failure.message,
        "details": failure.details,
    }


class V2Repository:
    def __init__(
        self, session: AsyncSession, workspace_id: UUID, *, persist_stages: bool = False
    ):
        if not workspace_id:
            raise ValueError("workspace scope is required")
        self.session, self.workspace_id, self.persist_stages = (
            session,
            workspace_id,
            persist_stages,
        )

    async def get_run(self, run_id: UUID) -> AnalysisRunModel | None:
        return await self.session.scalar(
            select(AnalysisRunModel).where(
                AnalysisRunModel.id == run_id,
                AnalysisRunModel.workspace_id == self.workspace_id,
            )
        )

    def _run_values(self, run: AnalysisRun) -> dict:
        return {
            "id": run.run_id,
            "workspace_id": run.workspace_id,
            "created_by_user_id": run.created_by_user_id,
            "module": run.module.value,
            "workflow_status": run.status.value,
            "schema_version": run.schema_version,
            "pipeline_version": run.pipeline_version,
            "language_context": run.language_context.to_dict(),
            "source_ids": [str(item) for item in run.source_ids],
            "input_hashes": list(run.input_hashes),
            "idempotency_purpose": run.idempotency_purpose,
            "idempotency_key": run.idempotency_key,
            "request_fingerprint": run.request_fingerprint,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "failure": failure_json(run.failure),
            "legacy_comparison_id": run.legacy_comparison_id,
        }

    async def _validate_run_sources(self, run: AnalysisRun) -> None:
        if len(set(run.source_ids)) != len(run.source_ids):
            raise ValueError("source IDs must not contain duplicates")
        if not run.source_ids:
            if not run.allow_empty_sources:
                raise ValueError("source-less runs require explicit internal opt-in")
            return
        found = set(
            (
                await self.session.scalars(
                    select(SourceDocumentModel.id).where(
                        SourceDocumentModel.workspace_id == self.workspace_id,
                        SourceDocumentModel.id.in_(run.source_ids),
                    )
                )
            ).all()
        )
        if found != set(run.source_ids):
            raise PermissionError(
                "all run sources must exist in the repository workspace"
            )

    async def create_run(self, run: AnalysisRun) -> AnalysisRunModel:
        if run.workspace_id != self.workspace_id:
            raise PermissionError("cross-workspace run denied")
        await self._validate_run_sources(run)
        model = AnalysisRunModel(**self._run_values(run))
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_or_create_run(
        self, run: AnalysisRun
    ) -> tuple[AnalysisRunModel, bool]:
        """Atomically insert under PostgreSQL's scoped unique constraint."""
        if run.workspace_id != self.workspace_id:
            raise PermissionError("cross-workspace run denied")
        await self._validate_run_sources(run)
        statement = (
            insert(AnalysisRunModel)
            .values(**self._run_values(run))
            .on_conflict_do_nothing(
                constraint="uq_analysis_runs_v2_workspace_purpose_key"
            )
            .returning(AnalysisRunModel.id)
        )
        inserted_id = await self.session.scalar(statement)
        existing = await self.session.scalar(
            select(AnalysisRunModel).where(
                AnalysisRunModel.workspace_id == self.workspace_id,
                AnalysisRunModel.idempotency_purpose == run.idempotency_purpose,
                AnalysisRunModel.idempotency_key == run.idempotency_key,
            )
        )
        if existing is None:
            raise RuntimeError("atomic idempotency insert could not be read back")
        if existing.request_fingerprint != run.request_fingerprint:
            raise IdempotencyConflict("idempotency key was used with different inputs")
        return existing, inserted_id is not None

    async def get_source(self, source_id: UUID) -> SourceDocumentModel | None:
        return await self.session.scalar(
            select(SourceDocumentModel).where(
                SourceDocumentModel.id == source_id,
                SourceDocumentModel.workspace_id == self.workspace_id,
            )
        )

    async def create_source(self, source: SourceDocument) -> SourceDocumentModel:
        if source.workspace_id != self.workspace_id:
            raise PermissionError("cross-workspace source denied")
        if source.supersedes_source_id == source.source_id:
            raise ValueError("a source cannot supersede itself")
        if (
            source.supersedes_source_id is not None
            and await self.get_source(source.supersedes_source_id) is None
        ):
            raise PermissionError(
                "superseded source must exist in the repository workspace"
            )
        model = SourceDocumentModel(
            id=source.source_id,
            workspace_id=source.workspace_id,
            filename=source.filename,
            source_format=source.source_format.value,
            content_hash=source.content_hash,
            parser_version=source.parser_version,
            trust_level=source.trust_level.value,
            language_metadata=[item.value for item in source.languages],
            uploaded_at=source.uploaded_at,
            created_at=source.created_at,
            supersedes_source_id=source.supersedes_source_id,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def persist_stage(self, stage: StageResult) -> StageResultModel | None:
        if not self.persist_stages:
            return None
        if await self.get_run(stage.run_id) is None:
            raise PermissionError("run is outside workspace scope")
        existing = await self.session.scalar(
            select(StageResultModel).where(
                StageResultModel.run_id == stage.run_id,
                StageResultModel.stage_name == stage.stage_name,
                StageResultModel.stage_version == stage.stage_version,
                StageResultModel.input_hash == stage.input_hash,
            )
        )
        expected_error = failure_json(stage.error)
        expected_payload = canonicalize(stage.payload)
        if existing:
            if any(
                (
                    existing.status != stage.status.value,
                    existing.output_hash != stage.output_hash,
                    existing.output_schema_version != stage.output_schema_version,
                    existing.payload != expected_payload,
                    existing.started_at != stage.started_at,
                    existing.completed_at != stage.completed_at,
                    existing.error != expected_error,
                )
            ):
                raise IdempotencyConflict("stage replay produced incompatible output")
            return existing
        model = StageResultModel(
            id=stage.stage_result_id,
            run_id=stage.run_id,
            stage_name=stage.stage_name,
            stage_version=stage.stage_version,
            status=stage.status.value,
            input_hash=stage.input_hash,
            output_hash=stage.output_hash,
            output_schema_version=stage.output_schema_version,
            payload=expected_payload,
            started_at=stage.started_at,
            completed_at=stage.completed_at,
            error=expected_error,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def persist_metric(
        self, run_id: UUID, observation: MetricObservation, trace: CalculationTrace
    ) -> tuple[MetricObservationModel, bool]:
        """Persist/replay a trace and observation within the repository workspace."""
        if await self.get_run(run_id) is None:
            raise PermissionError("run is outside workspace scope")
        refs = tuple(
            sorted({ref for item in trace.inputs for ref in item.source_references})
        )
        provenance = tuple(
            sorted({pair for item in trace.inputs for pair in item.provenance})
        )
        expected = {
            "metric_id": trace.metric_id,
            "metric_definition_version": trace.metric_definition_version,
            "value": trace.exact_output,
            "rounded_value": trace.rounded_output,
            "unit": trace.output_unit,
            "currency": trace.output_currency,
            "period": trace.period,
            "scope": trace.scope,
            "dimensions": trace.dimensions,
            "source_references": refs,
            "provenance": provenance,
            "status": "calculated",
            "calculation_trace_hash": trace.trace_hash,
        }
        actual = {key: getattr(observation, key) for key in expected}
        actual["period"] = observation.period.identity
        if actual != expected:
            raise IdempotencyConflict("observation and calculation trace do not match")
        scoped_trace_id = uuid5(
            TRACE_NAMESPACE, f"{self.workspace_id}:{run_id}:{trace.trace_hash}"
        )
        scoped_observation_id = uuid5(
            TRACE_NAMESPACE,
            f"observation:{self.workspace_id}:{run_id}:{trace.trace_hash}",
        )

        def normalize_json(value):
            return json.loads(json.dumps(value))

        trace_values = dict(
            id=scoped_trace_id,
            workspace_id=self.workspace_id,
            run_id=run_id,
            metric_id=trace.metric_id,
            metric_definition_version=trace.metric_definition_version,
            canonical_expression=trace.canonical_expression,
            inputs=normalize_json(trace.payload["inputs"]),
            exact_output=trace.payload["exact_output"],
            rounded_output=trace.payload["rounded_output"],
            rounding_policy=trace.rounding_policy,
            rounding_version=trace.rounding_version,
            engine_version=trace.engine_version,
            trace_hash=trace.trace_hash,
        )
        inserted = await self.session.scalar(
            insert(CalculationTraceModel)
            .values(**trace_values)
            .on_conflict_do_nothing(constraint="uq_calculation_traces_v2_scope_hash")
            .returning(CalculationTraceModel.id)
        )
        stored_trace = await self.session.scalar(
            select(CalculationTraceModel).where(
                CalculationTraceModel.workspace_id == self.workspace_id,
                CalculationTraceModel.run_id == run_id,
                CalculationTraceModel.trace_hash == trace.trace_hash,
            )
        )
        if (
            stored_trace is None
            or stored_trace.id != scoped_trace_id
            or stored_trace.run_id != run_id
        ):
            raise IdempotencyConflict("trace replay conflicts with persisted context")
        period = observation.period
        values = dict(
            id=scoped_observation_id,
            workspace_id=self.workspace_id,
            run_id=run_id,
            metric_id=observation.metric_id,
            metric_definition_version=observation.metric_definition_version,
            value=observation.value,
            rounded_value=observation.rounded_value,
            unit=observation.unit,
            currency=observation.currency,
            period={
                "start": period.start_date.isoformat(),
                "end": period.end_date.isoformat(),
                "granularity": period.granularity,
                "fiscal_year": period.fiscal_year,
                "comparison_basis": period.comparison_basis,
                "fiscal_year_end_month": period.fiscal_year_end_month,
            },
            scope=observation.scope,
            dimensions=normalize_json(observation.dimensions),
            provenance=normalize_json(observation.provenance),
            source_references=normalize_json(observation.source_references),
            calculation_trace_id=scoped_trace_id,
            calculation_trace_hash=trace.trace_hash,
            status=observation.status,
        )
        await self.session.execute(
            insert(MetricObservationModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_metric_observations_v2_scope_trace")
        )
        stored = await self.session.scalar(
            select(MetricObservationModel).where(
                MetricObservationModel.workspace_id == self.workspace_id,
                MetricObservationModel.run_id == run_id,
                MetricObservationModel.calculation_trace_id == scoped_trace_id,
            )
        )
        if stored is None or any(
            getattr(stored, key) != value
            for key, value in values.items()
            if key != "created_at"
        ):
            raise IdempotencyConflict("metric replay produced incompatible output")
        return stored, inserted is not None

    async def persist_quality_gate(self, coverage, issues, decisions):
        """Persist or validate an F-05 gate inside an atomic savepoint.

        Callers may catch IdempotencyConflict inside their outer transaction.
        The savepoint ensures that an incompatible replay cannot leave issues,
        coverage, or decisions partially persisted.
        """
        async with self.session.begin_nested():
            return await self._persist_quality_gate(coverage, issues, decisions)

    async def _persist_quality_gate(self, coverage, issues, decisions):
        # Freeze iterables once: validation and writes must see the same gate.
        issues, decisions = tuple(issues), tuple(decisions)
        # Issues belong to the run, not to one profile. Serialize all gate
        # writers for a run so another policy cannot race past its blockers.
        # NO KEY UPDATE stays compatible with earlier stage/FK key-share locks.
        run = await self.session.scalar(
            select(AnalysisRunModel).where(
                AnalysisRunModel.id == coverage.run_id,
                AnalysisRunModel.workspace_id == self.workspace_id,
            ).with_for_update(key_share=True)
        )
        if run is None:
            raise PermissionError("quality gate run is outside workspace")
        if run.module != coverage.module.value:
            raise ValueError("quality gate module does not match run module")
        if any(decision.run_id != coverage.run_id for decision in decisions):
            raise PermissionError("quality gate decision run mismatch")
        coverage = validate_coverage(coverage)
        validate_issue_identities(issues)
        capabilities = tuple(decision.capability for decision in decisions)
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("quality gate decisions contain duplicate capabilities")
        if set(capabilities) != set(Capability):
            raise ValueError(
                "quality gate persistence requires the complete capability matrix"
            )

        def incompatible(stored, values, ignored=("id", "created_at", "assessed_at")):
            return stored is None or any(
                getattr(stored, key) != value
                for key, value in values.items()
                if key not in ignored
            )

        persisted_issues = (
            await self.session.scalars(
                select(DataQualityIssueModel).where(
                    DataQualityIssueModel.workspace_id == self.workspace_id,
                    DataQualityIssueModel.run_id == coverage.run_id,
                )
            )
        ).all()
        stored_issues = tuple(
            DataQualityIssue(
                code=row.code, severity=Severity(row.severity), category=row.category,
                message_key=row.message_key, affected_refs=tuple(row.affected_refs),
                expected=row.expected, observed=row.observed,
                remediation_key=row.remediation_key,
                blocking_capabilities=tuple(Capability(c) for c in row.blocking_capabilities),
                issue_id=row.id,
            )
            for row in persisted_issues
        )
        issues = (*issues, *stored_issues)
        validate_issue_identities(issues)
        issue_ids = {
            issue.issue_id: _quality_issue_id(
                self.workspace_id, coverage.run_id, issue
            )
            for issue in issues
        }
        expected_decisions = decide_capabilities(
            coverage.run_id,
            coverage,
            issues=tuple(issues),
            capabilities=capabilities,
        )

        def decision_semantics(decision):
            try:
                supporting_ids = sorted(
                    {str(issue_ids[issue_id]) for issue_id in decision.supporting_issue_ids}
                )
            except KeyError as exc:
                raise ValueError(
                    "capability decision references an issue outside the supplied gate"
                ) from exc
            return (
                decision.capability.value,
                decision.decision_status.value,
                tuple(decision.reason_codes),
                tuple(supporting_ids),
                tuple(decision.requirements_to_unlock),
                decision.rule_version,
            )

        supplied_by_capability = {
            decision.capability: decision_semantics(decision)
            for decision in decisions
        }
        expected_by_capability = {
            decision.capability: decision_semantics(decision)
            for decision in expected_decisions
        }
        if supplied_by_capability != expected_by_capability:
            raise ValueError(
                "quality gate decisions do not match supplied coverage and issues"
            )

        # A new profile may not leave older immutable decisions stale when it
        # contributes a run-scoped blocker. Blocking precedence is independent
        # of profile; compare its canonical evidence, reasons and remediation.
        stored_decisions = (
            await self.session.scalars(
                select(CapabilityDecisionModel).where(
                    CapabilityDecisionModel.workspace_id == self.workspace_id,
                    CapabilityDecisionModel.run_id == coverage.run_id,
                )
            )
        ).all()
        for stored in stored_decisions:
            expected = expected_by_capability[Capability(stored.capability)]
            if expected[2] != ("blocking_issue_precedence",):
                continue
            actual = (
                stored.capability, stored.decision_status, tuple(stored.reason_codes),
                tuple(sorted(set(stored.supporting_issue_ids))),
                tuple(stored.requirements_to_unlock),
            )
            if actual != expected[:-1]:
                raise IdempotencyConflict(
                    "run-scoped issues conflict with a persisted policy gate"
                )

        # Treat issues as a logical set. Deduplicate semantic duplicates and
        # acquire PostgreSQL uniqueness locks in stable-ID order so concurrent
        # replays cannot deadlock by presenting the same issues differently.
        canonical_issues = {}
        for issue in issues:
            canonical_issues.setdefault(issue_ids[issue.issue_id], issue)
        for stable_issue_id, issue in sorted(
            canonical_issues.items(), key=lambda item: str(item[0])
        ):
            values = dict(
                id=stable_issue_id,
                workspace_id=self.workspace_id,
                run_id=coverage.run_id,
                **canonical_issue_payload(issue),
            )
            await self.session.execute(
                insert(DataQualityIssueModel)
                .values(**values)
                .on_conflict_do_nothing()
            )
            stored = await self.session.get(DataQualityIssueModel, stable_issue_id)
            if incompatible(stored, values):
                raise IdempotencyConflict(
                    "quality issue replay produced incompatible output"
                )

        coverage_values = dict(
            id=coverage.assessment_id,
            workspace_id=self.workspace_id,
            run_id=coverage.run_id,
            module=coverage.module.value,
            required_inputs=list(coverage.required_inputs),
            present_inputs=list(coverage.present_inputs),
            missing_inputs=list(coverage.missing_inputs),
            ambiguous_inputs=list(coverage.ambiguous_inputs),
            quality_level=coverage.quality_level.value,
            score_components=dict(coverage.score_components),
            assessment_rule_version=coverage.assessment_rule_version,
            assessed_at=coverage.assessed_at,
        )
        inserted = await self.session.scalar(
            insert(CoverageAssessmentModel)
            .values(**coverage_values)
            .on_conflict_do_nothing()
            .returning(CoverageAssessmentModel.id)
        )
        stored_coverage = await self.session.scalar(
            select(CoverageAssessmentModel).where(
                CoverageAssessmentModel.workspace_id == self.workspace_id,
                CoverageAssessmentModel.run_id == coverage.run_id,
                CoverageAssessmentModel.module == coverage.module.value,
                CoverageAssessmentModel.assessment_rule_version
                == coverage.assessment_rule_version,
            )
        )
        if incompatible(stored_coverage, coverage_values):
            raise IdempotencyConflict(
                "coverage replay produced incompatible assessment"
            )

        stored_decisions = []
        for decision in decisions:
            values = dict(
                id=decision.decision_id,
                workspace_id=self.workspace_id,
                run_id=decision.run_id,
                capability=decision.capability.value,
                decision_status=decision.decision_status.value,
                reason_codes=list(decision.reason_codes),
                supporting_issue_ids=sorted(
                    {
                        str(issue_ids[issue_id])
                        for issue_id in decision.supporting_issue_ids
                    }
                ),
                requirements_to_unlock=list(decision.requirements_to_unlock),
                rule_version=decision.rule_version,
            )
            await self.session.execute(
                insert(CapabilityDecisionModel)
                .values(**values)
                .on_conflict_do_nothing()
            )
            stored = await self.session.scalar(
                select(CapabilityDecisionModel).where(
                    CapabilityDecisionModel.workspace_id == self.workspace_id,
                    CapabilityDecisionModel.run_id == decision.run_id,
                    CapabilityDecisionModel.capability == decision.capability.value,
                    CapabilityDecisionModel.rule_version == decision.rule_version,
                )
            )
            if incompatible(stored, values):
                raise IdempotencyConflict(
                    "capability replay produced incompatible decision"
                )
            stored_decisions.append(stored)
        return stored_coverage, tuple(stored_decisions), inserted is not None


    async def persist_reconciliation(
        self, run_id: UUID, result: ReconciliationResult
    ) -> tuple[ReconciliationResultModel, bool]:
        """Atomically persist a fully validated result, or compare its replay."""
        if await self.get_run(run_id) is None:
            raise PermissionError("run is outside workspace scope")
        # Treat the exported dataclass as untrusted input at the persistence
        # boundary.  This validation happens before ON CONFLICT as well as replay.
        if result.input_fingerprint != ReconciliationEngine.input_fingerprint(result):
            raise ValueError("invalid reconciliation input fingerprint")
        if result.result_hash != ReconciliationEngine.semantic_hash(result):
            raise ValueError("invalid reconciliation result hash")
        policy = TolerancePolicy(
            **{
                key: (
                    Decimal(value)
                    if key.endswith(("_absolute", "_relative"))
                    else value
                )
                for key, value in result.policy_payload.items()
            }
        )
        if policy.fingerprint != result.policy_fingerprint:
            raise ValueError("invalid reconciliation policy fingerprint")
        if not result.equations:
            raise ValueError("reconciliation equations are required")
        worst = max(
            result.equations,
            key=lambda e: (_RANK[e.status], e.absolute_delta, e.equation_id),
        )
        if any(
            e.signed_delta != e.observed - e.expected
            or e.absolute_delta != abs(e.signed_delta)
            for e in result.equations
        ):
            raise ValueError("invalid reconciliation equation arithmetic")
        if (
            result.status,
            result.severity,
            result.expected,
            result.observed,
            result.signed_delta,
            result.absolute_delta,
        ) != (
            worst.status,
            worst.severity,
            worst.expected,
            worst.observed,
            worst.signed_delta,
            worst.absolute_delta,
        ):
            raise ValueError("invalid aggregate reconciliation result")
        reconstructed = tuple(
            ReconciliationInput(
                role=ref["role"],
                observation_id=UUID(ref["observation_id"]),
                identity=ref["identity"],
                value=Decimal(ref["value"]),
                unit=ref["unit"],
                currency=ref["currency"],
                scale=ref["scale"],
                period=tuple(ref["period"]),
                scope=ref["scope"],
                dimensions=tuple(tuple(item) for item in ref["dimensions"]),
                status=ref["status"],
                source_references=tuple(ref["source_references"]),
                provenance=tuple(tuple(item) for item in ref["provenance"]),
                variant=ref["variant"],
                calculation_trace_id=(
                    UUID(ref["calculation_trace_id"])
                    if ref["calculation_trace_id"]
                    else None
                ),
                calculation_trace_hash=ref["calculation_trace_hash"],
            )
            for ref in result.input_references
        )
        rebuilt = ReconciliationEngine().evaluate(
            FINANCIAL_RULES_V1.get(result.rule_id, result.rule_version),
            reconstructed,
            policy,
            timestamp=result.created_at,
            capability_evidence=tuple(
                CapabilityEvidence(**item.__dict__)
                for item in result.capability_evidence
            ),
            workspace_id=self.workspace_id,
            run_id=run_id,
        )
        if rebuilt.payload() != result.payload():
            raise ValueError(
                "reconciliation result does not match canonical evaluation"
            )
        trace_ids = {UUID(value) for value in result.calculation_trace_references}
        if trace_ids:
            found = set(
                (
                    await self.session.scalars(
                        select(CalculationTraceModel.id).where(
                            CalculationTraceModel.workspace_id == self.workspace_id,
                            CalculationTraceModel.run_id == run_id,
                            CalculationTraceModel.id.in_(trace_ids),
                        )
                    )
                ).all()
            )
            if found != trace_ids:
                raise PermissionError(
                    "calculation references are outside workspace/run"
                )
        evidence_trace_ids = {
            UUID(value)
            for evidence in result.capability_evidence
            for value in evidence.evidence_references
        }
        if evidence_trace_ids:
            found = set(
                (
                    await self.session.scalars(
                        select(CalculationTraceModel.id).where(
                            CalculationTraceModel.workspace_id == self.workspace_id,
                            CalculationTraceModel.run_id == run_id,
                            CalculationTraceModel.id.in_(evidence_trace_ids),
                        )
                    )
                ).all()
            )
            if found != evidence_trace_ids:
                raise PermissionError(
                    "capability evidence references are outside workspace/run"
                )
        identifier = uuid5(
            TRACE_NAMESPACE,
            f"reconciliation:{self.workspace_id}:{run_id}:{result.rule_id}:{result.rule_version}",
        )
        payload = canonicalize(result.payload())
        values = dict(
            id=identifier,
            workspace_id=self.workspace_id,
            run_id=run_id,
            rule_id=result.rule_id,
            rule_version=result.rule_version,
            policy_id=result.policy_id,
            policy_version=result.policy_version,
            policy_fingerprint=result.policy_fingerprint,
            input_fingerprint=result.input_fingerprint,
            result_hash=result.result_hash,
            status=result.status,
            result=payload,
            created_at=result.created_at,
        )
        inserted = await self.session.scalar(
            insert(ReconciliationResultModel)
            .values(**values)
            # The deterministic primary key and the scoped execution unique can
            # race independently; either conflict represents the same replay
            # candidate and is compared in full below.
            .on_conflict_do_nothing()
            .returning(ReconciliationResultModel.id)
        )
        stored = await self.session.scalar(
            select(ReconciliationResultModel).where(
                ReconciliationResultModel.workspace_id == self.workspace_id,
                ReconciliationResultModel.run_id == run_id,
                ReconciliationResultModel.rule_id == result.rule_id,
                ReconciliationResultModel.rule_version == result.rule_version,
            )
        )
        if stored is None or any(
            getattr(stored, key) != value
            for key, value in values.items()
            if key != "created_at"
        ):
            raise IdempotencyConflict(
                "reconciliation replay produced incompatible result"
            )
        return stored, inserted is not None
