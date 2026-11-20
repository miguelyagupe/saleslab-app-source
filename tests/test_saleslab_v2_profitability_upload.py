"""Grouped profitability product acceptance: all sources, variants and bridges."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from product_fixtures import csv_content, upload_case
from profitability_fixtures import (
    TERMS,
    VALUES,
    profitability_choices,
    profitability_rows,
)

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.i18n.financial_v3 import FINANCIAL_TERMS_V3, FinancialTerminologyV3
from saleslab_v2.i18n.financial_v4 import FINANCIAL_TERMS_V4, FinancialTerminologyV4
from saleslab_v2.pipeline.replay import restore_request
from saleslab_v2.product.contracts import InvalidUpload
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.preview import LABELS


def case(*, changes=None, omit=(), extra=(), **options):
    opts = profitability_choices(**options)
    content = csv_content(
        extra=[*profitability_rows(changes=changes, omit=omit), *extra]
    )
    return upload_case(content=content, choices=opts)


def metrics(preview):
    return {m["key"]: m for m in preview["calculated_metrics"]}


def checks(preview):
    return {r["rule_id"]: r for r in preview["validation_summary"]["reconciliations"]}


@pytest.mark.parametrize(
    "source,locale",
    (("en", "en-GB"), ("pt", "pt-PT"), ("es", "es-ES"), ("fr", "fr-FR")),
)
@pytest.mark.parametrize("report", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize(
    "scale,factor", (("raw", 1), ("thousands", 1000), ("millions", 1000000))
)
def test_complete_bundle_has_independent_reported_sources_localized_variants_and_traces(
    source, locale, report, scale, factor
):
    opts = profitability_choices(
        source_language=source, format_locale=locale, report_language=report
    )
    base = ("revenue", "net_income", "total_assets", "total_liabilities", "equity")
    content = csv_content(
        language=source,
        changes={c: {"scale": scale} for c in base},
        extra=profitability_rows(language=source, scale=scale),
    )
    parsed, v, _, _, _ = upload_case(content=content, choices=opts)
    assert v["ready"]
    assert parsed.batch.metadata.terminology_version == "4"
    actual = metrics(v)
    for key, expected in (
        ("gross_profit", 400 * factor),
        ("gross_margin_pct", 40),
        ("ebitda_statutory", 250 * factor),
        ("ebitda_adjusted", 270 * factor),
        ("reported_ebitda_statutory", 250 * factor),
        ("reported_ebitda_adjusted", 270 * factor),
        ("ebitda_margin_statutory_pct", 25),
        ("ebitda_margin_adjusted_pct", 27),
        ("cash_conversion_statutory_pct", 120),
        ("adjustments_to_statutory_ebitda_pct", 8),
    ):
        assert Decimal(actual[key]["exact_value"]) == expected
        assert (
            actual[key]["label"] == LABELS[key][("pt", "en", "es", "fr").index(report)]
        )
        assert actual[key]["calculation_evidence"]["trace_hash"]
    assert abs(
        Decimal(actual["cash_conversion_adjusted_pct"]["exact_value"])
        - Decimal(1000) / 9
    ) < Decimal("1e-24")
    stat = actual["ebitda_margin_statutory_pct"]["calculation_evidence"]["inputs"][0]
    adj = actual["ebitda_margin_adjusted_pct"]["calculation_evidence"]["inputs"][0]
    assert stat["variant"] == "statutory" and adj["variant"] == "adjusted"
    assert actual["ebitda_statutory"]["source_references"] == ["csv:14", "csv:7"]
    assert actual["reported_ebitda_statutory"]["source_references"] == ["csv:16"]
    assert (
        actual["ebitda_statutory"]["calculation_evidence"]["metric_definition_version"]
        == "1"
    )
    assert (
        actual["reported_ebitda_statutory"]["calculation_evidence"][
            "metric_definition_version"
        ]
        == "2"
    )
    bridge = checks(v)["REC-EBITDA-001"]
    assert bridge["status"] == checks(v)["REC-PL-001"]["status"] == "pass"
    evidence = bridge["reconciliation_evidence"]
    assert len(evidence["equations"]) == 2
    observed = next(i for i in evidence["inputs"] if i["role"] == "statutory_ebitda")
    assert observed["source_references"] == ["csv:16"]
    assert observed["sources"][0]["amount"] == "250"
    assert all(
        r["terminology_version"] == "financial-terminology-v4" for r in v["rows"]
    )


@pytest.mark.parametrize("concept", [c for c, _ in VALUES])
def test_missing_inputs_are_not_zero_or_substituted_by_reported_totals(concept):
    _, v, *_ = case(omit=(concept,))
    gaps = [g for g in v["metric_availability"] if g["status"] == "not_planned"]
    assert any(
        any(
            r["concept_id"] == concept and r["status"] == "missing"
            for r in g["requirements"]
        )
        for g in gaps
    )
    if concept == "depreciation_and_amortization":
        assert "reported_ebitda_statutory" in metrics(v)
        assert "ebitda_statutory" not in metrics(v)
    if concept in {"reported_statutory_ebitda", "reported_adjusted_ebitda"}:
        assert "REC-EBITDA-001" not in checks(v)
        assert "ebitda_statutory" in metrics(v)


@pytest.mark.parametrize(
    "concept",
    (
        "cost_of_sales",
        "ebit",
        "depreciation_and_amortization",
        "ebitda_adjustments",
        "reported_statutory_ebitda",
        "reported_adjusted_ebitda",
    ),
)
def test_duplicates_are_not_selected_even_when_one_duplicate_is_invalid(concept):
    t = TERMS[concept]
    row = (
        t.labels[LanguageCode.EN],
        "(10)",
        "2026",
        t.statement_type,
        "consolidated",
        "EUR",
        "raw",
        "current",
    )
    _, v, *_ = case(extra=(row,))
    assert not v["ready"]
    assert any(
        any(
            r["concept_id"] == concept and r["status"] == "ambiguous"
            for r in g["requirements"]
        )
        for g in v["metric_availability"]
    )


@pytest.mark.parametrize(
    "concept", ("cost_of_sales", "operating_costs", "depreciation_and_amortization")
)
def test_negative_deduction_or_da_magnitudes_are_blocked_without_sign_conversion(
    concept,
):
    _, v, *_ = case(changes={concept: {"amount": "-1"}})
    assert not v["ready"]
    assert metrics(v)[concept]["status"] == "unavailable"
    assert metrics(v)[concept]["calculation_evidence"] is None
    assert next(r for r in v["rows"] if r["concept_id"] == concept)["value"] == "-1"


@pytest.mark.parametrize(
    "concept,value,rule",
    (
        ("reported_statutory_ebitda", "251", "REC-EBITDA-001"),
        ("reported_adjusted_ebitda", "271", "REC-EBITDA-001"),
        ("operating_costs", "811", "REC-PL-001"),
        ("income_tax", "41", "REC-PL-001"),
    ),
)
def test_independent_reported_differences_block_generation_but_retain_actual_calculations(
    concept, value, rule
):
    _, v, *_ = case(changes={concept: {"amount": value}})
    assert not v["ready"]
    assert checks(v)[rule]["status"] == "blocking"
    assert Decimal(checks(v)[rule]["absolute_delta"]) == 1
    assert Decimal(metrics(v)["ebitda_statutory"]["exact_value"]) == 250
    assert checks(v)[rule]["reconciliation_evidence"]


def test_one_explicit_profitability_allowance_is_separate_from_balance_and_cash():
    _, blocked, *_ = case(
        changes={"reported_adjusted_ebitda": {"amount": "270.005"}},
        rounding_tolerance="100",
        cash_rounding_tolerance="100",
    )
    assert not blocked["ready"]
    _, accepted, *_ = case(
        changes={"reported_adjusted_ebitda": {"amount": "270.005"}},
        profitability_rounding_tolerance="0.01",
    )
    assert accepted["ready"]
    assert checks(accepted)["REC-EBITDA-001"]["status"] == "rounding_pass"
    assert Decimal(checks(accepted)["REC-EBITDA-001"]["absolute_delta"]) == Decimal(
        "0.005"
    )


@pytest.mark.parametrize("finance,tax", (("-10", "110"), ("210", "-110")))
def test_signed_adjustments_finance_income_and_tax_benefits_keep_the_written_sign(
    finance, tax
):
    _, v, *_ = case(
        changes={
            "ebitda_adjustments": {"amount": "-20"},
            "reported_adjusted_ebitda": {"amount": "230"},
            "net_finance_cost": {"amount": finance},
            "income_tax": {"amount": tax},
        }
    )
    assert v["ready"]
    assert Decimal(metrics(v)["ebitda_adjusted"]["exact_value"]) == 230
    assert (
        Decimal(metrics(v)["adjustments_to_statutory_ebitda_pct"]["exact_value"]) == -8
    )


def test_zero_ebitda_denominators_never_become_zero_percentages():
    _, v, *_ = case(
        changes={
            "ebit": {"amount": "-50"},
            "reported_statutory_ebitda": {"amount": "0"},
            "reported_adjusted_ebitda": {"amount": "20"},
            "operating_costs": {"amount": "1060"},
            "income_tax": {"amount": "-210"},
        }
    )
    assert not v["ready"]
    for key in ("cash_conversion_statutory_pct", "adjustments_to_statutory_ebitda_pct"):
        assert metrics(v)[key]["exact_value"] is None
    assert Decimal(metrics(v)["ebitda_margin_statutory_pct"]["exact_value"]) == 0


@pytest.mark.parametrize(
    "concept",
    ("reported_adjusted_ebitda", "depreciation_and_amortization", "cost_of_sales"),
)
@pytest.mark.parametrize(
    "field,value",
    (("currency", "USD"), ("period", "2025"), ("comparison_basis", "comparative")),
)
def test_currency_period_and_basis_mismatches_do_not_form_a_supported_bridge(
    concept, field, value
):
    _, v, *_ = case(changes={concept: {field: value}})
    if field == "currency":
        assert not v["ready"]
    else:
        assert any(
            any(r["concept_id"] == concept for r in g["requirements"])
            for g in v["metric_availability"]
        )


def test_generic_ebitda_stays_unverified_and_explicit_mapping_requires_statement_context():
    rows = profitability_rows(changes={"reported_statutory_ebitda": {"line": "EBITDA"}})
    content = csv_content(extra=rows)
    _, blocked, *_ = upload_case(content=content, choices=profitability_choices())
    assert not blocked["ready"]
    assert "reported_ebitda_statutory" not in metrics(blocked)
    opts = profitability_choices(mappings={"csv:16": "reported_statutory_ebitda"})
    _, accepted, *_ = upload_case(content=content, choices=opts)
    assert accepted["ready"]
    assert (
        metrics(accepted)["reported_ebitda_statutory"]["calculation_evidence"][
            "inputs"
        ][0]["sources"][0]["line"]
        == "EBITDA"
    )
    with pytest.raises(InvalidUpload, match="mapping must match"):
        upload_case(
            content=content, choices=opts.model_copy(update={"planning_version": "6"})
        )


def test_old_catalogs_and_semantic_replay_remain_pinned():
    assert FINANCIAL_TERMS_V4[: len(FINANCIAL_TERMS_V3)] == FINANCIAL_TERMS_V3
    for concept in set(TERMS) - {t.concept_id for t in FINANCIAL_TERMS_V3}:
        label = TERMS[concept].labels[LanguageCode.EN]
        assert (
            FinancialTerminologyV3().lookup(label, LanguageCode.EN, "income_statement")
            is None
        )
        assert (
            FinancialTerminologyV4()
            .lookup(label, LanguageCode.EN, "income_statement")
            .concept_id
            == concept
        )
    parsed, _, _, _, user = case(profitability_rounding_tolerance="0.01")
    opts = profitability_choices(profitability_rounding_tolerance="0.01")
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
    assert restored.sources[0].metadata.terminology_version == "4"
    assert (
        profitability_choices(profitability_rounding_tolerance=None)
        .contract_payload()
        .get("profitability_rounding_tolerance")
        is None
    )
    assert all(
        r.policy.rounding_absolute == Decimal("0.01")
        for r in restored.reconciliations
        if r.rule_id in {"REC-PL-001", "REC-EBITDA-001"}
    )


@pytest.mark.parametrize("value", ("NaN", "-1", "1e64", "1e-64", 0, True, ""))
def test_profitability_tolerance_is_strict_and_bounded(value):
    with pytest.raises(ValueError):
        profitability_choices(profitability_rounding_tolerance=value)


@pytest.mark.parametrize(
    "period,month", (("FY 2026", 6), ("2026 Q1", None), ("January 2026", None))
)
def test_profitability_uses_actual_fiscal_and_calendar_intervals(period, month):
    opts = profitability_choices(target_period=period, fiscal_year_end_month=month)
    content = csv_content(
        changes={
            c: {"period": period}
            for c in (
                "revenue",
                "net_income",
                "total_assets",
                "total_liabilities",
                "equity",
            )
        },
        extra=profitability_rows(period=period),
    )
    _, v, *_ = upload_case(content=content, choices=opts)
    assert v["ready"]
    assert checks(v)["REC-PL-001"]["status"] == "pass"
    assert checks(v)["REC-EBITDA-001"]["status"] == "pass"
