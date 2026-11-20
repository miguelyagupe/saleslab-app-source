"""Explicit fiscal calendars use the existing normalizer and remain source-bound."""

import calendar
import json
from datetime import date
from uuid import uuid4

import pytest
from product_fixtures import csv_content, upload_case

from saleslab_v2.product.api import choices_from_json
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.planning import prepare_upload


def choices(**updates):
    return UploadChoices(
        source_language="en", format_locale="en-GB", report_language="pt", **updates
    )


def fiscal_content(label="FY 2026", prior=None):
    changes = {
        c: {"period": label}
        for c in (
            "revenue",
            "net_income",
            "total_assets",
            "total_liabilities",
            "equity",
        )
    }
    extra = (
        []
        if prior is None
        else [
            (
                "revenue",
                "800",
                prior,
                "income_statement",
                "consolidated",
                "EUR",
                "raw",
                "comparative",
            )
        ]
    )
    return csv_content(changes=changes, extra=extra)


@pytest.mark.parametrize("month", range(1, 13))
@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
def test_explicit_fiscal_year_all_closing_months_preserves_source_and_dates(
    month, language
):
    opts = choices(fiscal_year_end_month=month).model_copy(
        update={"report_language": language}
    )
    parsed, preview, _, _, _ = upload_case(
        choices=opts, content=fiscal_content(prior="FY 2025")
    )
    start_month = month % 12 + 1
    start_year = 2026 if month == 12 else 2025
    revenue = next(
        r
        for r in preview["rows"]
        if r["concept_id"] == "revenue" and r["period"]["comparison_basis"] == "current"
    )
    period = revenue["period"]
    assert period["start_date"] == date(start_year, start_month, 1).isoformat()
    assert (
        period["end_date"]
        == date(2026, month, calendar.monthrange(2026, month)[1]).isoformat()
    )
    assert (period["fiscal_year"], period["fiscal_year_end_month"]) == (2026, month)
    assert revenue["original_input"]["period"] == "FY 2026"
    assert all(r.fiscal_year_end_month == month for r in parsed.batch.rows)
    assert preview["ready"]
    growth = next(
        m
        for m in preview["calculated_metrics"]
        if m["metric_id"] == "revenue_growth_reported_pct"
    )
    assert growth["exact_value"] == "25.00"
    assert growth["period"] == period


@pytest.mark.parametrize(
    "label,start,end,granularity",
    (
        ("FY 2026 Q1", "2025-07-01", "2025-09-30", "quarter"),
        ("FY 2026 Q2", "2025-10-01", "2025-12-31", "quarter"),
        ("FY 2026 Q3", "2026-01-01", "2026-03-31", "quarter"),
        ("FY 2026 Q4", "2026-04-01", "2026-06-30", "quarter"),
        ("FY 2026 Q2 YTD", "2025-07-01", "2025-12-31", "ytd"),
        ("exercício 2026 T1", "2025-07-01", "2025-09-30", "quarter"),
        ("ejercicio 2026 T1", "2025-07-01", "2025-09-30", "quarter"),
        ("exercice 2026 T1", "2025-07-01", "2025-09-30", "quarter"),
    ),
)
def test_fiscal_quarter_ytd_and_multilingual_markers_keep_existing_semantics(
    label, start, end, granularity
):
    _, preview, _, _, _ = upload_case(
        choices=choices(fiscal_year_end_month=6), content=fiscal_content(label)
    )
    assert preview["ready"]
    for row in preview["rows"]:
        p = row["period"]
        assert (p["start_date"], p["end_date"], p["granularity"]) == (
            start,
            end,
            granularity,
        )
        assert row["original_input"]["period"] == label


@pytest.mark.parametrize(
    "label,month,code",
    (
        ("FY 2026", None, "period.fiscal_calendar_required"),
        ("Q1 2026", 6, "period.quarter_basis_ambiguous"),
        ("Q1 2026", 12, "period.quarter_basis_ambiguous"),
        ("YTD September 2026", 6, "period.ytd_basis_ambiguous"),
        ("FY March 2026", 6, "period.fiscal_month_basis_ambiguous"),
        ("FY 2026 YTD", 6, "period.ytd_cutoff_required"),
    ),
)
def test_calendar_choice_does_not_resolve_ambiguous_periods(label, month, code):
    _, preview, _, _, _ = upload_case(
        choices=choices(fiscal_year_end_month=month), content=fiscal_content(label)
    )
    assert not preview["ready"]
    assert all(code in [i["code"] for i in r["issues"]] for r in preview["rows"])
    assert not preview["planned_metrics"]


@pytest.mark.parametrize("invalid", (0, 13, -1, True, False, 6.0, "6", "", [], {}))
def test_month_requires_strict_bounded_integer(invalid):
    with pytest.raises(InvalidUpload):
        choices_from_json(
            json.dumps({**choices().model_dump(), "fiscal_year_end_month": invalid}),
            UploadChoices,
        )


def test_omitted_and_null_keep_legacy_contract_and_bindings_while_month_changes_are_bound():
    workspace, user = uuid4(), uuid4()
    content = fiscal_content()
    hashes, fingerprints = {}, {}
    for month in (None, 6, 12):
        opts = choices(fiscal_year_end_month=month)
        parsed, preview, _, _, _ = upload_case(
            workspace, user, choices=opts, content=content
        )
        request, _ = prepare_upload(parsed, opts, user, "preview")
        hashes[month], fingerprints[month] = (
            preview["preview_hash"],
            request.fingerprint,
        )
        if month is None:
            assert "fiscal_year_end_month" not in opts.contract_payload()
        else:
            assert opts.contract_payload()["fiscal_year_end_month"] == month
        assert parsed.original_rows[0]["period"] == "FY 2026"
    assert len(set(hashes.values())) == len(set(fingerprints.values())) == 3
    parsed, preview, _, _, _ = upload_case(
        workspace, user, choices=choices(), content=content
    )
    assert preview["preview_hash"] == hashes[None]
    assert "fiscal_year_end_month" not in choices().contract_payload()
    # Calendar years remain calendar years even with an explicit fiscal choice.
    _, preview, _, _, _ = upload_case(choices=choices(fiscal_year_end_month=6))
    assert all(r["period"]["fiscal_year"] is None for r in preview["rows"])
    assert all(r["period"]["start_date"] == "2026-01-01" for r in preview["rows"])


def test_unsupported_shifted_date_boundary_is_rejected_as_invalid_upload():
    with pytest.raises(InvalidUpload, match="Row 2"):
        upload_case(
            choices=choices(fiscal_year_end_month=6), content=fiscal_content("FY 0001")
        )


def test_mixed_fiscal_and_calendar_comparatives_remain_blocked():
    _, preview, _, _, _ = upload_case(
        choices=choices(fiscal_year_end_month=12), content=fiscal_content(prior="2025")
    )
    growth = next(
        m
        for m in preview["calculated_metrics"]
        if m["metric_id"] == "revenue_growth_reported_pct"
    )
    assert growth["status"] == "unavailable"
    assert "pipeline.metric_invalid" in growth["reason_codes"]
    gap = next(
        m
        for m in preview["metric_availability"]
        if m["metric_id"] == "revenue_growth_reported_pct"
    )
    assert gap["status"] == "blocked"
    assert not preview["ready"]
