"""Plan 6 cash bridge: source-only movements, explicit anchors and legacy replay."""

from decimal import Decimal
from uuid import UUID

import pytest
from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v2 import FINANCIAL_TERMS_V2
from saleslab_v2.i18n.financial_v3 import FINANCIAL_TERMS_V3
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.planning import prepare_upload

TERMS = {t.concept_id: t for t in FINANCIAL_TERMS_V3}


def choices(**updates):
    return UploadChoices(
        **{
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "planning_version": "6",
            "normalization_version": "2",
            "target_period": "2026",
            **updates,
        }
    )


def cash_rows(
    *,
    language="en",
    scale="raw",
    opening="2025",
    closing="2026",
    flow="2026",
    end="320",
    fx="0"
):
    def row(concept, value, period):
        t = TERMS[concept]
        return (
            t.labels[LanguageCode(language)],
            value,
            period,
            t.statement_type,
            "consolidated",
            "EUR",
            scale,
            "current",
        )

    return [
        row("cash_and_equivalents", "100", opening),
        row("cash_and_equivalents", end, closing),
        row("cfo", "300", flow),
        row("cfi", "-120", flow),
        row("cff", "40", flow),
        row("cash_fx_other", fx, flow),
    ]


def preview(rows=None, **opts):
    return upload_case(
        content=csv_content(
            language=opts.get("source_language", "en"),
            extra=cash_rows() if rows is None else rows,
        ),
        choices=choices(**opts),
    )[1]


def check(value):
    return next(
        r
        for r in value["validation_summary"]["reconciliations"]
        if r["rule_id"] == "REC-CF-001"
    )


@pytest.mark.parametrize(
    "language,locale",
    (("pt", "pt-PT"), ("en", "en-GB"), ("es", "es-ES"), ("fr", "fr-FR")),
)
@pytest.mark.parametrize("report_language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize(
    "scale,multiplier", (("raw", 1), ("thousands", 1000), ("millions", 1000000))
)
def test_six_explicit_components_reconcile_with_all_sources_and_catalog_pin(
    language, locale, report_language, scale, multiplier
):
    v = preview(
        cash_rows(language=language, scale=scale),
        source_language=language,
        format_locale=locale,
        report_language=report_language,
    )
    assert v["ready"]
    c = check(v)
    assert c["status"] == v["cash_reconciliation"]["status"] == "pass"
    assert Decimal(c["expected"]) == Decimal(c["observed"]) == 320 * multiplier
    assert c["source_references"] == [
        "csv:10",
        "csv:11",
        "csv:12",
        "csv:7",
        "csv:8",
        "csv:9",
    ]
    metric = next(
        m for m in v["calculated_metrics"] if m["metric_id"] == "cash_fx_other"
    )
    assert metric["status"] == "calculated"
    assert (
        metric["calculation_evidence"]["canonical_expression"]
        == "reported_cash_fx_other"
    )
    assert metric["source_references"] == ["csv:12"]
    assert all(
        r["terminology_version"] == "financial-terminology-v3" for r in v["rows"]
    )


@pytest.mark.parametrize(
    "end,tolerance,status,ready",
    (
        ("321", None, "blocking", False),
        ("321", "1", "rounding_pass", True),
        ("321.01", "1", "blocking", False),
        ("320", None, "pass", True),
    ),
)
def test_cash_tolerance_is_separate_from_balance_allowance(
    end, tolerance, status, ready
):
    v = preview(
        cash_rows(end=end), cash_rounding_tolerance=tolerance, rounding_tolerance="1000"
    )
    assert check(v)["status"] == status
    assert v["ready"] == ready
    assert Decimal(check(v)["absolute_delta"]) == abs(Decimal(end) - 320)


@pytest.mark.parametrize("index", range(6))
def test_missing_component_is_never_imputed(index):
    rows = cash_rows()
    rows.pop(index)
    v = preview(rows)
    assert v["cash_reconciliation"]["status"] == "not_planned"
    assert not any(
        r["rule_id"] == "REC-CF-001" for r in v["validation_summary"]["reconciliations"]
    )
    if index == 5:
        assert "cash_fx_other" not in v["planned_metrics"]
        assert v["cash_reconciliation"]["requirements"][0]["role"] == "fx_other"


@pytest.mark.parametrize("index", range(6))
def test_duplicate_operands_are_not_selected(index):
    rows = cash_rows()
    rows.append(rows[index])
    v = preview(rows)
    assert v["cash_reconciliation"]["status"] == "not_planned"
    assert any(
        r["status"] == "ambiguous" for r in v["cash_reconciliation"]["requirements"]
    )


@pytest.mark.parametrize(
    "index,field,value",
    (
        (0, 7, "comparative"),
        (0, 2, "2024"),
        (1, 2, "2027"),
        (3, 2, "2025"),
        (5, 7, "comparative"),
    ),
)
def test_wrong_basis_or_anchor_is_not_automatically_rewritten(index, field, value):
    rows = cash_rows()
    row = list(rows[index])
    row[field] = value
    rows[index] = row
    v = preview(rows)
    assert v["cash_reconciliation"]["status"] == "not_planned"


@pytest.mark.parametrize("index", (0, 1, 3, 4, 5))
@pytest.mark.parametrize("field,value", ((5, "USD"), (4, "standalone")))
def test_currency_and_scope_mismatch_are_blocked_by_existing_rule(index, field, value):
    rows = cash_rows()
    row = list(rows[index])
    row[field] = value
    rows[index] = row
    if field == 4:
        with pytest.raises(InvalidUpload, match="consolidated scope"):
            preview(rows)
        return
    v = preview(rows)
    assert not v["ready"]
    assert check(v)["status"] in {"not_evaluated", "blocking"}


def test_ambiguous_sign_requires_confirmation_and_explicit_zero_is_valid():
    v = preview(cash_rows(fx="(0)"))
    assert v["cash_reconciliation"]["status"] == "not_planned"
    assert any(
        r["role"] == "fx_other" and r["status"] == "needs_confirmation"
        for r in v["cash_reconciliation"]["requirements"]
    )
    assert (
        check(preview(cash_rows(fx="(0)"), accounting_parentheses=True))["status"]
        == "pass"
    )


@pytest.mark.parametrize(
    "opening,flow,month",
    (
        ("FY 2025", "FY 2026", 6),
        ("January 2026", "February 2026", None),
        ("2025 Q4", "2026 Q1", None),
    ),
)
def test_explicit_fiscal_monthly_and_quarter_anchors(opening, flow, month):
    v = preview(
        cash_rows(opening=opening, closing=flow, flow=flow),
        target_period=flow,
        fiscal_year_end_month=month,
    )
    assert check(v)["status"] == "pass"


def test_no_selected_period_never_guesses_current_year():
    v = preview(target_period="")
    assert not v["ready"]
    assert v["cash_reconciliation"]["status"] == "period_required"


@pytest.mark.parametrize(
    "value", ("NaN", "Infinity", "-1", "1e64", "1e-64", "x", "0" * 65, 1, True)
)
def test_cash_tolerance_has_bounded_strict_finite_decimal_contract(value):
    with pytest.raises(ValueError):
        choices(cash_rounding_tolerance=value)


def test_frozen_catalogs_and_plan_five_hashes_recorded_before_extension():
    assert FINANCIAL_TERMS_V3[:-1] == FINANCIAL_TERMS_V2
    w = UUID("10000000-0000-4000-8000-000000000001")
    u = UUID("10000000-0000-4000-8000-000000000002")
    opts = UploadChoices(
        source_language="en",
        format_locale="en-GB",
        report_language="pt",
        planning_version="5",
        normalization_version="2",
    )
    p, v, *_ = upload_case(w, u, choices=opts)
    request, _ = prepare_upload(p, opts, u, "preview")
    assert (
        v["preview_hash"]
        == "dfdce0ca5d652b5fc57fa06fea56e383ec356fc7679954cd4f2b7172169c478d"
    )
    assert (
        request.fingerprint
        == "dddf9c88d92e3df8a5e0ca1f64442da9445c304a42113afe58b9785f401b54b6"
    )
    assert "cash_rounding_tolerance" not in opts.contract_payload()
    assert "cash_reconciliation" not in v
    assert p.batch.metadata.terminology_version == "2"


def test_plan_six_source_and_tolerance_survive_semantic_snapshot_replay():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from saleslab_v2.domain.idempotency import canonicalize
    from saleslab_v2.pipeline.replay import restore_request

    opts = choices(cash_rounding_tolerance="0.01")
    parsed, _, _, _, user = upload_case(
        content=csv_content(extra=cash_rows()), choices=opts
    )
    request, _ = prepare_upload(parsed, opts, user, "preview")
    restored = restore_request(
        canonicalize(request.semantic_payload()),
        run=SimpleNamespace(
            workspace_id=request.workspace_id,
            created_by_user_id=user,
            idempotency_key="preview",
            created_at=datetime.now(UTC),
            language_context={"format_locale": "en-GB"},
        ),
    )
    assert restored.fingerprint == request.fingerprint
    assert restored.sources[0].metadata.terminology_version == "3"
    step = next(r for r in restored.reconciliations if r.rule_id == "REC-CF-001")
    assert step.policy.rounding_absolute == Decimal("0.01")
