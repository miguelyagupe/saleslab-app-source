"""Allowlisted atomic compilation: exact approval → parity gate → completed run."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select

from models import WorkspaceMember
from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.analyst.persistence import PersistedFinancialEvidence
from saleslab_v2.audit.contracts import AUDITOR_VERSION
from saleslab_v2.audit.revisions import revision_for_input
from saleslab_v2.audit.service import FinancialAuditor
from saleslab_v2.domain.enums import StageStatus
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.domain.runs import StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import AnalysisRunModel, StageResultModel
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.contracts import PIPELINE_VERSION

from .compiler import compile_report, verify_approval
from .contracts import (
    COMPILER_VERSION,
    RENDER_VERSION,
    REPORT_LOCALES,
    CompiledReport,
    InvalidReport,
)
from .render import render_channels, verify_artifacts


class _ReplayOnlyCritic:
    def __init__(self, configuration):
        self.configuration = deepcopy(configuration)

    async def generate(self, context):
        raise InvalidReport("compilation cannot invoke or retry an independent critic")


class FinancialReportCompiler:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    async def execute(
        self, *, workspace_id: UUID, user_id: UUID, run_id: UUID, audit_id: UUID
    ):
        if any(
            not isinstance(v, UUID) for v in (workspace_id, user_id, run_id, audit_id)
        ):
            raise TypeError("workspace, actor, run and audit must be UUIDs")
        if not (
            self.settings.enabled
            and self.settings.persist_stages
            and self.settings.financial_report_enabled
            and self.settings.financial_audit_enabled
            and str(workspace_id) in self.settings.internal_workspaces
        ):
            raise PermissionError(
                "internal financial compilation is disabled for this workspace"
            )
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
            audit_stage = await self.session.get(
                StageResultModel, audit_id, populate_existing=True
            )
            if (
                audit_stage is None
                or audit_stage.run_id != run_id
                or audit_stage.stage_name != "financial_audit"
                or audit_stage.stage_version != AUDITOR_VERSION
                or audit_stage.status != "completed"
                or audit_stage.output_hash != semantic_hash(audit_stage.payload)
            ):
                raise InvalidReport("a completed scoped audit stage is required")
            audit = deepcopy(audit_stage.payload)
            if audit.get("status") not in {"approved", "approved_with_warnings"}:
                raise InvalidReport("audit has not approved publication")
            inputs = (
                await self.session.scalars(
                    select(StageResultModel)
                    .where(
                        StageResultModel.run_id == run_id,
                        StageResultModel.stage_name == "financial_audit_input",
                        StageResultModel.output_hash == audit_stage.input_hash,
                    )
                    .execution_options(populate_existing=True)
                )
            ).all()
            if len(inputs) != 1:
                raise InvalidReport("exact audit input is missing or ambiguous")
            audit_input = inputs[0]
            source = audit_input.payload
            if (
                audit_input.stage_version != AUDITOR_VERSION
                or audit_input.status != "completed"
                or audit_input.input_hash != audit_stage.input_hash
                or semantic_hash(source) != audit_stage.input_hash
                or audit.get("audit_id") != str(audit_id)
                or audit.get("run_id") != str(run_id)
                or audit.get("draft_stage_id") != source.get("draft_stage_id")
                or audit.get("draft_hash") != source.get("draft_hash")
                or audit.get("draft_hash") != source.get("stored_draft_hash")
                or audit.get("checkpoint_hash") != source.get("checkpoint_hash")
                or audit.get("critic_configuration") != source.get("configuration")
            ):
                raise InvalidReport("audit input integrity mismatch")
            # Reuse F-07's full read-only replay validation: source tables, stage
            # chain, independent recalculation, exact draft/input and revision ancestry.
            replay = await FinancialAuditor(
                self.session, self.settings, _ReplayOnlyCritic(source["configuration"])
            ).execute(
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                analyst_attempt_key=source["analyst_attempt_key"],
                attempt_key=source["attempt_key"],
            )
            if not replay.replayed or replay.payload != audit:
                raise InvalidReport("compiler requires an existing exact audit replay")
            checkpoint, _, context = await PersistedFinancialEvidence(
                self.session
            ).load(run)
            if context is None:
                raise InvalidReport("current deterministic evidence blocks publication")
            draft_stage = await self.session.get(
                StageResultModel, UUID(audit["draft_stage_id"]), populate_existing=True
            )
            analyst_input = await self.session.get(
                StageResultModel,
                uuid5(
                    TRACE_NAMESPACE,
                    f"analyst-input:{run_id}:{source['analyst_attempt_key']}",
                ),
                populate_existing=True,
            )
            revision = await revision_for_input(self.session, run_id, analyst_input)
            verify_approval(
                audit,
                draft_stage.payload,
                context,
                deterministic_checks=source["deterministic_checks"],
                revision_number=revision["number"] if revision else 0,
            )
            for name, expected in (
                ("financial_analyst", draft_stage.id),
                ("financial_audit", audit_id),
            ):
                latest = await self.session.scalar(
                    select(StageResultModel.id)
                    .where(
                        StageResultModel.run_id == run_id,
                        StageResultModel.stage_name == name,
                        StageResultModel.status == "completed",
                    )
                    .order_by(
                        StageResultModel.started_at.desc(), StageResultModel.id.desc()
                    )
                    .limit(1)
                )
                if latest != expected:
                    raise IdempotencyConflict(
                        "compiler cannot publish a superseded draft or audit"
                    )
            ids = {
                name: uuid5(TRACE_NAMESPACE, f"{name}:{run_id}:{COMPILER_VERSION}")
                for name in (
                    "financial_report_input",
                    "financial_report",
                    "financial_report_render",
                )
            }
            stored = {
                name: await self.session.get(
                    StageResultModel, sid, populate_existing=True
                )
                for name, sid in ids.items()
            }
            existing = stored["financial_report"]
            # Published artifacts are immutable: replay their renderer version,
            # while new publications use the report language's number format.
            render_version = (
                existing.payload.get("render_version")
                if existing
                else (
                    "financial-render-v4"
                    if "evidence_selection" in context
                    else RENDER_VERSION
                )
            )
            if render_version not in {
                "financial-render-v1",
                "financial-render-v2",
                "financial-render-v3",
                "financial-render-v4",
            }:
                raise InvalidReport("unsupported publication renderer")
            input_payload = {
                "audit_id": str(audit_id),
                "audit_hash": audit_stage.output_hash,
                "audit_input_hash": audit_stage.input_hash,
                "draft_hash": draft_stage.output_hash,
                "checkpoint_hash": context["checkpoint_hash"],
                "compiler_version": COMPILER_VERSION,
                "render_version": render_version,
                "locale": (
                    run.language_context["format_locale"]
                    if render_version == "financial-render-v1"
                    else REPORT_LOCALES[context["language"]]
                ),
            }
            if render_version != "financial-render-v1":
                input_payload["source_locale"] = run.language_context["format_locale"]
            input_hash = semantic_hash(input_payload)
            if any(stored.values()):
                if not all(stored.values()):
                    raise InvalidReport("publication is missing an immutable stage")
                for name, stage in stored.items():
                    if (
                        stage.run_id != run_id
                        or stage.stage_name != name
                        or stage.stage_version != COMPILER_VERSION
                        or stage.status != "completed"
                        or stage.input_hash != input_hash
                        or stage.output_hash != semantic_hash(stage.payload)
                    ):
                        raise IdempotencyConflict(
                            "publication inputs or stored stages differ"
                        )
                generated_at = datetime.fromisoformat(existing.payload["generated_at"])
                if generated_at != existing.started_at:
                    raise InvalidReport(
                        "publication timestamp differs from its execution"
                    )
                expected = compile_report(
                    run=run,
                    context=context,
                    draft=draft_stage.payload,
                    audit=audit,
                    checkpoint=checkpoint,
                    generated_at=generated_at,
                    locale=input_payload["locale"],
                    render_version=render_version,
                )
                if (
                    expected != existing.payload
                    or stored["financial_report_input"].payload != input_payload
                    or run.workflow_status != "completed"
                    or run.completed_at is None
                ):
                    raise InvalidReport("publication replay contract mismatch")
                artifacts = stored["financial_report_render"].payload
                verify_artifacts(existing.payload, artifacts)
                return CompiledReport(
                    run_id, deepcopy(existing.payload), deepcopy(artifacts), True
                )
            if run.workflow_status != "compiling" or run.completed_at is not None:
                raise IdempotencyConflict("run is not awaiting compilation")
            started = datetime.now(UTC)
            payload = compile_report(
                run=run,
                context=context,
                draft=draft_stage.payload,
                audit=audit,
                checkpoint=checkpoint,
                generated_at=started,
                locale=input_payload["locale"],
                render_version=render_version,
            )
            artifacts = render_channels(payload)
            verify_artifacts(payload, artifacts)
            repository = V2Repository(self.session, workspace_id, persist_stages=True)
            for name, value in (
                ("financial_report_input", input_payload),
                ("financial_report", payload),
                ("financial_report_render", artifacts),
            ):
                await repository.persist_stage(
                    StageResult(
                        run_id,
                        name,
                        COMPILER_VERSION,
                        StageStatus.COMPLETED,
                        input_hash,
                        semantic_hash(value),
                        "1",
                        value,
                        stage_result_id=ids[name],
                        started_at=started,
                        completed_at=datetime.now(UTC),
                    )
                )
            run.workflow_status, run.completed_at = "completed", datetime.now(UTC)
            await self.session.flush()
            return CompiledReport(run_id, deepcopy(payload), deepcopy(artifacts), False)
