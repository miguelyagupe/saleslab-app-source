"""Authenticated opt-in routes; no client-supplied workspace or actor identities."""

# ruff: noqa: B008 -- FastAPI resolves these dependency defaults.

import base64
import json
import os
from uuid import UUID

import anthropic
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from auth import get_current_session, get_remaining_quota
from database import get_db
from models import Analysis
from saleslab_v2.analyst.provider import AnthropicFinancialModel
from saleslab_v2.audit.provider import AnthropicFinancialCritic
from saleslab_v2.config import V2Settings
from saleslab_v2.domain.idempotency import IdempotencyConflict
from saleslab_v2.normalization.currencies import ISO_4217_CODES
from saleslab_v2.persistence.models import AnalysisRunModel
from saleslab_v2.reports.contracts import InvalidReport

from .capex_source import CapexReviewRequired
from .contracts import (
    NORMALIZATION_VERSIONS,
    PLANNING_VERSIONS,
    GenerateChoices,
    InvalidUpload,
    UploadChoices,
)
from .debt_source import DebtReviewRequired
from .growth_source import GrowthReviewRequired
from .ingestion import MAX_BYTES, parse_upload
from .jobs import enqueue, job_status
from .month_end import MonthEndChoices, prepare_month_end
from .month_end_catalog import reconciliation_catalog
from .month_end_csv import checklist_csv
from .month_end_saved import PURPOSE as MONTHLY_SNAPSHOT_PURPOSE
from .month_end_saved import VERSION as MONTHLY_SNAPSHOT_VERSION
from .month_end_saved import (
    InvalidMonthlySnapshot,
    MonthlySnapshots,
    SaveMonthEndChoices,
    readable,
)
from .pdf_contracts import PdfLayoutRequest
from .pdf_source import PdfReviewRequired, pdf_available
from .pilot import sample
from .planning import prepare_upload
from .roic_source import RoicReviewRequired
from .service import FinancialProduct, available
from .tabular import inspect_upload
from .working_capital_source import WorkingCapitalReviewRequired

router = APIRouter(prefix="/financial-v2")


def get_settings():
    return V2Settings.from_mapping(os.environ)


async def get_models():
    analyst, critic = os.environ.get("SALESLAB_V2_ANALYST_MODEL"), os.environ.get(
        "SALESLAB_V2_CRITIC_MODEL"
    )
    if not analyst or not critic:
        raise HTTPException(503, "The validated financial models are not configured.")
    async with anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=0
    ) as client:
        yield AnthropicFinancialModel(client, analyst), AnthropicFinancialCritic(
            client, critic
        )


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise InvalidUpload("Duplicate validation fields are not accepted.")
        result[key] = value
    return result


def choices_from_json(raw, model):
    if len(raw.encode()) > 64_000:
        raise InvalidUpload("Validation choices exceed the limit.")
    try:
        return model.model_validate(json.loads(raw, object_pairs_hook=_pairs))
    except (TypeError, ValueError) as exc:
        raise InvalidUpload("Check the validation choices and upload format.") from exc


async def actor(db, token, settings, *, generation=True):
    session = await get_current_session(db, token)
    if generation and not available(settings, session["workspace"].id, session["role"]):
        raise HTTPException(
            403, "Validated financial upload is not enabled for this workspace."
        )
    return session


async def parsed_file(file, choices, workspace_id):
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise InvalidUpload("The file exceeds 5 MB.")
    parsed = await run_in_threadpool(
        parse_upload, workspace_id, file.filename or "", content, choices
    )
    return parsed, content


@router.get("/capabilities")
async def capabilities(
    x_token: str | None = Header(default=None), db: AsyncSession = Depends(get_db)
):
    session = await get_current_session(db, x_token)
    month_end_enabled = False
    monthly_read_enabled = False
    monthly_save_enabled = False
    try:
        settings = get_settings()
        month_end_enabled = settings.financial_month_end_enabled
        monthly_read_enabled = readable(
            settings, session["workspace"].id, session["role"]
        )
        monthly_save_enabled = month_end_enabled and available(
            settings, session["workspace"].id, session["role"]
        )
        enabled = available(
            settings, session["workspace"].id, session["role"]
        ) and bool(
            os.environ.get("SALESLAB_V2_ANALYST_MODEL")
            and os.environ.get("SALESLAB_V2_CRITIC_MODEL")
        )
    except ValueError:
        enabled = False
    has_reports = (
        await db.scalar(
            select(Analysis.id)
            .where(
                Analysis.workspace_id == session["workspace"].id,
                Analysis.module_type == "financial",
                Analysis.result_json["schema"].astext == "financial-v2",
            )
            .limit(1)
        )
        if session["role"] in {"owner", "manager"}
        else None
    )
    has_monthly = (
        await db.scalar(
            select(AnalysisRunModel.id)
            .where(
                AnalysisRunModel.workspace_id == session["workspace"].id,
                AnalysisRunModel.idempotency_purpose == MONTHLY_SNAPSHOT_PURPOSE,
                AnalysisRunModel.pipeline_version == MONTHLY_SNAPSHOT_VERSION,
            )
            .limit(1)
        )
        if monthly_read_enabled
        else None
    )
    return JSONResponse(
        {
            "enabled": bool(enabled),
            "month_end_snapshot_versions": ["1"] if monthly_read_enabled else [],
            "month_end_checklist_csv_versions": ["1"] if monthly_read_enabled else [],
            "month_end_save_versions": ["1"] if monthly_save_enabled else [],
            "month_end_snapshots_available": has_monthly is not None,
            "reports_available": has_reports is not None,
            "formats": ["csv", "xlsx", *(["pdf"] if pdf_available() else [])],
            "max_rows": 500,
            "planning_versions": list(PLANNING_VERSIONS),
            "normalization_versions": list(NORMALIZATION_VERSIONS),
            "accounting_parentheses_supported": True,
            "fiscal_calendar_supported": True,
            "source_metadata_supported": True,
            "source_currencies": sorted(ISO_4217_CODES),
            "month_end_preview_versions": (["1"] if month_end_enabled else []),
            **(
                {"month_end_reconciliation_catalog": reconciliation_catalog()}
                if month_end_enabled
                else {}
            ),
            "tabular_import_versions": ["1"],
            "pdf_import_versions": ["1"] if pdf_available() else [],
        },
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/inspect")
async def inspect_source(
    file: UploadFile = File(...),
    delimiter: str | None = Form(default=None),
    pdf_layout: str | None = Form(default=None),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    await actor(db, x_token, settings)
    content = await file.read(MAX_BYTES + 1)
    try:
        layout = (
            choices_from_json(pdf_layout, PdfLayoutRequest)
            if pdf_layout is not None
            else None
        )
        result = await run_in_threadpool(
            inspect_upload, file.filename or "", content, delimiter, layout
        )
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    except InvalidUpload as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/history")
async def financial_history(
    before: UUID | None = None,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    session = await get_current_session(db, x_token)
    if session["role"] not in {"owner", "manager"}:
        raise HTTPException(403, "Workspace membership required.")
    query = select(Analysis).where(
        Analysis.workspace_id == session["workspace"].id,
        Analysis.module_type == "financial",
        Analysis.result_json["schema"].astext == "financial-v2",
    )
    if before:
        cursor = await db.scalar(query.where(Analysis.id == before))
        if cursor is None:
            raise HTTPException(404, "History cursor unavailable.")
        query = query.where(
            tuple_(Analysis.created_at, Analysis.id) < (cursor.created_at, cursor.id)
        )
    records = (
        await db.scalars(
            query.order_by(Analysis.created_at.desc(), Analysis.id.desc()).limit(51)
        )
    ).all()
    return {
        "reports": [
            {
                "id": str(r.id),
                "run_id": r.result_json["run_id"],
                "period": r.period_label,
                "created_at": r.created_at.isoformat(),
            }
            for r in records[:50]
        ],
        "next_cursor": str(records[49].id) if len(records) > 50 else None,
    }


@router.post("/preview")
async def preview(
    options: str = Form(...),
    file: UploadFile = File(...),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings)
    try:
        choices = choices_from_json(options, UploadChoices)
        parsed, _ = await parsed_file(file, choices, session["workspace"].id)
        _, result = await run_in_threadpool(
            prepare_upload, parsed, choices, session["user"].id, "preview"
        )
        return result
    except (
        PdfReviewRequired,
        GrowthReviewRequired,
        DebtReviewRequired,
        CapexReviewRequired,
        WorkingCapitalReviewRequired,
        RoicReviewRequired,
    ) as exc:
        return JSONResponse(exc.payload, headers={"Cache-Control": "private, no-store"})
    except InvalidUpload as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/month-end/preview")
async def month_end_preview(
    options: str = Form(...),
    month_end: str = Form(...),
    file: UploadFile = File(...),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings)
    if not settings.financial_month_end_enabled:
        raise HTTPException(403, "Monthly close preparation is disabled.")
    try:
        choices = choices_from_json(options, UploadChoices)
        close = choices_from_json(month_end, MonthEndChoices)
        parsed, _ = await parsed_file(file, choices, session["workspace"].id)
        result = await run_in_threadpool(
            prepare_month_end, parsed, choices, close, session["user"].id
        )
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    except InvalidUpload as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/month-end/snapshots")
async def save_monthly_snapshot(
    options: str = Form(...),
    month_end: str = Form(...),
    save: str = Form(...),
    file: UploadFile = File(...),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings)
    try:
        choices = choices_from_json(options, UploadChoices)
        close = choices_from_json(month_end, MonthEndChoices)
        confirmation = choices_from_json(save, SaveMonthEndChoices)
        parsed, _ = await parsed_file(file, choices, session["workspace"].id)
        result, created = await MonthlySnapshots(db, settings).save(
            parsed, choices, close, confirmation, session["user"].id
        )
        await db.commit()
        return JSONResponse(
            result,
            status_code=201 if created else 200,
            headers={"Cache-Control": "private, no-store"},
        )
    except (
        InvalidUpload,
        IdempotencyConflict,
        InvalidMonthlySnapshot,
        PermissionError,
    ) as exc:
        await db.rollback()
        raise HTTPException(
            (
                403
                if isinstance(exc, PermissionError)
                else 422 if isinstance(exc, InvalidUpload) else 409
            ),
            str(exc),
        ) from exc
    except HTTPException:
        await db.rollback()
        raise


@router.get("/month-end/snapshots")
async def monthly_snapshot_history(
    cursor: UUID | None = None,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings, generation=False)
    try:
        result = await MonthlySnapshots(db, settings).history(
            session["workspace"].id, session["user"].id, cursor
        )
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    except (PermissionError, InvalidMonthlySnapshot) as exc:
        raise HTTPException(
            404 if isinstance(exc, PermissionError) else 409,
            "Monthly snapshots are unavailable or cannot be verified.",
        ) from exc


@router.get("/month-end/snapshots/{snapshot_id}")
async def monthly_snapshot(
    snapshot_id: UUID,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings, generation=False)
    try:
        result = await MonthlySnapshots(db, settings).read(
            session["workspace"].id, session["user"].id, snapshot_id
        )
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    except (PermissionError, InvalidMonthlySnapshot) as exc:
        raise HTTPException(
            404 if isinstance(exc, PermissionError) else 409,
            "The monthly snapshot is unavailable or cannot be verified.",
        ) from exc


@router.get("/month-end/snapshots/{snapshot_id}/checklist.csv")
async def monthly_snapshot_csv(
    snapshot_id: UUID,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings, generation=False)
    try:
        snapshot = await MonthlySnapshots(db, settings).read(
            session["workspace"].id, session["user"].id, snapshot_id
        )
        content = await run_in_threadpool(checklist_csv, snapshot)
        return Response(
            content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": (
                    f'attachment; filename="saleslab-month-end-{snapshot_id}.csv"'
                ),
            },
        )
    except (PermissionError, InvalidMonthlySnapshot) as exc:
        raise HTTPException(
            404 if isinstance(exc, PermissionError) else 409,
            "The monthly snapshot is unavailable or cannot be verified.",
        ) from exc


@router.get("/sample")
async def public_sample(
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    await actor(db, x_token, settings)
    try:
        return sample()
    except (ValueError, OSError) as exc:
        raise HTTPException(
            503, "The public example is temporarily unavailable."
        ) from exc


@router.post("/generate")
async def generate(
    options: str = Form(...),
    file: UploadFile = File(...),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
    models=Depends(get_models),
):
    session = await actor(db, x_token, settings)
    try:
        choices = choices_from_json(options, GenerateChoices)
        parsed, content = await parsed_file(file, choices, session["workspace"].id)
        job_id = await enqueue(
            db,
            settings,
            parsed,
            choices,
            content,
            session["user"].id,
            {"analyst": models[0].configuration, "critic": models[1].configuration},
        )
        await db.commit()
        return JSONResponse(
            {"job_id": str(job_id), "status": "pending"}, status_code=202
        )
    except (InvalidUpload, IdempotencyConflict, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            (
                409
                if isinstance(exc, IdempotencyConflict)
                else 403 if isinstance(exc, PermissionError) else 422
            ),
            str(exc),
        ) from exc


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: UUID,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings, generation=False)
    try:
        result = await job_status(db, session["workspace"].id, job_id)
        if result["status"] != "pending":
            result["analyses_remaining"] = await get_remaining_quota(
                db, session["workspace"]
            )
        return result
    except (PermissionError, InvalidUpload) as exc:
        raise HTTPException(404, "The upload job is unavailable.") from exc


@router.get("/reports/{run_id}/{channel}")
async def report(
    run_id: UUID,
    channel: str,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
):
    session = await actor(db, x_token, settings, generation=False)
    if channel not in {"html", "pdf", "json"}:
        raise HTTPException(404, "Unknown report format.")
    try:
        compiled = await FinancialProduct(db, settings).read_report(
            session["workspace"].id, session["user"].id, run_id
        )
    except (InvalidReport, IdempotencyConflict, PermissionError, ValueError) as exc:
        raise HTTPException(
            404, "The report is unavailable or its evidence requires review."
        ) from exc
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if channel == "json":
        return JSONResponse(compiled.payload, headers=headers)
    if channel == "html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
        )
        return HTMLResponse(compiled.artifacts["html"], headers=headers)
    headers["Content-Disposition"] = (
        f'attachment; filename="saleslab-financial-{run_id}.pdf"'
    )
    return Response(
        base64.b64decode(compiled.artifacts["pdf_base64"], validate=True),
        media_type="application/pdf",
        headers=headers,
    )
