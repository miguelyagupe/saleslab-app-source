"""One transaction for an audited report, history record and quota consumption."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import func, select

from auth import (
    consume_quota_after_success,
    ensure_quota_available,
    get_remaining_quota,
)
from models import Analysis, Workspace, WorkspaceMember
from saleslab_v2.analyst.service import FinancialAnalyst
from saleslab_v2.audit.workflow import FinancialReviewWorkflow
from saleslab_v2.domain.enums import StageStatus
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.domain.runs import StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import AnalysisRunModel, StageResultModel
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.service import FinancialPipeline
from saleslab_v2.reports.contracts import InvalidReport
from saleslab_v2.reports.service import FinancialReportCompiler

from .contracts import VERSION
from .planning import prepare_upload


def available(settings, workspace_id, role):
    return bool(
        settings.enabled
        and settings.persist_stages
        and settings.financial_product_enabled
        and settings.financial_analyst_enabled
        and settings.financial_audit_enabled
        and settings.financial_report_enabled
        and str(workspace_id) in settings.internal_workspaces
        and role in {"owner", "manager"}
    )


class FinancialProduct:
    def __init__(self, session, settings, analyst=None, critic=None):
        self.session, self.settings, self.analyst, self.critic = (
            session,
            settings,
            analyst,
            critic,
        )

    async def generate(self, parsed, choices, user_id):
        request, preview = prepare_upload(parsed, choices, user_id, choices.attempt_key)
        if preview["preview_hash"] != choices.preview_hash:
            raise IdempotencyConflict(
                "The file or validation choices changed; validate again."
            )
        workspace_id = request.workspace_id
        async with self.session.begin_nested():
            role = await self.session.scalar(
                select(WorkspaceMember.role).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
            )
            if not available(self.settings, workspace_id, role):
                raise PermissionError("validated financial upload is disabled")
            # Serialize V2 publication and replays, including provider calls, per workspace.
            # Legacy quota consumption remains guarded by its atomic counter update too.
            await self.session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(
                            "financial-product:" + str(workspace_id), 0
                        )
                    )
                )
            )
            workspace = await self.session.scalar(
                select(Workspace)
                .where(Workspace.id == workspace_id)
                .execution_options(populate_existing=True)
            )
            pipeline = await FinancialPipeline(self.session, self.settings).execute(
                request
            )
            sid = uuid5(TRACE_NAMESPACE, "financial-product:" + str(pipeline.run_id))
            saved = await self.session.get(
                StageResultModel, sid, populate_existing=True
            )
            input_payload = {
                "version": VERSION,
                "actor_id": str(user_id),
                "preview_hash": preview["preview_hash"],
                "request_hash": request.fingerprint,
                "original_rows": list(parsed.original_rows),
                "mapping_decisions": parsed.decisions,
                "analyst": self.analyst.configuration,
                "critic": self.critic.configuration,
            }
            if parsed.import_manifest is not None:
                input_payload["source_import"] = parsed.import_manifest
            digest = semantic_hash(input_payload)
            if saved is not None:
                if (
                    saved.stage_name != "financial_product"
                    or saved.run_id != pipeline.run_id
                    or saved.stage_version != VERSION
                    or saved.status != "completed"
                    or saved.input_hash != digest
                    or saved.output_hash != semantic_hash(saved.payload)
                    or saved.payload.get("input") != input_payload
                ):
                    raise IdempotencyConflict(
                        "Stored financial publication differs from this attempt."
                    )
                response = deepcopy(saved.payload["response"])
                if response["status"] == "completed":
                    await self.read_report(workspace_id, user_id, pipeline.run_id)
                response["analyses_remaining"] = await get_remaining_quota(
                    self.session, workspace
                )
                response["replayed"] = True
                return response
            response = {
                "schema": "financial-v2",
                "run_id": str(pipeline.run_id),
                "status": pipeline.payload["workflow_status"],
                "quality": preview["quality"],
                "issues": preview["issues"],
                "replayed": False,
            }
            if pipeline.ready and preview["ready"]:
                await ensure_quota_available(self.session, workspace)
                analyst = await FinancialAnalyst(
                    self.session, self.settings, self.analyst
                ).execute(
                    workspace_id=workspace_id, user_id=user_id, run_id=pipeline.run_id
                )
                response["status"] = "failed"
                if analyst.payload["status"] == "draft":
                    review = await FinancialReviewWorkflow(
                        self.session, self.settings, self.analyst, self.critic
                    ).execute(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        run_id=pipeline.run_id,
                    )
                    response["status"] = review["status"]
                    if review["status"] in {"approved", "approved_with_warnings"}:
                        compiled = await FinancialReportCompiler(
                            self.session, self.settings
                        ).execute(
                            workspace_id=workspace_id,
                            user_id=user_id,
                            run_id=pipeline.run_id,
                            audit_id=UUID(review["audits"][-1]["audit_id"]),
                        )
                        envelope = {
                            "schema": "financial-v2",
                            "report_type": "financial",
                            "run_id": str(pipeline.run_id),
                            "report_id": compiled.payload["report_id"],
                            "period": preview["target_period"],
                            "kpis": [],
                            "executive_summary": "Relatório financeiro V2 auditado. Abra o relatório para consultar os valores verificados.",
                        }
                        history_id = uuid5(
                            TRACE_NAMESPACE, "financial-history:" + str(pipeline.run_id)
                        )
                        self.session.add(
                            Analysis(
                                id=history_id,
                                workspace_id=workspace_id,
                                created_by_user_id=user_id,
                                module_type="financial",
                                period_label=preview["target_period"],
                                result_json=envelope,
                                data_quality="partial",
                            )
                        )
                        await self.session.flush()
                        await consume_quota_after_success(self.session, workspace)
                        response.update(
                            status="completed",
                            history=envelope,
                            history_id=str(history_id),
                            audit_id=review["audits"][-1]["audit_id"],
                        )
                if response["status"] != "completed":
                    response["message"] = (
                        "The analysis was not approved for publication. No quota was consumed. Start a new attempt after reviewing the data."
                    )
            elif not preview["ready"]:
                response["status"] = (
                    "mapping_required" if not pipeline.ready else "blocked"
                )
                response["message"] = (
                    "Validate the mappings, target period and required inputs before generating."
                )
            now = datetime.now(UTC)
            await V2Repository(
                self.session, workspace_id, persist_stages=True
            ).persist_stage(
                StageResult(
                    pipeline.run_id,
                    "financial_product",
                    VERSION,
                    StageStatus.COMPLETED,
                    digest,
                    semantic_hash({"input": input_payload, "response": response}),
                    "1",
                    {"input": input_payload, "response": response},
                    stage_result_id=sid,
                    started_at=now,
                    completed_at=now,
                )
            )
            response["analyses_remaining"] = await get_remaining_quota(
                self.session, workspace
            )
            return response

    async def read_report(self, workspace_id, user_id, run_id):
        membership = await self.session.scalar(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if membership not in {"owner", "manager"}:
            raise PermissionError("workspace membership required")
        run = await self.session.scalar(
            select(AnalysisRunModel).where(
                AnalysisRunModel.id == run_id,
                AnalysisRunModel.workspace_id == workspace_id,
            )
        )
        saved = await self.session.get(
            StageResultModel,
            uuid5(TRACE_NAMESPACE, "financial-product:" + str(run_id)),
            populate_existing=True,
        )
        if (
            run is None
            or saved is None
            or saved.run_id != run_id
            or saved.stage_name != "financial_product"
            or saved.stage_version != VERSION
            or saved.status != "completed"
            or saved.output_hash != semantic_hash(saved.payload)
            or set(saved.payload) != {"input", "response"}
            or saved.input_hash != semantic_hash(saved.payload["input"])
            or saved.payload["input"].get("request_hash") not in run.input_hashes
        ):
            raise InvalidReport("A published financial report is required.")
        response = saved.payload["response"]
        if (
            response.get("status") != "completed"
            or response.get("schema") != "financial-v2"
            or response.get("run_id") != str(run_id)
            or response.get("history_id")
            != str(uuid5(TRACE_NAMESPACE, "financial-history:" + str(run_id)))
        ):
            raise InvalidReport("The analysis has not been approved for publication.")
        history = await self.session.get(
            Analysis, UUID(response["history_id"]), populate_existing=True
        )
        if (
            history is None
            or history.workspace_id != workspace_id
            or history.module_type != "financial"
            or history.created_by_user_id != run.created_by_user_id
            or history.result_json != response["history"]
        ):
            raise InvalidReport("Published report history differs.")
        report_stage = await self.session.scalar(
            select(StageResultModel).where(
                StageResultModel.run_id == run_id,
                StageResultModel.stage_name == "financial_report",
            )
        )
        if (
            report_stage is None
            or report_stage.payload.get("report_id") != response["history"]["report_id"]
        ):
            raise InvalidReport("Published report artifact is missing.")
        return await FinancialReportCompiler(self.session, self.settings).execute(
            workspace_id=workspace_id,
            user_id=user_id,
            run_id=run_id,
            audit_id=UUID(response["audit_id"]),
        )
