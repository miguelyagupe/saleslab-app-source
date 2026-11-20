"""Persisted F-07 approval boundary; neither analyst nor critic publishes reports."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select

from models import WorkspaceMember
from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.analyst.contracts import ANALYST_VERSION
from saleslab_v2.analyst.persistence import PersistedFinancialEvidence
from saleslab_v2.domain.enums import StageStatus
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.domain.runs import Failure, StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import AnalysisRunModel, StageResultModel
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.contracts import PIPELINE_VERSION

from .context import (
    CONTEXT_VERSION,
    LEGACY_CONTEXT_VERSION,
    available_comparatives,
    comparative_directions,
    presentation_preview,
)
from .contracts import (
    AUDITOR_VERSION,
    CRITIC_CHECKS,
    MAX_REVISIONS,
    AuditResult,
    CriticUnavailable,
    IndependentCritic,
    InvalidCriticOutput,
    assemble_result,
    audit_check,
    bind_critique,
)
from .statement import statement_checks


class FinancialAuditor:
    def __init__(self, session, settings, critic: IndependentCritic):
        self.session, self.settings, self.critic = session, settings, critic

    async def execute(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        run_id: UUID,
        analyst_attempt_key="initial",
        attempt_key="initial",
    ) -> AuditResult:
        if any(not isinstance(v, UUID) for v in (workspace_id, user_id, run_id)):
            raise TypeError("workspace, actor and run must be UUIDs")
        for key in (attempt_key, analyst_attempt_key):
            if (
                not isinstance(key, str)
                or not key
                or key != key.strip()
                or len(key) > 100
            ):
                raise ValueError("invalid audit or analyst attempt key")
        settings = self.settings
        if not (
            settings.enabled
            and settings.persist_stages
            and settings.financial_audit_enabled
            and str(workspace_id) in settings.internal_workspaces
        ):
            raise PermissionError(
                "internal financial audit is disabled for this workspace"
            )
        configuration = deepcopy(self.critic.configuration)
        if (
            not isinstance(configuration, dict)
            or configuration.get("role") != "independent_critic"
        ):
            raise ValueError("a separately configured independent critic is required")
        semantic_hash(configuration)
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
                raise InvalidCheckpoint("run is not an internal financial pipeline")
            draft_id = uuid5(TRACE_NAMESPACE, f"analyst:{run_id}:{analyst_attempt_key}")
            draft_stage = await self.session.get(
                StageResultModel, draft_id, populate_existing=True
            )
            if (
                draft_stage is None
                or draft_stage.run_id != run_id
                or draft_stage.stage_name != "financial_analyst"
                or draft_stage.stage_version != ANALYST_VERSION
                or draft_stage.status != "completed"
            ):
                raise InvalidCheckpoint("a completed scoped analyst draft is required")
            draft = deepcopy(draft_stage.payload)
            saved_input = await self.session.get(
                StageResultModel,
                uuid5(TRACE_NAMESPACE, f"analyst-input:{run_id}:{analyst_attempt_key}"),
                populate_existing=True,
            )
            audit_id = uuid5(TRACE_NAMESPACE, f"audit:{run_id}:{attempt_key}")
            checks, context, checkpoint_hash, revision_number = [], None, None, 0
            try:
                if (
                    draft_stage.output_hash != semantic_hash(draft)
                    or saved_input is None
                    or saved_input.run_id != run_id
                    or saved_input.stage_name != "financial_analyst_input"
                    or saved_input.stage_version != ANALYST_VERSION
                    or saved_input.status != "completed"
                    or saved_input.output_hash != semantic_hash(saved_input.payload)
                    or saved_input.input_hash != draft_stage.input_hash
                    or saved_input.output_hash != draft_stage.input_hash
                ):
                    raise InvalidCheckpoint("analyst audit-stage integrity mismatch")
                _, stages, context = await PersistedFinancialEvidence(
                    self.session
                ).load(run)
                if context is None:
                    raise InvalidCheckpoint("deterministic checkpoint is blocked")
                checkpoint_hash = context["checkpoint_hash"]
                from .revisions import revision_for_input, verify_revision_parent

                revision = await revision_for_input(self.session, run_id, saved_input)
                if revision is not None:

                    await verify_revision_parent(
                        self.session, run_id, revision, input_stage_id=saved_input.id
                    )
                    revision_number = revision["number"]
                    if (
                        type(revision_number) is not int
                        or not 1 <= revision_number <= MAX_REVISIONS
                    ):
                        raise InvalidCheckpoint("invalid revision budget")
                checks = statement_checks(
                    run, stages, draft, saved_input.payload, context, audit_id=audit_id
                )
            except (InvalidCheckpoint, ValueError, TypeError, KeyError):
                checks = [
                    audit_check(
                        audit_id,
                        "statement",
                        "block",
                        "audit.evidence_integrity_failed",
                        refs=(str(draft_id),),
                        remediation="Create a corrected run from the original sources or request human review.",
                    )
                ]
            model_input = {
                "evidence_context": context,
                "draft": draft,
                "required_checks": sorted(CRITIC_CHECKS),
            }
            # Old audits rebuild their original input byte-for-byte on replay.
            # Comparative rows are critic-only and require verified source stages.
            if configuration.get("context_version") in {
                LEGACY_CONTEXT_VERSION,
                CONTEXT_VERSION,
            }:
                if context is not None and not any(
                    c["status"] == "block" for c in checks
                ):
                    model_input["available_comparatives"] = available_comparatives(
                        stages
                    )
                    model_input["presentation"] = presentation_preview(draft, context)
                else:
                    model_input["available_comparatives"] = []
                    model_input["presentation"] = None
            if configuration.get("context_version") == CONTEXT_VERSION:
                model_input["comparative_directions"] = (
                    comparative_directions(stages)
                    if context is not None
                    and not any(c["status"] == "block" for c in checks)
                    else []
                )
            if len(json.dumps(model_input, ensure_ascii=True).encode()) > 384_000:
                checks.append(
                    audit_check(
                        audit_id,
                        "schema",
                        "block",
                        "audit.context_too_large",
                        refs=(str(draft_id),),
                        remediation="Split the analysis into explicitly scoped runs.",
                    )
                )
            input_payload = {
                "context": model_input,
                "configuration": configuration,
                "deterministic_checks": deepcopy(checks),
                "draft_stage_id": str(draft_id),
                "draft_hash": semantic_hash(draft),
                "stored_draft_hash": draft_stage.output_hash,
                "saved_input_hash": saved_input.output_hash if saved_input else None,
                "checkpoint_hash": checkpoint_hash,
                "attempt_key": attempt_key,
                "analyst_attempt_key": analyst_attempt_key,
                "auditor_version": AUDITOR_VERSION,
            }
            input_hash = semantic_hash(input_payload)
            existing = await self.session.get(
                StageResultModel, audit_id, populate_existing=True
            )
            if existing:
                if (
                    existing.run_id != run_id
                    or existing.stage_name != "financial_audit"
                    or existing.input_hash != input_hash
                ):
                    raise IdempotencyConflict(
                        "audit attempt key was used with different inputs/configuration"
                    )
                prior_input = await self.session.get(
                    StageResultModel,
                    uuid5(TRACE_NAMESPACE, f"audit-input:{run_id}:{attempt_key}"),
                    populate_existing=True,
                )
                if (
                    existing.output_hash != semantic_hash(existing.payload)
                    or existing.status not in {"completed", "failed"}
                    or prior_input is None
                    or prior_input.run_id != run_id
                    or prior_input.status != "completed"
                    or prior_input.payload != input_payload
                    or prior_input.input_hash != input_hash
                    or prior_input.output_hash != input_hash
                ):
                    raise InvalidCheckpoint("audit replay integrity mismatch")
                return AuditResult(run_id, deepcopy(existing.payload), True)
            if run.workflow_status != "auditing":
                raise IdempotencyConflict(
                    "run is not awaiting audit; replay the original attempt"
                )
            latest = await self.session.scalar(
                select(StageResultModel.id)
                .where(
                    StageResultModel.run_id == run_id,
                    StageResultModel.stage_name == "financial_analyst",
                    StageResultModel.status == "completed",
                )
                .order_by(
                    StageResultModel.started_at.desc(), StageResultModel.id.desc()
                )
                .limit(1)
            )
            if latest != draft_id:
                raise IdempotencyConflict(
                    "only the current analyst draft may receive a new audit"
                )
            started = datetime.now(UTC)
            retryable, error = False, None
            if not any(c["status"] == "block" for c in checks):
                try:
                    raw = await self.critic.generate(deepcopy(model_input))
                    if self.critic.configuration != configuration:
                        raise InvalidCriticOutput("critic configuration changed")
                    semantic_checks, _ = bind_critique(
                        raw, draft, audit_id=audit_id, language=context["language"]
                    )
                    checks.extend(semantic_checks)
                except (InvalidCriticOutput, CriticUnavailable) as exc:
                    code = (
                        "audit.invalid_critic_output"
                        if isinstance(exc, InvalidCriticOutput)
                        else "audit.critic_unavailable"
                    )
                    checks.append(
                        audit_check(
                            audit_id,
                            "critic",
                            "block",
                            code,
                            refs=(str(draft_id),),
                            remediation="Retry the independent critic with a new explicit audit attempt key.",
                            deterministic=False,
                        )
                    )
                    retryable, error = True, Failure(code, code)
            finding_ids = {
                f["finding_id"]
                for f in draft.get("findings", [])
                if isinstance(f, dict) and isinstance(f.get("finding_id"), str)
            }
            result = assemble_result(
                audit_id=audit_id,
                run_id=run_id,
                draft_stage_id=draft_id,
                draft_hash=semantic_hash(draft),
                checkpoint_hash=checkpoint_hash,
                checks=checks,
                finding_ids=finding_ids,
                configuration=configuration,
                audited_at=datetime.now(UTC),
                revision_number=revision_number,
                retryable=retryable,
            )
            run.workflow_status = (
                "auditing"
                if retryable
                else {
                    "approved": "compiling",
                    "approved_with_warnings": "compiling",
                    "revision_required": "analyzing",
                    "blocked": "blocked",
                }[result["status"]]
            )
            repository = V2Repository(self.session, workspace_id, persist_stages=True)
            await repository.persist_stage(
                StageResult(
                    run_id,
                    "financial_audit_input",
                    AUDITOR_VERSION,
                    StageStatus.COMPLETED,
                    input_hash,
                    input_hash,
                    "1",
                    input_payload,
                    stage_result_id=uuid5(
                        TRACE_NAMESPACE, f"audit-input:{run_id}:{attempt_key}"
                    ),
                    started_at=started,
                    completed_at=started,
                )
            )
            await repository.persist_stage(
                StageResult(
                    run_id,
                    "financial_audit",
                    AUDITOR_VERSION,
                    StageStatus.FAILED if retryable else StageStatus.COMPLETED,
                    input_hash,
                    semantic_hash(result),
                    "1",
                    result,
                    stage_result_id=audit_id,
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    error=error,
                )
            )
            await self.session.flush()
            return AuditResult(run_id, deepcopy(result), False)
