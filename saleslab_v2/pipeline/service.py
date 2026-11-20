"""Atomic, allowlisted F-01 → F-05 orchestration. Caller owns the transaction."""

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from models import WorkspaceMember
from saleslab_v2.domain.enums import ModuleType, StageStatus, WorkflowStatus
from saleslab_v2.domain.idempotency import (
    IdempotencyConflict,
    canonicalize,
    semantic_hash,
)
from saleslab_v2.domain.runs import AnalysisRun, StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import SourceDocumentModel, StageResultModel
from saleslab_v2.persistence.repositories import V2Repository

from .contracts import FinancialRequest, PIPELINE_VERSION, STAGES
from .evaluator import evaluate


@dataclass(frozen=True)
class PipelineResult:
    run_id: UUID
    payload: dict
    replayed: bool

    @property
    def ready(self):
        return self.payload["ready"]


async def _ensure_source(repository, source):
    if source.supersedes_source_id == source.source_id:
        raise ValueError("a source cannot supersede itself")
    if (
        source.supersedes_source_id
        and await repository.get_source(source.supersedes_source_id) is None
    ):
        raise PermissionError("superseded source must exist in the workspace")
    values = {
        "id": source.source_id,
        "workspace_id": source.workspace_id,
        "filename": source.filename,
        "source_format": source.source_format.value,
        "content_hash": source.content_hash,
        "parser_version": source.parser_version,
        "trust_level": source.trust_level.value,
        "language_metadata": [x.value for x in source.languages],
        "supersedes_source_id": source.supersedes_source_id,
    }
    await repository.session.execute(
        insert(SourceDocumentModel)
        .values(**values, uploaded_at=source.uploaded_at, created_at=source.created_at)
        .on_conflict_do_nothing()
    )
    stored = await repository.get_source(source.source_id)
    if stored is None:
        raise PermissionError("source ID belongs to another workspace")
    if any(getattr(stored, name) != value for name, value in values.items()):
        raise IdempotencyConflict("source metadata is immutable")


class FinancialPipeline:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    async def execute(self, request: FinancialRequest) -> PipelineResult:
        # Snapshot caller-owned transport data before the first await.
        request = request.snapshot()
        if not (
            self.settings.enabled
            and self.settings.persist_stages
            and str(request.workspace_id) in self.settings.internal_workspaces
        ):
            raise PermissionError("internal V2 pipeline is disabled for this workspace")
        async with self.session.begin_nested():
            member = await self.session.scalar(
                select(WorkspaceMember.id).where(
                    WorkspaceMember.workspace_id == request.workspace_id,
                    WorkspaceMember.user_id == request.user_id,
                    WorkspaceMember.role.in_(("owner", "manager")),
                )
            )
            if member is None:
                raise PermissionError("actor is not a workspace member")
            repository = V2Repository(
                self.session, request.workspace_id, persist_stages=True
            )
            # Supersession is relative to previously registered evidence, never
            # to whichever source happens to sort first in the current batch.
            for batch in request.sources:
                previous = batch.document.supersedes_source_id
                if previous and await repository.get_source(previous) is None:
                    raise PermissionError(
                        "superseded source must already exist in the workspace"
                    )
            # Consistent source order prevents uniqueness-lock inversion on retries.
            for batch in request.sources:
                await _ensure_source(repository, batch.document)
            candidate = AnalysisRun(
                request.workspace_id,
                request.user_id,
                ModuleType.FINANCIAL,
                "1",
                PIPELINE_VERSION,
                request.language,
                tuple(b.document.source_id for b in request.sources),
                (
                    *tuple(b.document.content_hash for b in request.sources),
                    request.fingerprint,
                ),
                "internal-financial-pipeline",
                request.idempotency_key,
            )
            stored, created = await repository.get_or_create_run(candidate)
            if not created:
                checkpoint = await self.session.scalar(
                    select(StageResultModel).where(
                        StageResultModel.run_id == stored.id,
                        StageResultModel.stage_name == "checkpoint",
                        StageResultModel.stage_version == PIPELINE_VERSION,
                        StageResultModel.input_hash == request.fingerprint,
                    )
                )
                if (
                    checkpoint is None
                    or checkpoint.status != StageStatus.COMPLETED.value
                ):
                    raise IdempotencyConflict(
                        "existing pipeline run has no completed checkpoint"
                    )
                state_matches = (
                    checkpoint.payload["workflow_status"] == stored.workflow_status
                )
                if (
                    not state_matches
                    and checkpoint.payload["workflow_status"] == "calculating"
                    and stored.workflow_status
                    in {
                        "analyzing",
                        "auditing",
                        "compiling",
                        "completed",
                        "blocked",
                        "failed",
                    }
                ):
                    # This checkpoint is an immutable F-01–F-05 snapshot. A
                    # persisted, bound F-06 draft can legitimately advance the
                    # live run while deterministic replay remains byte-for-byte.
                    downstream = (
                        await self.session.scalars(
                            select(StageResultModel).where(
                                StageResultModel.run_id == stored.id,
                                StageResultModel.stage_name == "financial_analyst",
                                StageResultModel.status == "completed",
                            )
                        )
                    ).all()
                    state_matches = any(
                        s.payload.get("checkpoint_hash") == checkpoint.output_hash
                        and s.output_hash == semantic_hash(s.payload)
                        and s.payload.get("status") == "draft"
                        for s in downstream
                    )
                if (
                    checkpoint.output_hash != semantic_hash(checkpoint.payload)
                    or not state_matches
                ):
                    raise IdempotencyConflict("pipeline checkpoint integrity mismatch")
                return PipelineResult(stored.id, deepcopy(checkpoint.payload), True)

            run = candidate.transition(WorkflowStatus.PARSING)
            evaluation = evaluate(request, run.run_id, run.started_at)
            previous_hash = request.fingerprint
            hashes = {}
            for name, payload in zip(STAGES, evaluation.payloads):
                payload = canonicalize(payload)
                output_hash = semantic_hash(payload)
                await repository.persist_stage(
                    StageResult(
                        run.run_id,
                        name,
                        PIPELINE_VERSION,
                        StageStatus.COMPLETED,
                        previous_hash,
                        output_hash,
                        "1",
                        payload,
                        stage_result_id=uuid5(
                            TRACE_NAMESPACE, f"stage:{run.run_id}:{name}"
                        ),
                        started_at=run.started_at,
                        completed_at=run.started_at,
                    )
                )
                hashes[name] = output_hash
                previous_hash = output_hash
                if name == "metrics":
                    for _, observation, trace in evaluation.metrics:
                        await repository.persist_metric(run.run_id, observation, trace)
                elif name == "reconciliation":
                    for result in evaluation.reconciliations:
                        await repository.persist_reconciliation(run.run_id, result)
                elif name == "quality_gates":
                    await repository.persist_quality_gate(
                        evaluation.coverage, evaluation.issues, evaluation.decisions
                    )

            if evaluation.workflow_status == WorkflowStatus.MAPPING_REQUIRED:
                run = run.transition(WorkflowStatus.MAPPING_REQUIRED)
            else:
                run = run.transition(WorkflowStatus.VALIDATING).transition(
                    evaluation.workflow_status
                )
            stored.workflow_status, stored.started_at = run.status.value, run.started_at
            payload = {
                "run_id": str(run.run_id),
                "pipeline_version": PIPELINE_VERSION,
                "request_hash": request.fingerprint,
                "workflow_status": run.status.value,
                "ready": evaluation.ready,
                "stage_hashes": hashes,
                "requested_capabilities": [
                    c.value for c in request.requested_capabilities
                ],
                "quality": evaluation.payloads[3],
                "metrics": evaluation.payloads[1]["metrics"],
                "reconciliations": evaluation.payloads[2]["results"],
            }
            payload = canonicalize(payload)
            await repository.persist_stage(
                StageResult(
                    run.run_id,
                    "checkpoint",
                    PIPELINE_VERSION,
                    StageStatus.COMPLETED,
                    request.fingerprint,
                    semantic_hash(payload),
                    "1",
                    payload,
                    stage_result_id=uuid5(
                        TRACE_NAMESPACE, f"stage:{run.run_id}:checkpoint"
                    ),
                    started_at=run.started_at,
                    completed_at=run.started_at,
                )
            )
            await self.session.flush()
            return PipelineResult(run.run_id, deepcopy(payload), False)
