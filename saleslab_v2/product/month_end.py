"""Source-bound, non-persisted monthly preparation; no models or product quota."""

import calendar
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal
from uuid import uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from saleslab_v2.domain.idempotency import canonicalize, semantic_hash
from saleslab_v2.domain.value_objects import Period
from saleslab_v2.month_end.contracts import (
    ClosePolicy,
    ComparisonPlan,
    MonthEndRequest,
    MovementExpectation,
    TaskConfirmation,
)
from saleslab_v2.month_end.evaluator import evaluate as evaluate_close
from saleslab_v2.pipeline.contracts import InputRef
from saleslab_v2.pipeline.evaluator import evaluate
from saleslab_v2.reports.contracts import REPORT_LOCALES
from saleslab_v2.reports.presentation import format_value

from .contracts import InvalidUpload, UploadChoices
from .month_end_catalog import MAX_REQUIRED_RECONCILIATIONS
from .planning import prepare_upload

VERSION = "financial-upload-month-end-preview-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Comparison(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    current: str = Field(min_length=1, max_length=200)
    prior: str = Field(min_length=1, max_length=200)
    relation: Literal["month_on_month", "month_year_on_year", "ytd_year_on_year"]


class Movement(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    current: str = Field(min_length=1, max_length=200)
    prior: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["nonzero_value", "changed_from_prior_month"]


class Confirmation(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)
    status: Literal["complete", "incomplete"]
    evidence: list[str] = Field(max_length=20)


class MonthEndChoices(StrictModel):
    version: Literal["1"] = "1"
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    policy_id: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=100)
    currency: str = Field(min_length=3, max_length=3)
    fiscal_year_start_month: int = Field(ge=1, le=12)
    required_statements: list[str] = Field(min_length=1, max_length=3)
    required_reconciliations: list[str] = Field(
        min_length=1, max_length=MAX_REQUIRED_RECONCILIATIONS
    )
    required_tasks: list[str] = Field(min_length=1, max_length=20)
    required_comparisons: list[str] = Field(min_length=1, max_length=3)
    absolute_variation_threshold: str = Field(min_length=1, max_length=64)
    percentage_variation_threshold: str = Field(min_length=1, max_length=64)
    materiality_basis: str = Field(min_length=1, max_length=100)
    comparisons: list[Comparison] = Field(default_factory=list, max_length=50)
    movements: list[Movement] = Field(default_factory=list, max_length=50)
    confirmations: list[Confirmation] = Field(default_factory=list, max_length=20)

    @field_validator("absolute_variation_threshold", "percentage_variation_threshold")
    @classmethod
    def validate_threshold(cls, value):
        return UploadChoices.validate_tolerance(value)


def prepare_month_end(parsed, choices, close, user_id):
    try:
        return _prepare(parsed, choices, close, user_id)
    except (ValueError, KeyError, OverflowError) as exc:
        if isinstance(exc, InvalidUpload):
            raise
        raise InvalidUpload(
            "Check the explicit monthly policy and source row bindings."
        ) from exc


def _prepare(parsed, choices, close, user_id):
    request, preview = prepare_upload(parsed, choices, user_id, "preview")
    if close.preview_hash != preview["preview_hash"]:
        raise InvalidUpload("The source or validation choices changed; validate again.")
    start_month = (choices.fiscal_year_end_month or 12) % 12 + 1
    if close.fiscal_year_start_month != start_month:
        raise InvalidUpload("The close policy must match the source fiscal calendar.")
    year, month = map(int, close.target_month.split("-"))
    period = Period(
        date(year, month, 1),
        date(year, month, calendar.monthrange(year, month)[1]),
        close.target_month,
        "month",
        comparison_basis="current",
    )
    policy = ClosePolicy(
        close.policy_id,
        close.policy_version,
        close.currency,
        "consolidated",
        (),
        close.fiscal_year_start_month,
        tuple(close.required_statements),
        tuple(close.required_reconciliations),
        tuple(close.required_tasks),
        tuple(close.required_comparisons),
        Decimal(close.absolute_variation_threshold),
        Decimal(close.percentage_variation_threshold),
        close.materiality_basis,
    )
    locations = {r.source_reference for r in parsed.batch.rows}

    def ref(location):
        if location not in locations:
            raise InvalidUpload("A monthly evidence reference is outside this source.")
        return InputRef(location, parsed.batch.document.source_id)

    binding = semantic_hash(
        {
            "schema": VERSION,
            "workspace_id": request.workspace_id,
            "user_id": user_id,
            "source_preview_hash": preview["preview_hash"],
            "choices": close.model_dump(),
        }
    )
    pipeline_id = uuid5(request.workspace_id, "preview:" + request.fingerprint)
    close_id = uuid5(request.workspace_id, "month-end-preview:" + binding)
    monthly = MonthEndRequest(
        request.workspace_id,
        user_id,
        pipeline_id,
        period,
        policy,
        tuple(
            ComparisonPlan(c.key, ref(c.current), ref(c.prior), c.relation)
            for c in close.comparisons
        ),
        tuple(
            MovementExpectation(
                m.key, ref(m.current), m.kind, ref(m.prior) if m.prior else None
            )
            for m in close.movements
        ),
        tuple(
            TaskConfirmation(c.task_id, c.status, tuple(ref(r) for r in c.evidence))
            for c in close.confirmations
        ),
    )
    pipeline = evaluate(request, pipeline_id, datetime.now(UTC))
    checkpoint = canonicalize(
        {
            "quality": pipeline.payloads[3],
            "reconciliations": [r.payload() for r in pipeline.reconciliations],
        }
    )
    result, _ = evaluate_close(
        monthly,
        pipeline.payloads[0],
        checkpoint,
        run_id=close_id,
        language=choices.report_language,
    )

    def display(value, unit, currency):
        if value is None:
            return None
        with localcontext() as context:
            context.prec = 4096  # More than the bounded source-cell precision/scale.
            rounded = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format_value(
            {"value": str(rounded), "unit": unit, "currency": currency},
            REPORT_LOCALES[choices.report_language],
        )

    presentation = {
        "decimal_places": 2,
        "rounding": "ROUND_HALF_UP",
        "comparisons": [
            {
                "key": c["key"],
                **{
                    k: display(c[k], "monetary", c["currency"])
                    for k in ("current_value", "prior_value", "delta")
                },
                "percentage": display(c["percentage"], "percentage", None),
            }
            for c in result["comparisons"]
        ],
    }
    payload = {
        "schema": VERSION,
        "binding_hash": binding,
        "source_preview_hash": preview["preview_hash"],
        "source_hash": parsed.batch.document.content_hash,
        "filename": parsed.batch.document.filename,
        "upload_choices": choices.contract_payload(),
        "close_choices": close.model_dump(),
        "original_rows": list(parsed.original_rows),
        "source_import": parsed.import_manifest,
        "result": result,
        "presentation": presentation,
        "persisted": False,
        "quota_consumed": False,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return {**payload, "payload_hash": semantic_hash(payload)}
