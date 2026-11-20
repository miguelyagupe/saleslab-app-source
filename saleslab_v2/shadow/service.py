"""Atomic, scoped F-09 case execution and immutable shadow evidence."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic_ns
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from models import WorkspaceMember
from saleslab_v2.analyst.persistence import PersistedFinancialEvidence
from saleslab_v2.analyst.service import FinancialAnalyst
from saleslab_v2.audit.workflow import FinancialReviewWorkflow
from saleslab_v2.domain.enums import ModuleType, StageStatus
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.domain.runs import AnalysisRun, StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import AnalysisRunModel, StageResultModel
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.service import FinancialPipeline
from saleslab_v2.reports.service import FinancialReportCompiler

from .comparison import compare_case
from .contracts import (
    SHADOW_VERSION,
    InvalidShadow,
    LegacyUnavailable,
    ShadowCase,
    ShadowResult,
)
from .legacy import snapshot_legacy
from .projection import shared_projection


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class DeterministicPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    forecast_allowed: bool


class LegacyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    report: dict
    usage: Usage | None
    deterministic_policy: DeterministicPolicy | None = None


class LegacyCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["completed", "failed"]
    output: LegacyOutput | None
    error_code: Literal["shadow.legacy_failed"] | None
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "completed":
            if self.output is None or self.error_code is not None:
                raise ValueError("completed legacy capture requires output only")
        elif self.output is not None or self.error_code is None:
            raise ValueError("failed legacy capture requires an error only")
        return self


class V2Capture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot: dict
    duration_ms: int = Field(ge=0)


async def capture_v2(session, run_id):
    run = await session.get(AnalysisRunModel, run_id, populate_existing=True)
    checkpoint, _, _ = await PersistedFinancialEvidence(session).load(run)
    stages = (
        await session.scalars(
            select(StageResultModel)
            .where(StageResultModel.run_id == run_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    if any(
        s.status not in {"completed", "failed"}
        or s.output_hash != semantic_hash(s.payload)
        for s in stages
    ):
        raise InvalidShadow("V2 stage integrity differs")
    audits = sorted(
        (s for s in stages if s.stage_name == "financial_audit"),
        key=lambda s: (s.started_at, str(s.id)),
    )
    reports = [s for s in stages if s.stage_name == "financial_report"]
    if len(reports) > 1:
        raise InvalidShadow("ambiguous V2 report")
    return {
        "run_id": str(run_id),
        "workflow_status": run.workflow_status,
        "checkpoint": checkpoint,
        "audit": deepcopy(audits[-1].payload) if audits else None,
        "report": deepcopy(reports[0].payload) if reports else None,
        "stage_hashes": {
            str(s.id): {
                "name": s.stage_name,
                "version": s.stage_version,
                "status": s.status,
                "input_hash": s.input_hash,
                "output_hash": s.output_hash,
            }
            for s in stages
        },
    }


class FinancialShadowRunner:
    def __init__(
        self,
        session,
        settings,
        analyst_model,
        critic,
        legacy_runner,
        *,
        legacy_timeout_seconds=60,
    ):
        if (
            type(legacy_timeout_seconds) is not int
            or not 1 <= legacy_timeout_seconds <= 120
        ):
            raise ValueError("invalid legacy timeout")
        self.session, self.settings = session, settings
        self.analyst_model, self.critic, self.legacy = (
            analyst_model,
            critic,
            legacy_runner,
        )
        self.legacy_timeout_seconds = legacy_timeout_seconds

    @property
    def configuration(self):
        return deepcopy(
            {
                "legacy": self.legacy.configuration,
                "analyst": self.analyst_model.configuration,
                "critic": self.critic.configuration,
                "legacy_timeout_seconds": self.legacy_timeout_seconds,
            }
        )

    async def execute(self, case: ShadowCase, *, attempt_key: str):
        case = case.snapshot()
        if (
            not isinstance(attempt_key, str)
            or not attempt_key
            or attempt_key != attempt_key.strip()
            or len(attempt_key) > 100
        ):
            raise ValueError("invalid explicit shadow attempt key")
        request, settings = case.request, self.settings
        if not (
            settings.enabled
            and settings.persist_stages
            and settings.financial_shadow_enabled
            and settings.financial_analyst_enabled
            and settings.financial_audit_enabled
            and settings.financial_report_enabled
            and str(request.workspace_id) in settings.internal_workspaces
        ):
            raise PermissionError("internal financial shadow evaluation is disabled")
        configs = self.configuration
        if configs["legacy"].get("role") != "legacy_financial":
            raise ValueError("a configured legacy financial runner is required")
        child_key = str(
            uuid5(TRACE_NAMESPACE, f"shadow-child:{request.workspace_id}:{attempt_key}")
        )
        request = replace(request, idempotency_key=child_key)
        async with self.session.begin_nested():
            member = await self.session.scalar(
                select(WorkspaceMember.id).where(
                    WorkspaceMember.workspace_id == request.workspace_id,
                    WorkspaceMember.user_id == request.user_id,
                    WorkspaceMember.role.in_(("owner", "manager")),
                )
            )
            if member is None:
                raise PermissionError("actor is not an authorized workspace member")
            # Deterministic child/source registration precedes the parent FK checks;
            # neither runner/model has been called yet. All writes share this savepoint.
            before = monotonic_ns()
            child = await FinancialPipeline(self.session, settings).execute(request)
            pipeline_ns = monotonic_ns() - before
            input_stage = await self.session.scalar(
                select(StageResultModel).where(
                    StageResultModel.run_id == child.run_id,
                    StageResultModel.stage_name == "normalization",
                )
            )
            shared = shared_projection(case, input_stage.payload)
            input_payload = {
                "case": case.payload(),
                "configurations": configs,
                "shared_input": shared,
                "v2_run_id": str(child.run_id),
                "attempt_key": attempt_key,
                "shadow_version": SHADOW_VERSION,
            }
            digest = semantic_hash(input_payload)
            if len(json.dumps(input_payload, ensure_ascii=True).encode()) > 1_000_000:
                raise InvalidShadow("shadow input exceeds the evaluation limit")
            repo = V2Repository(self.session, request.workspace_id, persist_stages=True)
            candidate = AnalysisRun(
                request.workspace_id,
                request.user_id,
                ModuleType.FINANCIAL,
                "1",
                SHADOW_VERSION,
                request.language,
                tuple(b.document.source_id for b in request.sources),
                (digest,),
                "internal-financial-shadow",
                attempt_key,
            )
            run, created = await repo.get_or_create_run(candidate)
            run = await self.session.scalar(
                select(AnalysisRunModel)
                .where(AnalysisRunModel.id == run.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
            names = (
                "financial_shadow_input",
                "financial_shadow_legacy",
                "financial_shadow_v2",
                "financial_shadow_result",
            )
            ids = {n: uuid5(TRACE_NAMESPACE, f"{n}:{run.id}") for n in names}
            if not created:
                saved = {
                    n: await self.session.get(
                        StageResultModel, sid, populate_existing=True
                    )
                    for n, sid in ids.items()
                }
                if any(
                    s is None
                    or s.run_id != run.id
                    or s.stage_name != n
                    or s.stage_version != SHADOW_VERSION
                    or s.status != "completed"
                    or s.input_hash != digest
                    or s.output_hash != semantic_hash(s.payload)
                    for n, s in saved.items()
                ):
                    raise InvalidShadow("shadow replay stage integrity mismatch")
                if (
                    saved[names[0]].payload != input_payload
                    or run.workflow_status != "completed"
                    or run.completed_at is None
                ):
                    raise IdempotencyConflict("shadow replay inputs or state differ")
                LegacyCapture.model_validate(saved[names[1]].payload)
                V2Capture.model_validate(saved[names[2]].payload)
                current = await capture_v2(self.session, child.run_id)
                if current != saved[names[2]].payload["snapshot"]:
                    raise InvalidShadow("V2 run has changed since shadow evaluation")
                if current["report"] is not None:
                    await FinancialReportCompiler(self.session, settings).execute(
                        workspace_id=request.workspace_id,
                        user_id=request.user_id,
                        run_id=child.run_id,
                        audit_id=UUID(
                            current["report"]["approval_binding"]["audit_id"]
                        ),
                    )
                expected = compare_case(
                    case,
                    saved[names[1]].payload,
                    current,
                    configurations=configs,
                    v2_duration_ms=saved[names[2]].payload["duration_ms"],
                )
                if expected != saved[names[3]].payload:
                    raise InvalidShadow(
                        "shadow comparison output differs from stored evidence"
                    )
                return ShadowResult(run.id, deepcopy(expected), True)
            started = datetime.now(UTC)
            before = monotonic_ns()
            try:
                raw = await asyncio.wait_for(
                    self.legacy.generate(deepcopy(shared)),
                    timeout=self.legacy_timeout_seconds,
                )
                output = LegacyOutput.model_validate(snapshot_legacy(raw)).model_dump()
                if len(str(output).encode()) > 512_000:
                    raise ValueError("legacy output exceeds limit")
                legacy = {"status": "completed", "output": output, "error_code": None}
            except (LegacyUnavailable, TimeoutError, ValueError, TypeError):
                legacy = {
                    "status": "failed",
                    "output": None,
                    "error_code": "shadow.legacy_failed",
                }
            legacy["duration_ms"] = (monotonic_ns() - before) // 1_000_000
            if self.legacy.configuration != configs["legacy"]:
                raise InvalidShadow("legacy runner configuration changed")
            before = monotonic_ns()
            if child.ready:
                analyst = await FinancialAnalyst(
                    self.session, settings, self.analyst_model
                ).execute(
                    workspace_id=request.workspace_id,
                    user_id=request.user_id,
                    run_id=child.run_id,
                )
                if analyst.payload["status"] == "draft":
                    review = await FinancialReviewWorkflow(
                        self.session, settings, self.analyst_model, self.critic
                    ).execute(
                        workspace_id=request.workspace_id,
                        user_id=request.user_id,
                        run_id=child.run_id,
                    )
                    if review["status"] in {"approved", "approved_with_warnings"}:
                        await FinancialReportCompiler(self.session, settings).execute(
                            workspace_id=request.workspace_id,
                            user_id=request.user_id,
                            run_id=child.run_id,
                            audit_id=UUID(review["audits"][-1]["audit_id"]),
                        )
            if (
                self.analyst_model.configuration != configs["analyst"]
                or self.critic.configuration != configs["critic"]
            ):
                raise InvalidShadow("V2 model configuration changed")
            v2 = await capture_v2(self.session, child.run_id)
            v2_output = {
                "snapshot": v2,
                "duration_ms": (pipeline_ns + monotonic_ns() - before) // 1_000_000,
            }
            LegacyCapture.model_validate(legacy)
            V2Capture.model_validate(v2_output)
            result = compare_case(
                case,
                legacy,
                v2,
                configurations=configs,
                v2_duration_ms=v2_output["duration_ms"],
            )
            for name, payload in zip(names, (input_payload, legacy, v2_output, result)):
                await repo.persist_stage(
                    StageResult(
                        run.id,
                        name,
                        SHADOW_VERSION,
                        StageStatus.COMPLETED,
                        digest,
                        semantic_hash(payload),
                        "1",
                        payload,
                        stage_result_id=ids[name],
                        started_at=started,
                        completed_at=datetime.now(UTC),
                    )
                )
            run.workflow_status, run.started_at, run.completed_at = (
                "completed",
                started,
                datetime.now(UTC),
            )
            await self.session.flush()
            return ShadowResult(run.id, deepcopy(result), False)
