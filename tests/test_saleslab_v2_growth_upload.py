"""Versioned growth methods and monetary bridges preserve source/denominator proof."""

import csv
import io
from decimal import Decimal

import pytest
from growth_fixtures import BRIDGE, CC, LFL, growth_case
from product_fixtures import upload_case
from pydantic import ValidationError

from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.growth_source import GrowthReviewRequired
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.planning import prepare_upload


def preview_case(**kwargs):
    w, u, c, o = growth_case(**kwargs)
    p, v, g, *_ = upload_case(w, u, content=c, choices=o)
    return p, v, g, c, u


def test_reported_cc_lfl_are_distinct_and_bridge_uses_explicit_monetary_contributions():
    p, v, _g, _, _ = preview_case()
    values = {m["key"]: Decimal(m["exact_value"]) for m in v["calculated_metrics"]}
    assert v["ready"]
    assert values["revenue_growth"] == Decimal("9.3")
    assert values["revenue_growth_cc_pct"] == Decimal("4.9")
    assert values["revenue_growth_lfl_pct"] == Decimal("0.6")
    assert values["revenue_change_reported"] == Decimal(93)
    check = next(
        r
        for r in v["validation_summary"]["reconciliations"]
        if r["rule_id"] == "REC-GROWTH-001"
    )
    assert check["status"] == "pass"
    assert check["expected"] == check["observed"] == "93"
    assert {
        row["growth_definition_quote"]
        for row in p.original_rows
        if "growth_definition_quote" in row
    } == {CC, LFL, BRIDGE}
    for key in ("revenue_growth_cc_pct", "revenue_growth_lfl_pct"):
        metric = next(m for m in v["calculated_metrics"] if m["key"] == key)
        assert len(metric["calculation_evidence"]["inputs"]) == 2
        assert all(i["sources"] for i in metric["calculation_evidence"]["inputs"])
    assert any(i["code"] == "pipeline.causal_evidence_required" for i in v["issues"])
    assert (
        next(d for d in v["quality"]["decisions"] if d["capability"] == "infer_cause")[
            "decision_status"
        ]
        == "blocked"
    )


def test_source_only_review_precedes_all_financial_calculation():
    w, _u, c, o = growth_case(confirmed=False)
    with pytest.raises(GrowthReviewRequired) as found:
        parse_upload(w, "statements.csv", c, o)
    review = found.value.payload
    assert review["schema"] == "financial-growth-review-v1"
    assert review["ready"] is False
    assert len(review["rows"]) == 8
    assert "calculated_metrics" not in review


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
@pytest.mark.parametrize("report_language", ["pt", "en", "es", "fr"])
def test_source_and_report_languages_do_not_change_growth(language, report_language):
    _, v, *_ = preview_case(language=language, report_language=report_language)
    assert v["ready"]
    assert Decimal(
        next(m for m in v["calculated_metrics"] if m["key"] == "revenue_growth_cc_pct")[
            "exact_value"
        ]
    ) == Decimal("4.9")


@pytest.mark.parametrize("change", ["absent", "outside", "mismatched", "not_verbatim"])
def test_missing_foreign_or_inconsistent_definitions_cannot_generate(change):
    w, _u, c, o = growth_case()
    quotes = dict(o.growth_definitions)
    if change == "absent":
        quotes.pop("csv:8")
    elif change == "outside":
        quotes["csv:999"] = CC
    elif change == "mismatched":
        quotes["csv:8"] = "exchange rates fixed at 2025"
    else:
        quotes["csv:8"] = "invented exchange rate method"
    with pytest.raises(InvalidUpload):
        parse_upload(
            w, "statements.csv", c, o.model_copy(update={"growth_definitions": quotes})
        )


def test_source_definition_is_bound_to_request_trace_and_preview():
    w, u, c, o = growth_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    quotes = dict(o.growth_definitions)
    for key in ("csv:8", "csv:9"):
        quotes[key] = "exchange rates fixed at 2025"
    q, z, *_ = upload_case(
        w, u, content=c, choices=o.model_copy(update={"growth_definitions": quotes})
    )
    assert p.batch.document.content_hash == q.batch.document.content_hash
    assert p.batch.document.source_id != q.batch.document.source_id
    assert v["preview_hash"] != z["preview_hash"]
    assert (
        next(m for m in v["calculated_metrics"] if m["key"] == "revenue_growth_cc_pct")[
            "calculation_trace_id"
        ]
        != next(
            m for m in z["calculated_metrics"] if m["key"] == "revenue_growth_cc_pct"
        )["calculation_trace_id"]
    )


@pytest.mark.parametrize(
    "field,value", [("currency", "USD"), ("period", "2024"), ("amount", "0")]
)
def test_incompatible_or_zero_cc_bases_do_not_produce_invented_growth(field, value):
    w, u, c, o = growth_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows[8][{"currency": 5, "period": 2, "amount": 1}[field]] = value
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _p, v, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    if field != "period":
        assert not v["ready"]
    assert not any(
        m["key"] == "revenue_growth_cc_pct" and m["status"] == "calculated"
        for m in v["calculated_metrics"]
    )


def test_bridge_difference_blocks_and_no_missing_component_is_assumed_zero():
    w, u, c, o = growth_case()
    bad = c.replace(b";44;2026;", b";45;2026;")
    _, v, *_ = upload_case(w, u, content=bad, choices=o)
    assert not v["ready"]
    assert (
        next(
            r
            for r in v["validation_summary"]["reconciliations"]
            if r["rule_id"] == "REC-GROWTH-001"
        )["status"]
        == "blocking"
    )
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows.pop()
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    mappings = {k: v for k, v in o.mappings.items() if k != "csv:15"}
    quotes = {k: v for k, v in o.growth_definitions.items() if k != "csv:15"}
    _, v, *_ = upload_case(
        w,
        u,
        content=out.getvalue().encode(),
        choices=o.model_copy(
            update={"mappings": mappings, "growth_definitions": quotes}
        ),
    )
    assert not any(
        r["rule_id"] == "REC-GROWTH-001"
        for r in v["validation_summary"]["reconciliations"]
    )
    assert v["growth_reconciliations"][0]["status"] == "not_planned"
    assert any(
        r["concept_id"] == "growth_other_contribution"
        for r in v["growth_reconciliations"][0]["requirements"]
    )


def test_lfl_rate_is_not_a_contribution_on_the_reported_revenue_base():
    w, u, c, o = growth_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows[9][1] = "503"
    rows[10][1] = "500"
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _, v, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert v["ready"]
    values = {m["key"]: Decimal(m["exact_value"]) for m in v["calculated_metrics"]}
    assert values["revenue_growth_lfl_pct"] == Decimal("0.6")
    assert values["growth_lfl_contribution"] == Decimal(6)


def test_rates_cannot_be_reclassified_as_monetary_bases():
    w, u, c, o = growth_case()
    _, v, *_ = upload_case(w, u, content=c.replace(b";1049;", b";4.9%;"), choices=o)
    assert not v["ready"]
    assert not any(
        m["key"] == "revenue_growth_cc_pct" and m["status"] == "calculated"
        for m in v["calculated_metrics"]
    )


def test_plan_9_requires_exact_normalization_and_older_plans_reject_growth_decisions():
    with pytest.raises(ValidationError):
        UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            planning_version="9",
        )
    with pytest.raises(ValidationError):
        UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            planning_version="8",
            growth_definitions={},
        )


def test_full_growth_capital_bundle_keeps_quoted_methods_through_independent_audit_and_web_pdf():
    import json
    from unittest.mock import patch

    from analyst_fixtures import model_output
    from growth_fixtures import complete_growth_case
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    w, u, c, o = complete_growth_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    request, _ = prepare_upload(p, o, u, "preview")
    assert v["ready"]

    def selected(context):
        raw = model_output(context)
        sizes = {e["evidence_id"]: len(json.dumps(e)) for e in context["evidence"]}
        raw["findings"] = sorted(
            raw["findings"], key=lambda f: sizes[f["evidence_ids"][0]], reverse=True
        )[:20]
        return raw

    with patch("report_fixtures.request", return_value=request), patch(
        "audit_fixtures.model_output", side_effect=selected
    ):
        report, (_, _, context, _, _, audit, checks) = report_fixture(growth=False)
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert all(check["status"] != "block" for check in checks)
    assert len(report["metric_observations"]) == 71
    assert len(context["growth_definitions"]) == 8
    assert report["methodology"]["growth_definitions"] == context["growth_definitions"]
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert artifacts["parity"]["status"] == "pass"
    assert artifacts["parity"]["page_count"] <= 120
    assert CC in artifacts["html"]


def test_core_normalization_cannot_accept_unconfirmed_or_mismatched_growth_methods():
    from dataclasses import replace

    from saleslab_v2.normalization.financial import FinancialNormalizer

    w, _u, c, o = growth_case()
    parsed = parse_upload(w, "statements.csv", c, o)
    rows = list(parsed.batch.rows)
    target = next(i for i, r in enumerate(rows) if r.source_reference == "csv:8")
    rows[target] = replace(rows[target], definition_quote=None)
    missing = FinancialNormalizer().normalize(rows, parsed.batch.metadata)
    assert (
        next(r for r in missing if r.source_reference == "csv:8").normalization_status
        == "needs_confirmation"
    )
    rows[target] = replace(
        rows[target], definition_quote="different documented FX method"
    )
    mismatched = FinancialNormalizer().normalize(rows, parsed.batch.metadata)
    assert all(
        r.normalization_status == "needs_confirmation"
        for r in mismatched
        if r.concept_id == "revenue_constant_currency"
    )


@pytest.mark.parametrize(
    "language,suffix",
    [
        ("pt", " · comparativo"),
        ("en", " · prior"),
        ("es", " · comparativo"),
        ("fr", " · antérieur"),
    ],
)
def test_growth_gaps_request_the_localized_monetary_base(language, suffix):
    from saleslab_v2.domain.enums import LanguageCode
    from saleslab_v2.i18n.financial_v6 import FINANCIAL_TERMS_V6

    w, u, c, o = growth_case(report_language=language)
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows[8][2] = "2024"
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _, preview, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    gap = next(
        m
        for m in preview["metric_availability"]
        if m.get("key") == "revenue_growth_cc_pct"
    )
    required = next(
        r for r in gap["requirements"] if r["role"] == "revenue_constant_currency_prior"
    )
    term = next(
        t for t in FINANCIAL_TERMS_V6 if t.concept_id == "revenue_constant_currency"
    )
    assert required["label"] == term.labels[LanguageCode(language)] + suffix
    assert required["status"] == "missing"


@pytest.mark.parametrize("available", ["cc", "lfl", "both", "none"])
def test_validated_cc_lfl_pairs_independently_authorize_their_report_snapshot(
    available,
):
    from unittest.mock import patch

    from report_fixtures import report_fixture

    w, u, c, o = growth_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows[6][7] = "current"  # No comparable prior-year reported revenue.
    if available == "cc":
        rows[10][2] = "2024"
    if available == "lfl":
        rows[8][2] = "2024"
    if available == "none":
        rows[8][5] = "USD"
        rows[10][5] = "USD"
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    parsed, preview, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    if available == "none":
        assert not preview["ready"]
        assert (
            next(
                d
                for d in preview["quality"]["decisions"]
                if d["capability"] == "compare"
            )["decision_status"]
            == "blocked"
        )
        return
    assert preview["ready"]
    assert not any(m["key"] == "revenue_growth" for m in preview["calculated_metrics"])
    compare = next(
        d for d in preview["quality"]["decisions"] if d["capability"] == "compare"
    )
    assert compare["decision_status"] == "allowed"
    request, _ = prepare_upload(parsed, o, u, "preview")
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, context, _, draft, audit, _) = report_fixture(growth=False)
    assert audit["status"] in {"approved", "approved_with_warnings"}
    required = (
        {"revenue_growth_cc_pct"}
        if available == "cc"
        else (
            {"revenue_growth_lfl_pct"}
            if available == "lfl"
            else {"revenue_growth_cc_pct", "revenue_growth_lfl_pct"}
        )
    )
    for evidence in (
        context["evidence"],
        draft["financial_snapshot"],
        report["evidence"],
    ):
        assert required <= {e["concept_id"] for e in evidence}
        assert not any(
            e["concept_id"] == "revenue_growth_reported_pct" for e in evidence
        )
