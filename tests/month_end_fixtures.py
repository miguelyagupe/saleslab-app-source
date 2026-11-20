"""Synthetic monthly income/balance inputs; no claimed business acceptance limits."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

from pipeline_fixtures import request

from saleslab_v2.domain.value_objects import Period
from saleslab_v2.month_end.contracts import (
    ClosePolicy,
    ComparisonPlan,
    MonthEndRequest,
    MovementExpectation,
    TaskConfirmation,
)
from saleslab_v2.pipeline.contracts import InputRef


def monthly(value=None, *, language="en"):
    value = value or request(language=language)
    batch = value.sources[0]
    month = {
        "pt": ("outubro", "setembro"),
        "en": ("October", "September"),
        "es": ("octubre", "septiembre"),
        "fr": ("octobre", "septembre"),
    }[language]
    rows = tuple(replace(r, period_label=month[0] + " 2026") for r in batch.rows)
    revenue = next(r for r in rows if r.source_reference == "revenue")
    extra = (
        replace(
            revenue,
            source_reference="revenue_prior_month",
            period_label=month[1] + " 2026",
            comparison_basis="comparative",
            text="1100",
        ),
        replace(
            revenue,
            source_reference="revenue_prior_year",
            period_label=month[0] + " 2025",
            comparison_basis="comparative",
            text="800",
        ),
        replace(
            revenue,
            source_reference="revenue_ytd",
            period_label="YTD " + month[0] + " 2026",
            text="10000",
        ),
        replace(
            revenue,
            source_reference="revenue_ytd_prior",
            period_label="YTD " + month[0] + " 2025",
            comparison_basis="comparative",
            text="8000",
        ),
    )
    return replace(value, sources=(replace(batch, rows=(*rows, *extra)),))


def close_request(value, pipeline_run_id=None):
    sid = value.sources[0].document.source_id

    def ref(key):
        return InputRef(key, sid)

    return MonthEndRequest(
        value.workspace_id,
        value.user_id,
        pipeline_run_id or uuid4(),
        Period(
            date(2026, 10, 1),
            date(2026, 10, 31),
            "October 2026",
            "month",
            comparison_basis="current",
        ),
        ClosePolicy(
            "synthetic-monthly",
            "1",
            "EUR",
            "consolidated",
            (),
            1,
            ("income_statement", "balance_sheet"),
            ("REC-BS-001",),
            ("review_inputs",),
            ("month_on_month", "month_year_on_year", "ytd_year_on_year"),
            Decimal(10000),
            Decimal(50),
            "synthetic acceptance test only",
        ),
        (
            ComparisonPlan(
                "revenue-mom",
                ref("revenue"),
                ref("revenue_prior_month"),
                "month_on_month",
            ),
            ComparisonPlan(
                "revenue-yoy",
                ref("revenue"),
                ref("revenue_prior_year"),
                "month_year_on_year",
            ),
            ComparisonPlan(
                "revenue-ytd",
                ref("revenue_ytd"),
                ref("revenue_ytd_prior"),
                "ytd_year_on_year",
            ),
        ),
        (
            MovementExpectation(
                "net-income-present", ref("net_income"), "nonzero_value"
            ),
        ),
        (
            TaskConfirmation(
                "review_inputs", "complete", (ref("assets"), ref("revenue"))
            ),
        ),
    )
