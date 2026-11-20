"""Durable upload jobs using existing V2 run/stage contracts and row locks."""

# ruff: noqa: BLE001 -- worker boundaries sanitize failures and survive transient errors.

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid5

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from starlette.concurrency import run_in_threadpool

from auth import ensure_quota_available
from models import Workspace
from saleslab_v2.domain.enums import LanguageCode, ModuleType, StageStatus
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun, StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import AnalysisRunModel, StageResultModel
from saleslab_v2.persistence.repositories import V2Repository

from .contracts import VERSION, GenerateChoices, InvalidUpload
from .ingestion import parse_upload
from .pdf_source import PdfDecoderBusy
from .planning import prepare_upload
from .service import FinancialProduct

PURPOSE = "financial-upload-job"
MAX_ATTEMPTS_PER_HOUR = 20


def stage_id(job_id, name):
    return uuid5(TRACE_NAMESPACE, f"upload-job:{job_id}:{name}")


async def enqueue(session, settings, parsed, choices, content, user_id, configurations):
    request, preview = prepare_upload(parsed, choices, user_id, choices.attempt_key)
    if preview["preview_hash"] != choices.preview_hash:
        raise IdempotencyConflict(
            "The file or validation choices changed; validate again."
        )
    if not preview["ready"]:
        raise InvalidUpload("Resolve the validation issues before generating.")
    workspace_id = request.workspace_id
    payload = {
        "filename": parsed.batch.document.filename,
        "content": base64.b64encode(content).decode(),
        "source_hash": sha256(content).hexdigest(),
        "choices": choices.contract_payload(),
        "models": configurations,
        "actor_id": str(user_id),
        "version": VERSION,
    }
    digest = semantic_hash(payload)
    # Short-lived queue lock is independent from the workspace lock held by models.
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended("financial-upload-queue:" + str(workspace_id), 0)
            )
        )
    )
    existing = await session.scalar(
        select(AnalysisRunModel).where(
            AnalysisRunModel.workspace_id == workspace_id,
            AnalysisRunModel.idempotency_purpose == PURPOSE,
            AnalysisRunModel.idempotency_key == choices.attempt_key,
        )
    )
    if existing is not None:
        saved = await session.get(
            StageResultModel, stage_id(existing.id, "input"), populate_existing=True
        )
        if (
            saved is None
            or saved.input_hash != digest
            or saved.output_hash != semantic_hash(saved.payload)
            or saved.payload != payload
        ):
            raise IdempotencyConflict(
                "This upload attempt already has different inputs."
            )
        return existing.id
    recent = await session.scalar(
        select(func.count())
        .select_from(AnalysisRunModel)
        .where(
            AnalysisRunModel.workspace_id == workspace_id,
            AnalysisRunModel.idempotency_purpose == PURPOSE,
            AnalysisRunModel.created_at >= func.now() - timedelta(hours=1),
        )
    )
    if recent >= MAX_ATTEMPTS_PER_HOUR:
        raise HTTPException(
            429,
            "This workspace has reached its hourly upload-attempt limit. Try again later.",
        )
    count = await session.scalar(
        select(func.count())
        .select_from(AnalysisRunModel)
        .where(
            AnalysisRunModel.workspace_id == workspace_id,
            AnalysisRunModel.idempotency_purpose == PURPOSE,
            AnalysisRunModel.workflow_status == "received",
        )
    )
    if count >= 3:
        raise HTTPException(429, "This workspace already has three pending uploads.")
    workspace = await session.get(Workspace, workspace_id)
    await ensure_quota_available(session, workspace)
    language = LanguageContext(
        (LanguageCode(choices.source_language),),
        LanguageCode.EN,
        LanguageCode(choices.report_language),
        LocaleTag(choices.format_locale),
    )
    run = AnalysisRun(
        workspace_id,
        user_id,
        ModuleType.FINANCIAL,
        "1",
        VERSION,
        language,
        (),
        (digest,),
        PURPOSE,
        choices.attempt_key,
        allow_empty_sources=True,
    )
    repository = V2Repository(session, workspace_id, persist_stages=True)
    saved, _ = await repository.get_or_create_run(run)
    now = datetime.now(UTC)
    await repository.persist_stage(
        StageResult(
            saved.id,
            "financial_upload_job_input",
            VERSION,
            StageStatus.COMPLETED,
            digest,
            digest,
            "1",
            payload,
            stage_result_id=stage_id(saved.id, "input"),
            started_at=now,
            completed_at=now,
        )
    )
    return saved.id


async def job_status(session, workspace_id, job_id):
    job = await session.scalar(
        select(AnalysisRunModel).where(
            AnalysisRunModel.id == job_id,
            AnalysisRunModel.workspace_id == workspace_id,
            AnalysisRunModel.idempotency_purpose == PURPOSE,
        )
    )
    if job is None:
        raise PermissionError("Upload job is outside the workspace.")
    result = await session.get(
        StageResultModel, stage_id(job.id, "result"), populate_existing=True
    )
    if result is None:
        if job.workflow_status != "received":
            raise InvalidUpload("Upload result evidence is incomplete.")
        return {
            "job_id": str(job.id),
            "status": "pending",
            "message": "Em fila ou em processamento.",
        }
    if (
        result.run_id != job.id
        or result.stage_name != "financial_upload_job_result"
        or result.stage_version != VERSION
        or result.status != "completed"
        or result.input_hash != job.input_hashes[0]
        or result.output_hash != semantic_hash(result.payload)
    ):
        raise InvalidUpload("Upload result evidence differs.")
    return {"job_id": str(job.id), **result.payload}


async def process_one(session, settings, analyst, critic):
    job = await session.scalar(
        select(AnalysisRunModel)
        .where(
            AnalysisRunModel.idempotency_purpose == PURPOSE,
            AnalysisRunModel.workflow_status == "received",
        )
        .order_by(AnalysisRunModel.created_at, AnalysisRunModel.id)
        .with_for_update(skip_locked=True)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if job is None:
        return False
    started = datetime.now(UTC)
    try:
        async with session.begin_nested():
            saved = await session.get(
                StageResultModel, stage_id(job.id, "input"), populate_existing=True
            )
            if (
                saved is None
                or saved.stage_name != "financial_upload_job_input"
                or saved.stage_version != VERSION
                or saved.status != "completed"
                or saved.input_hash != job.input_hashes[0]
                or saved.output_hash != semantic_hash(saved.payload)
                or saved.payload["actor_id"] != str(job.created_by_user_id)
            ):
                raise InvalidUpload("Upload input evidence differs.")
            payload = saved.payload
            if payload["models"] != {
                "analyst": analyst.configuration,
                "critic": critic.configuration,
            }:
                raise InvalidUpload("Upload model configuration has changed.")
            content = base64.b64decode(payload["content"], validate=True)
            if sha256(content).hexdigest() != payload["source_hash"]:
                raise InvalidUpload("Upload source hash differs.")
            choices = GenerateChoices.model_validate(payload["choices"])
            parsed = await run_in_threadpool(
                parse_upload, job.workspace_id, payload["filename"], content, choices
            )
            response = await FinancialProduct(
                session, settings, analyst, critic
            ).generate(parsed, choices, job.created_by_user_id)
    except asyncio.CancelledError:
        raise  # rollback leaves the committed job pending for the next worker
    except (DBAPIError, DatabaseTimeout, PdfDecoderBusy):
        raise  # even a recoverable savepoint timeout must leave the job retryable
    except Exception:
        # Neither provider exception messages nor document content enter public errors.
        response = {
            "status": "failed",
            "message": "Não foi possível concluir a análise. Não foi consumida quota; inicie uma nova tentativa após verificar os dados e a quota disponível.",
        }
    job.workflow_status = (
        "completed"  # execution captured; analysis outcome is separate
    )
    job.started_at, job.completed_at = started, datetime.now(UTC)
    await V2Repository(session, job.workspace_id, persist_stages=True).persist_stage(
        StageResult(
            job.id,
            "financial_upload_job_result",
            VERSION,
            StageStatus.COMPLETED,
            job.input_hashes[0],
            semantic_hash(response),
            "1",
            response,
            stage_result_id=stage_id(job.id, "result"),
            started_at=started,
            completed_at=job.completed_at,
        )
    )
    await session.flush()
    return True


async def worker_loop(sessions, settings_factory, models_factory):
    while True:
        try:
            settings = settings_factory()
            if not settings.enabled or not settings.financial_product_enabled:
                await asyncio.sleep(2)
                continue
            async with models_factory() as models, sessions.begin() as session:
                processed = await process_one(session, settings, *models)
            if not processed:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(2)  # transient DB failures leave jobs pending
