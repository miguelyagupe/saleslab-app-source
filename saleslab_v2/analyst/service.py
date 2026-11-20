"""Allowlisted F-06 continuation from a persisted F-01–F-05 checkpoint."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select

from models import WorkspaceMember
from saleslab_v2.domain.enums import StageStatus
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.domain.runs import Failure, StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    StageResultModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.contracts import PIPELINE_VERSION

from .context import InvalidCheckpoint, unpack
from .contracts import (
    ANALYST_VERSION,
    SCHEMA_VERSION,
    AnalystResult,
    AnalystUnavailable,
    FinancialModel,
    InvalidAnalystOutput,
    bind_findings,
)
from .persistence import PersistedFinancialEvidence


class FinancialAnalyst(PersistedFinancialEvidence):
    """Caller authenticates actor, owns outer commit, and injects the model."""

    def __init__(self, session, settings, model: FinancialModel):
        self.session, self.settings, self.model = session, settings, model

    async def execute(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        run_id: UUID,
        attempt_key="initial",
        revision_of: UUID | None = None,
    ) -> AnalystResult:
        if any(not isinstance(v, UUID) for v in (workspace_id, user_id, run_id)):
            raise TypeError("workspace, actor and run must be UUIDs")
        if (
            not isinstance(attempt_key, str)
            or not attempt_key.strip()
            or len(attempt_key) > 100
            or attempt_key != attempt_key.strip()
        ):
            raise ValueError("invalid analyst attempt key")
        settings = self.settings
        if revision_of is not None and not isinstance(revision_of, UUID):
            raise TypeError("revision authority must be a persisted audit UUID")
        if not (
            settings.enabled
            and settings.persist_stages
            and settings.financial_analyst_enabled
            and str(workspace_id) in settings.internal_workspaces
        ):
            raise PermissionError(
                "internal financial analyst is disabled for this workspace"
            )
        if revision_of is not None and not settings.financial_audit_enabled:
            raise PermissionError("financial revision requires the audit feature flag")
        configuration = deepcopy(self.model.configuration)
        semantic_hash(configuration)  # Reject unstable/noncanonical configuration.
        async with self.session.begin_nested():
            member = await self.session.scalar(
                select(WorkspaceMember.id).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                    WorkspaceMember.role.in_(("owner", "manager")),
                )
            )
            if member is None:
                raise PermissionError("actor is not an authorized workspace member")
            # Serialize model invocation as well as persistence. A concurrent
            # identical attempt sees the committed stage and never calls twice.
            run = await self.session.scalar(
                select(AnalysisRunModel)
                .where(
                    AnalysisRunModel.id == run_id,
                    AnalysisRunModel.workspace_id == workspace_id,
                )
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
            if run is None:
                raise PermissionError("run is outside workspace scope")
            if (
                run.module != "financial"
                or run.pipeline_version != PIPELINE_VERSION
                or run.idempotency_purpose != "internal-financial-pipeline"
            ):
                raise InvalidCheckpoint(
                    "run is not a deterministic financial checkpoint"
                )
            checkpoint, _, context = await self.load(run)
            if (
                context is None
                or not context["allowed_capabilities"]
                or not context["evidence"]
            ):
                return AnalystResult(
                    run_id,
                    {
                        "status": "blocked",
                        "run_id": str(run_id),
                        "reason_code": (
                            "deterministic_checkpoint_not_ready"
                            if context is None
                            else "no_supported_evidence"
                        ),
                        "quality": unpack(checkpoint["quality"]),
                        "findings": [],
                        "requires_independent_audit": True,
                    },
                    False,
                )
            if revision_of is not None:
                from saleslab_v2.audit.revisions import prepare_revision

                context = deepcopy(context)
                context["revision"] = await prepare_revision(
                    self.session, run, revision_of, attempt_key
                )
                if len(json.dumps(context, ensure_ascii=True).encode()) > 384_000:
                    raise InvalidCheckpoint(
                        "revision context exceeds its bounded input size"
                    )
            input_payload = {
                "context": context,
                "configuration": configuration,
                "attempt_key": attempt_key,
                "analyst_version": ANALYST_VERSION,
            }
            input_hash = semantic_hash(input_payload)
            stage_id = uuid5(TRACE_NAMESPACE, f"analyst:{run_id}:{attempt_key}")
            existing = await self.session.get(
                StageResultModel, stage_id, populate_existing=True
            )
            if existing:
                if existing.run_id != run_id or existing.input_hash != input_hash:
                    raise IdempotencyConflict(
                        "analyst attempt key was used with different inputs/configuration"
                    )
                if existing.output_hash != semantic_hash(
                    existing.payload
                ) or existing.status not in {"completed", "failed"}:
                    raise InvalidCheckpoint("analyst stage integrity mismatch")
                saved_input = await self.session.get(
                    StageResultModel,
                    uuid5(TRACE_NAMESPACE, f"analyst-input:{run_id}:{attempt_key}"),
                    populate_existing=True,
                )
                if (
                    saved_input is None
                    or saved_input.run_id != run_id
                    or saved_input.status != "completed"
                    or saved_input.input_hash != input_hash
                    or saved_input.output_hash != semantic_hash(input_payload)
                    or saved_input.payload != input_payload
                ):
                    raise InvalidCheckpoint(
                        "analyst input audit stage integrity mismatch"
                    )
                if existing.status == "completed" and run.workflow_status not in {
                    "analyzing",
                    "auditing",
                    "compiling",
                    "completed",
                    "blocked",
                    "failed",
                }:
                    raise InvalidCheckpoint("analyst draft has no downstream run state")
                return AnalystResult(run_id, deepcopy(existing.payload), True)
            expected_status = "analyzing" if revision_of is not None else "calculating"
            if run.workflow_status != expected_status:
                raise IdempotencyConflict(
                    "run is not awaiting an analyst; replay its original attempt"
                )
            repository = V2Repository(self.session, workspace_id, persist_stages=True)
            started = datetime.now(UTC)
            run.workflow_status = "analyzing"
            await self.session.flush()
            try:
                if context.get("revision", {}).get("budget_exhausted"):
                    raise InvalidAnalystOutput("revision budget exhausted")
                raw = await self.model.generate(deepcopy(context))
                if self.model.configuration != configuration:
                    raise InvalidAnalystOutput(
                        "model configuration changed during execution"
                    )
                findings = bind_findings(raw, context)
            except (InvalidAnalystOutput, AnalystUnavailable) as exc:
                code = (
                    "analyst.revision_budget_exhausted"
                    if context.get("revision", {}).get("budget_exhausted")
                    else (
                        "analyst.invalid_output"
                        if isinstance(exc, InvalidAnalystOutput)
                        else "analyst.provider_unavailable"
                    )
                )
                payload = {
                    "status": "failed",
                    "run_id": str(run_id),
                    "reason_code": code,
                    "checkpoint_hash": context["checkpoint_hash"],
                    "context_hash": semantic_hash(context),
                    "configuration": configuration,
                    "attempt_key": attempt_key,
                    "findings": [],
                    "requires_independent_audit": True,
                }
                status, error = StageStatus.FAILED, Failure(code, code)
                run.workflow_status = (
                    "blocked"
                    if code == "analyst.revision_budget_exhausted"
                    else expected_status
                )
            else:
                payload = {
                    "status": "draft",
                    "run_id": str(run_id),
                    "checkpoint_hash": context["checkpoint_hash"],
                    "context_hash": semantic_hash(context),
                    "configuration": configuration,
                    "attempt_key": attempt_key,
                    "language": context["language"],
                    "data_confidence": context["quality"],
                    "financial_snapshot": context["evidence"],
                    "reconciliations": context["reconciliations"],
                    "findings": findings,
                    "forecasts": [],
                    "recommendations": [],
                    "missing_data_requests": context["missing_data_requests"],
                    "source_index": context["sources"],
                    "requires_independent_audit": True,
                    "audited": False,
                }
                status, error = StageStatus.COMPLETED, None
                run.workflow_status = "auditing"
            completed = datetime.now(UTC)
            await repository.persist_stage(
                StageResult(
                    run_id,
                    "financial_analyst_input",
                    ANALYST_VERSION,
                    StageStatus.COMPLETED,
                    input_hash,
                    semantic_hash(input_payload),
                    SCHEMA_VERSION,
                    input_payload,
                    stage_result_id=uuid5(
                        TRACE_NAMESPACE, f"analyst-input:{run_id}:{attempt_key}"
                    ),
                    started_at=started,
                    completed_at=started,
                )
            )
            await repository.persist_stage(
                StageResult(
                    run_id,
                    "financial_analyst",
                    ANALYST_VERSION,
                    status,
                    input_hash,
                    semantic_hash(payload),
                    SCHEMA_VERSION,
                    payload,
                    stage_result_id=stage_id,
                    started_at=started,
                    completed_at=completed,
                    error=error,
                )
            )
            await self.session.flush()
            return AnalystResult(run_id, deepcopy(payload), False)
