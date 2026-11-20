"""Working capital disclosures: signed stock changes, never inferred cash effects."""

import csv
import io
import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest
from working_capital_fixtures import BASIS, upload_case, working_capital_case

from saleslab_v2.normalization.financial import FinancialNormalizer
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.working_capital_source import WorkingCapitalReviewRequired


def test_reported_balances_and_change_reconcile_with_explicit_components():
    w, u, c, o, _ = working_capital_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"]
    values = {
        m["key"]: Decimal(m["exact_value"])
        for m in v["calculated_metrics"]
        if m["status"] == "calculated"
    }
    assert values["net_working_capital"] == values["reported_operating_nwc"] == 120
    assert values["opening_reported_operating_nwc"] == 100
    assert (
        values["operating_nwc_change"] == values["reported_operating_nwc_change"] == 20
    )
    assert all(r["status"] == "pass" for r in v["working_capital_reconciliations"])
    request, _ = prepare_upload(p, o, u, "preview")
    assert "opening_reported_operating_nwc" not in request.analyst_metric_keys
    assert not any("cash_effect" in key or key == "cfo" for key in values)


@pytest.mark.parametrize(
    "missing",
    [
        "opening_assets",
        "opening_liabilities",
        "opening_nwc",
        "closing_assets",
        "closing_liabilities",
        "closing_nwc",
        "change",
    ],
)
def test_missing_operands_are_visible_and_never_inferred(missing):
    w, u, c, o, _ = working_capital_case(missing=(missing,))
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    checks = {r["rule_id"]: r for r in v["working_capital_reconciliations"]}
    required = (
        "REC-WC-OPENING-001"
        if missing.startswith("opening")
        else "REC-WC-CHANGE-001" if missing == "change" else "REC-WC-CLOSING-001"
    )
    assert (
        checks[required]["status"] == "not_planned" and checks[required]["requirements"]
    )
    if missing in ("opening_nwc", "closing_nwc"):
        assert not any(
            m["key"] == "operating_nwc_change" for m in v["calculated_metrics"]
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("closing_assets", "301"),
        ("opening_liabilities", "151"),
        ("change", "21"),
        ("change", "-20"),
        ("closing_nwc", "121"),
    ],
)
def test_material_disclosure_differences_block_publication(key, value):
    w, u, c, o, _ = working_capital_case(changes={key: {1: value}})
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    assert not v["ready"] and any(
        r["status"] not in ("pass", "not_planned")
        for r in v["working_capital_reconciliations"]
    )


@pytest.mark.parametrize(
    "field,value", [(1, "-1"), (1, ""), (1, "300%"), (5, "USD"), (6, "thousands")]
)
def test_invalid_or_incompatible_operating_components_do_not_pass_tie_out(field, value):
    w, u, c, o, _ = working_capital_case(changes={"closing_assets": {field: value}})
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    assert (
        not v["ready"]
        or next(
            r
            for r in v["working_capital_reconciliations"]
            if r["rule_id"] == "REC-WC-CLOSING-001"
        )["status"]
        != "pass"
    )


@pytest.mark.parametrize(
    "key,changes",
    [
        ("opening_nwc", {2: "2024"}),
        ("opening_nwc", {7: "comparative"}),
        ("opening_nwc", {5: "USD"}),
        ("closing_nwc", {2: "2025"}),
        ("change", {2: "2025"}),
        ("change", {7: "comparative"}),
    ],
)
def test_invalid_temporal_or_currency_anchor_cannot_be_a_verified_change(key, changes):
    w, u, c, o, _ = working_capital_case(changes={key: changes})
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    assert (
        not v["ready"]
        or next(
            r
            for r in v["working_capital_reconciliations"]
            if r["rule_id"] == "REC-WC-CHANGE-001"
        )["status"]
        != "pass"
    )


def test_negative_and_zero_operating_nwc_are_legitimate_not_absolute_values():
    w, u, c, o, _ = working_capital_case(
        changes={
            "closing_assets": {1: "100"},
            "closing_liabilities": {1: "180"},
            "closing_nwc": {1: "-80"},
            "change": {1: "-180"},
        }
    )
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"]
    values = {
        m["key"]: Decimal(m["exact_value"])
        for m in v["calculated_metrics"]
        if m["status"] == "calculated"
    }
    assert (
        values["net_working_capital"] == values["reported_operating_nwc"] == -80
        and values["operating_nwc_change"] == -180
    )
    w, u, c, o, _ = working_capital_case(
        changes={
            "closing_assets": {1: "180"},
            "closing_nwc": {1: "0"},
            "change": {1: "-100"},
        }
    )
    assert upload_case(w, u, content=c, choices=o)[1]["ready"]


def test_own_rounding_allowance_is_explicit_and_changes_preview_hash():
    w, u, c, o, _ = working_capital_case(changes={"change": {1: "20.01"}})
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    _, z, *_ = upload_case(
        w,
        u,
        content=c,
        choices=o.model_copy(update={"working_capital_rounding_tolerance": "0.01"}),
    )
    assert not v["ready"] and z["ready"] and v["preview_hash"] != z["preview_hash"]
    assert not upload_case(
        w,
        u,
        content=c,
        choices=o.model_copy(
            update={"rounding_tolerance": "1000", "capital_rounding_tolerance": "1000"}
        ),
    )[1]["ready"]


def test_private_source_review_preserves_partial_confirmations():
    w, _u, c, o, locs = working_capital_case(confirmed=False)
    with pytest.raises(WorkingCapitalReviewRequired) as exc:
        parse_upload(w, "statements.csv", c, o)
    assert (
        len(exc.value.payload["rows"]) == 7
        and "calculated_metrics" not in exc.value.payload
    )
    o = o.model_copy(
        update={"working_capital_definitions": {locs["opening_nwc"]: BASIS}}
    )
    with pytest.raises(WorkingCapitalReviewRequired) as exc:
        parse_upload(w, "statements.csv", c, o)
    assert (
        next(
            r for r in exc.value.payload["rows"] if r["location"] == locs["opening_nwc"]
        )["working_capital_definition_quote"]
        == BASIS
    )


@pytest.mark.parametrize("kind", ["foreign", "invented", "mismatched"])
def test_definitions_must_be_verbatim_scoped_and_consistent(kind):
    w, _u, c, o, locs = working_capital_case()
    definitions = dict(o.working_capital_definitions)
    if kind == "foreign":
        definitions["csv:500"] = BASIS
    elif kind == "invented":
        definitions[locs["closing_nwc"]] = "invented operating definition"
    else:
        definitions[locs["closing_nwc"]] = (
            "reported change is closing minus opening, not a cash flow"
        )
    with pytest.raises(InvalidUpload):
        parse_upload(
            w,
            "statements.csv",
            c,
            o.model_copy(update={"working_capital_definitions": definitions}),
        )


@pytest.mark.parametrize("kind", ["missing", "mismatched", "negative_component"])
def test_normalizer_independently_checks_operating_definition_and_component_sign(kind):
    w, _u, c, o, locs = working_capital_case()
    p = parse_upload(w, "statements.csv", c, o)
    rows = list(p.batch.rows)
    i = next(
        i for i, r in enumerate(rows) if r.source_reference == locs["closing_assets"]
    )
    changes = (
        {"definition_quote": None}
        if kind == "missing"
        else (
            {"definition_quote": "another operating perimeter"}
            if kind == "mismatched"
            else {"text": "-1"}
        )
    )
    rows[i] = replace(rows[i], **changes)
    values = FinancialNormalizer().normalize(rows, p.batch.metadata)
    assert any(
        issue.code.startswith("working_capital.")
        for v in values
        if v.source_reference == locs["closing_assets"]
        for issue in v.issues
    )


def test_partial_mappings_leave_regular_mapping_preview_accessible():
    w, u, c, o, locs = working_capital_case()
    partial = o.model_copy(
        update={
            "mappings": {locs["closing_nwc"]: "reported_operating_nwc"},
            "working_capital_definitions": None,
        }
    )
    _, v, *_ = upload_case(w, u, content=c, choices=partial)
    assert not v["ready"] and any(row["concept_id"] is None for row in v["rows"])
    with pytest.raises(WorkingCapitalReviewRequired):
        parse_upload(
            w,
            "statements.csv",
            c,
            o.model_copy(update={"working_capital_definitions": None}),
        )


def test_old_plans_omit_new_fields_and_reject_new_decisions():
    _, _, _, o, _ = working_capital_case()
    for version in map(str, range(1, 13)):
        data = o.model_dump() | {
            "planning_version": version,
            "working_capital_definitions": None,
        }
        assert (
            "working_capital_definitions"
            not in UploadChoices(**data).contract_payload()
        )
        assert (
            "working_capital_rounding_tolerance"
            not in UploadChoices(**data).contract_payload()
        )
        with pytest.raises(ValueError):
            UploadChoices(**(data | {"working_capital_rounding_tolerance": "0"}))
    with pytest.raises(ValueError):
        UploadChoices(**(o.model_dump() | {"normalization_version": "1"}))


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
def test_operating_perimeter_and_limits_survive_audit_and_html_pdf(language):
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    w, u, c, o, _ = working_capital_case(language=language, report_language=language)
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"]
    request, _ = prepare_upload(p, o, u, "preview")
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, context, _, draft, audit, _) = report_fixture(
            language, growth=False
        )
    assert audit["status"] in ("approved", "approved_with_warnings")
    assert (
        len(context["working_capital_definitions"]) == 7
        and "growth_definitions" not in context
    )
    assert (
        report["methodology"]["working_capital_definitions"]
        == context["working_capital_definitions"]
    )
    for evidence in [
        context["evidence"],
        draft["financial_snapshot"],
        report["evidence"],
    ]:
        assert {
            "operating_nwc_change",
            "reported_operating_nwc",
            "reported_operating_nwc_change",
        } <= {e["concept_id"] for e in evidence}
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert artifacts["parity"]["status"] == "pass" and BASIS in artifacts["html"]


@pytest.mark.parametrize(
    "opening,closing,fiscal",
    [("February 2026", "March 2026", None), ("FY 2025", "FY 2026 Q1", 6)],
)
def test_monthly_and_fiscal_quarter_opening_stocks_are_adjacent(
    opening, closing, fiscal
):
    changes = {
        key: {2: opening if key.startswith("opening") else closing}
        for key in (
            "opening_assets",
            "opening_liabilities",
            "opening_nwc",
            "closing_assets",
            "closing_liabilities",
            "closing_nwc",
            "change",
        )
    }
    w, u, c, o, _ = working_capital_case(changes=changes)
    o = o.model_copy(update={"target_period": closing, "fiscal_year_end_month": fiscal})
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"] and all(
        r["status"] == "pass" for r in v["working_capital_reconciliations"]
    )
    assert (
        Decimal(
            next(
                m["exact_value"]
                for m in v["calculated_metrics"]
                if m["key"] == "operating_nwc_change"
            )
        )
        == 20
    )


def test_metric_engine_rechecks_opening_adjacency_and_preserves_exact_subtraction():
    from datetime import date
    from uuid import uuid4

    from saleslab_v2.domain.value_objects import Period
    from saleslab_v2.metrics import FINANCIAL_V1
    from saleslab_v2.metrics.engine import (
        IncompatibleInputError,
        MetricEngine,
        MetricInput,
    )

    current = Period(
        date(2026, 1, 1),
        date(2026, 12, 31),
        granularity="year",
        comparison_basis="current",
    )
    prior = Period(
        date(2025, 1, 1),
        date(2025, 12, 31),
        granularity="year",
        comparison_basis="current",
    )

    def value(amount, period):
        return MetricInput(
            uuid4(),
            "reported_operating_nwc",
            Decimal(amount),
            "monetary",
            "EUR",
            period,
            "consolidated",
        )

    inputs = {
        "closing_operating_nwc": value("120.000000000000000000000000001", current),
        "opening_operating_nwc": value("100", prior),
    }
    actual, trace = MetricEngine(FINANCIAL_V1).calculate("operating_nwc_change", inputs)
    assert (
        actual.value == Decimal("20.000000000000000000000000001")
        and trace.metric_definition_version == "1"
    )
    bad = Period(
        date(2024, 1, 1),
        date(2024, 12, 31),
        granularity="year",
        comparison_basis="current",
    )
    inputs["opening_operating_nwc"] = value("100", bad)
    with pytest.raises(IncompatibleInputError):
        MetricEngine(FINANCIAL_V1).calculate("operating_nwc_change", inputs)


def test_changed_source_definition_changes_source_identity_and_preview_hash():
    w, u, c, o, _ = working_capital_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    shorter = "operating current assets exclude cash and non-operating assets; operating current liabilities exclude debt and leases"
    modified = o.model_copy(
        update={
            "working_capital_definitions": {
                loc: shorter for loc in o.working_capital_definitions
            }
        }
    )
    q, z, *_ = upload_case(w, u, content=c, choices=modified)
    assert (
        p.batch.document.source_id != q.batch.document.source_id
        and v["preview_hash"] != z["preview_hash"]
    )


def test_duplicate_and_incomplete_disclosure_rows_are_never_selected_silently():
    w, u, c, o, locs = working_capital_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows.append(rows[int(locs["closing_nwc"].split(":")[1]) - 1])
    loc = "csv:" + str(len(rows))
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    o = o.model_copy(
        update={
            "mappings": o.mappings | {loc: "reported_operating_nwc"},
            "working_capital_definitions": o.working_capital_definitions | {loc: BASIS},
        }
    )
    _, v, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert not v["ready"] and not any(
        m["key"] == "operating_nwc_change" for m in v["calculated_metrics"]
    )


def test_combined_profit_cash_capital_growth_debt_capex_roic_and_working_capital_report():
    from analyst_fixtures import model_output
    from capex_upload_fixtures import complete_capex_case
    from report_fixtures import report_fixture
    from roic_upload_fixtures import AVERAGE, NOPAT, TAX, roic_case

    from saleslab_v2.product.roic_contracts import RoicSourceChoices
    from saleslab_v2.reports.render import render_channels

    w, u, c, o = complete_capex_case()
    p, *_ = upload_case(w, u, content=c, choices=o)
    observations = FinancialNormalizer().normalize(list(p.batch.rows), p.batch.metadata)
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    mappings = dict(o.mappings)
    definitions = {}
    wc_rows = list(
        csv.reader(io.StringIO(working_capital_case()[2].decode()), delimiter=";")
    )
    _, _, _, wc_choices, wc_locs = working_capital_case()
    for key, old in wc_locs.items():
        concept = wc_choices.mappings[old]
        if key in ("closing_assets", "closing_liabilities"):
            match = next(
                ob
                for ob in observations
                if ob.concept_id == concept and ob.period.label == "2026"
            )
            loc = match.source_reference
            row = rows[int(loc.split(":")[1]) - 1]
            row[0] += " | " + BASIS
        else:
            loc = "csv:" + str(len(rows) + 1)
            rows.append(wc_rows[int(old.split(":")[1]) - 1])
        mappings[loc] = concept
        definitions[loc] = BASIS
    _, _, rc, ro = roic_case()
    source = list(csv.reader(io.StringIO(rc.decode()), delimiter=";"))
    roic_locations = {}
    for old, concept in ro.mappings.items():
        loc = "csv:" + str(len(rows) + 1)
        rows.append(source[int(old.split(":")[1]) - 1])
        mappings[loc] = concept
        roic_locations[concept] = loc
    nopat = roic_locations["nopat_with_explicit_tax_assumptions"]
    o = o.model_copy(
        update={
            "planning_version": "13",
            "mappings": mappings,
            "working_capital_definitions": definitions,
            "roic_sources": RoicSourceChoices(
                nopat_location=nopat,
                nopat_definition=NOPAT,
                tax_assumptions_location=nopat,
                tax_assumptions=TAX,
                average_capital_location=roic_locations["average_invested_capital"],
                average_capital_definition=AVERAGE,
            ),
        }
    )
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    p, v, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert v["ready"] and all(
        check["status"] == "pass" for check in v["working_capital_reconciliations"]
    )
    request, _ = prepare_upload(p, o, u, "preview")

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
        report, (_, _, context, _, _, audit, _) = report_fixture(growth=False)
    assert audit["status"] in ("approved", "approved_with_warnings")
    assert len(request.metrics) == 86
    assert len(json.dumps(context, ensure_ascii=True).encode()) < 256_000
    assert (
        len(context["working_capital_definitions"]) == 7
        and len(context["capex_definitions"]) == 5
    )
    assert (
        len(context["debt_definitions"]) == 5
        and len(context["growth_definitions"]) == 8
        and len(context["roic_sources"]) == 1
    )
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert (
        artifacts["parity"]["status"] == "pass"
        and artifacts["parity"]["page_count"] <= 120
    )
