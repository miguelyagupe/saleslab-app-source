"""Versioned reporting of signed source investing and financing cash flows."""

from decimal import Decimal
from uuid import UUID

import pytest
from product_fixtures import csv_content, upload_case
from test_saleslab_v2_f03_metrics import item

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.catalog import FINANCIAL_TERMS
from saleslab_v2.metrics.engine import (
    IncompatibleInputError,
    MetricEngine,
    MissingInputError,
)
from saleslab_v2.metrics.financial_v1 import FINANCIAL_V1
from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.preview import LABELS

TERMS = {t.concept_id: t for t in FINANCIAL_TERMS}


def choices(**updates):
    return UploadChoices(
        **{
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "planning_version": "4",
            **updates,
        }
    )


def flow(
    concept,
    amount,
    *,
    language="en",
    period="2026",
    scale="raw",
    currency="EUR",
    basis="current",
):
    return (
        TERMS[concept].labels[LanguageCode(language)],
        amount,
        period,
        "cash_flow_statement",
        "consolidated",
        currency,
        scale,
        basis,
    )


@pytest.mark.parametrize(
    "locale,amount",
    (
        ("pt-PT", "-1.234,56"),
        ("en-GB", "-1,234.56"),
        ("es-ES", "-1.234,56"),
        ("fr-FR", "-1 234,56"),
    ),
)
@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize(
    "scale,factor", (("raw", 1), ("thousands", 1000), ("millions", 1000000))
)
def test_signed_flows_preserve_originals_exact_scaled_values_and_trace(
    locale, amount, language, scale, factor
):
    content = csv_content(
        extra=[flow("cfi", amount, scale=scale), flow("cff", "25", scale=scale)]
    )
    _, preview, _, _, _ = upload_case(
        content=content, choices=choices(format_locale=locale, report_language=language)
    )
    assert preview["ready"]
    results = {m["metric_id"]: m for m in preview["calculated_metrics"]}
    for concept, value in (
        ("cfi", Decimal("-1234.56") * factor),
        ("cff", Decimal(25) * factor),
    ):
        metric = results[concept]
        assert metric["status"] == "calculated"
        assert Decimal(metric["exact_value"]) == value
        assert (
            metric["label"] == LABELS[concept][("pt", "en", "es", "fr").index(language)]
        )
        evidence = metric["calculation_evidence"]
        assert evidence["canonical_expression"] == f"reported_{concept}"
        assert evidence["metric_definition_version"] == "1"
        assert len(evidence["inputs"]) == 1
        source = evidence["inputs"][0]["sources"][0]
        assert source["scale"] == scale
        assert source["amount"] == (amount if concept == "cfi" else "25")
    assert [r["rule_id"] for r in preview["validation_summary"]["reconciliations"]] == [
        "REC-BS-001"
    ]
    assert "free_cash_flow" not in results


@pytest.mark.parametrize("concept", ("cfi", "cff"))
@pytest.mark.parametrize(
    "value", ("-125.75", "0", "25.50", "123456789012345678901234567890.123456789")
)
def test_engine_identity_preserves_any_reported_sign_and_exact_decimal(concept, value):
    role = f"reported_{concept}"
    engine = MetricEngine(FINANCIAL_V1)
    result, trace = engine.calculate(
        concept, {role: item(role, value, metric_id=concept)}
    )
    assert result.value == Decimal(value)
    assert result.source_references == ("sheet:1!B1",)
    assert trace.metric_id == concept
    with pytest.raises(MissingInputError):
        engine.calculate(concept, {})
    for wrong in ("cfo", "capex", "revenue", "cff" if concept == "cfi" else "cfi"):
        with pytest.raises(IncompatibleInputError):
            engine.calculate(concept, {role: item(role, value, metric_id=wrong)})
    with pytest.raises(IncompatibleInputError):
        engine.calculate(
            concept,
            {role: item(role, value, metric_id=concept, status="needs_confirmation")},
        )


@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
def test_imported_source_language_is_independent_of_report_language(language):
    _, preview, _, _, _ = upload_case(
        content=csv_content(
            language=language,
            extra=[
                flow("cfi", "120", language=language),
                flow("cff", "-40", language=language),
            ],
        ),
        choices=choices(source_language=language, report_language="en"),
    )
    assert preview["ready"]
    values = {m["metric_id"]: m["exact_value"] for m in preview["calculated_metrics"]}
    assert (values["cfi"], values["cff"]) == ("120", "-40")


@pytest.mark.parametrize("version,count", (("1", 4), ("2", 5), ("3", 6), ("4", 8)))
def test_missing_flows_are_missing_not_zero_and_only_shown_in_supported_plan(
    version, count
):
    _, preview, _, _, _ = upload_case(choices=choices(planning_version=version))
    availability = {m["metric_id"]: m for m in preview["metric_availability"]}
    assert len(availability) == count
    assert (
        "cfi" not in preview["planned_metrics"]
        and "cff" not in preview["planned_metrics"]
    )
    for concept in ("cfi", "cff"):
        assert (concept in availability) == (version == "4")
        if version == "4":
            assert availability[concept]["status"] == "not_planned"
            assert availability[concept]["requirements"][0]["status"] == "missing"


@pytest.mark.parametrize(
    "extra,status",
    (
        ([flow("cfi", "1"), flow("cfi", "2")], "ambiguous"),
        ([flow("cfi", "(1)")], "needs_confirmation"),
        ([flow("cfi", "1", currency="")], "needs_confirmation"),
        ([flow("cfi", "1", period="2025", basis="comparative")], "missing"),
    ),
)
def test_duplicate_unconfirmed_or_comparative_flows_are_not_selected(extra, status):
    _, preview, _, _, _ = upload_case(
        content=csv_content(extra=extra), choices=choices()
    )
    assert "cfi" not in preview["planned_metrics"]
    gap = next(m for m in preview["metric_availability"] if m["metric_id"] == "cfi")
    assert gap["requirements"][0]["status"] == status
    assert all(m["metric_id"] != "cfi" for m in preview["calculated_metrics"])


def test_fiscal_period_and_confirmed_parentheses_survive_new_plan():
    concepts = ("revenue", "net_income", "total_assets", "total_liabilities", "equity")
    content = csv_content(
        changes={c: {"period": "FY 2026"} for c in concepts},
        extra=[
            flow("cfi", "(120)", period="FY 2026"),
            flow("cff", "40", period="FY 2026"),
        ],
    )
    _, preview, _, _, _ = upload_case(
        content=content,
        choices=choices(fiscal_year_end_month=6, accounting_parentheses=True),
    )
    assert preview["ready"]
    for metric in preview["calculated_metrics"]:
        assert (metric["period"]["start_date"], metric["period"]["end_date"]) == (
            "2025-07-01",
            "2026-06-30",
        )
    assert (
        next(m for m in preview["calculated_metrics"] if m["metric_id"] == "cfi")[
            "exact_value"
        ]
        == "-120"
    )


def test_prior_plan_three_hash_is_pinned_and_new_plan_keeps_ebit_and_fcf():
    w = UUID("10000000-0000-4000-8000-000000000001")
    u = UUID("10000000-0000-4000-8000-000000000002")
    extra = [
        (
            "EBIT",
            "200",
            "2026",
            "income_statement",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ),
        flow("cfo", "300"),
        flow("capex", "-80"),
        flow("cfi", "-120"),
        flow("cff", "40"),
    ]
    content = csv_content(extra=extra)
    parsed, old, _, _, _ = upload_case(
        w, u, content=content, choices=choices(planning_version="3")
    )
    request, _ = prepare_upload(parsed, choices(planning_version="3"), u, "preview")
    # Recorded on merged PR #46 before adding the new catalog entries or plan.
    assert (
        old["preview_hash"]
        == "c654164edc841dca8b2045e1b75f98fc398ee7f2be8b47fd10bd243b73471b6f"
    )
    assert (
        request.fingerprint
        == "5ea46ffd1f7626e2e67fc195cd8f2b6d086c01cfb1070ca1a920df3205b5fd4a"
    )
    _, new = prepare_upload(parsed, choices(), u, "preview")
    old_values = {m["metric_id"]: m["exact_value"] for m in old["calculated_metrics"]}
    values = {m["metric_id"]: m["exact_value"] for m in new["calculated_metrics"]}
    assert all(values[k] == v for k, v in old_values.items())
    assert (
        values["ebit_margin_pct"],
        values["free_cash_flow"],
        values["cfi"],
        values["cff"],
    ) == ("20.0", "220", "-120", "40")
    assert new["preview_hash"] != old["preview_hash"]
