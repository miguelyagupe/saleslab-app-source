"""Source capex classification, reconciliation and commitment separation."""

import csv
import io
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest
from capex_upload_fixtures import (
    COMMIT_BASIS,
    PAID_BASIS,
    VALUES,
    capex_case,
    upload_case,
)

from saleslab_v2.product.capex_source import CapexReviewRequired
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.planning import prepare_upload


def changed_case(concept, field, value):
    w, u, c, o = capex_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    loc = next(loc for loc, key in o.mappings.items() if key == concept)
    rows[int(loc.split(":")[1]) - 1][field] = value
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    return w, u, out.getvalue().encode(), o


def test_cash_capex_reconciles_and_commitment_does_not_become_a_payment():
    w, u, c, o = capex_case()
    _, p, *_ = upload_case(w, u, content=c, choices=o)
    assert p["ready"]
    values = {
        m["key"]: Decimal(m["exact_value"])
        for m in p["calculated_metrics"]
        if m["status"] == "calculated"
    }
    assert {
        key: values[key]
        for key in (
            "capex_cash_total",
            "capex_cash_maintenance",
            "capex_cash_growth",
            "capex_commitments_outstanding",
        )
    } == {
        "capex_cash_total": Decimal(80),
        "capex_cash_maintenance": Decimal(30),
        "capex_cash_growth": Decimal(50),
        "capex_commitments_outstanding": Decimal(120),
    }
    assert values["capex_maintenance_share_pct"] == Decimal("37.5") and values[
        "capex_growth_share_pct"
    ] == Decimal("62.5")
    assert {r["rule_id"]: r["status"] for r in p["capex_reconciliations"]} == {
        "REC-CAPEX-001": "pass",
        "REC-CAPEX-002": "pass",
    }
    assert not any(
        "commitment" in key and key != "capex_commitments_outstanding" for key in values
    )


@pytest.mark.parametrize("concept", list(VALUES))
def test_missing_component_is_not_inferred_or_used_as_zero(concept):
    w, u, c, o = capex_case(changes={concept: None})
    _, p, *_ = upload_case(w, u, content=c, choices=o)
    if concept in ("capex_cash_total", "capex_cash_maintenance", "capex_cash_growth"):
        assert not any(
            m["key"] in ("capex_maintenance_share_pct", "capex_growth_share_pct")
            for m in p["calculated_metrics"]
        )
        assert (
            next(
                r for r in p["capex_reconciliations"] if r["rule_id"] == "REC-CAPEX-001"
            )["status"]
            == "not_planned"
        )
    if concept == "capex_commitments_outstanding":
        assert not any(m["key"] == concept for m in p["calculated_metrics"])
    if concept == "capex":
        assert (
            next(
                r for r in p["capex_reconciliations"] if r["rule_id"] == "REC-CAPEX-002"
            )["status"]
            == "not_planned"
        )


@pytest.mark.parametrize(
    "concept,value",
    [("capex", "-90"), ("capex", "80"), ("capex_cash_maintenance", "31")],
)
def test_independent_cash_or_partition_difference_blocks(concept, value):
    w, u, c, o = changed_case(concept, 1, value)
    _, p, *_ = upload_case(w, u, content=c, choices=o)
    assert not p["ready"]
    assert any(
        r["status"] not in ("pass", "not_planned") for r in p["capex_reconciliations"]
    )


@pytest.mark.parametrize(
    "field,value",
    [(1, "-1"), (1, ""), (2, "2025"), (5, "USD"), (6, "thousands"), (7, "comparative")],
)
def test_invalid_or_incompatible_paid_component_is_not_a_valid_share(field, value):
    w, u, c, o = changed_case("capex_cash_growth", field, value)
    _, p, *_ = upload_case(w, u, content=c, choices=o)
    assert not p["ready"] or not any(
        m["key"] == "capex_growth_share_pct" and m["status"] == "calculated"
        for m in p["calculated_metrics"]
    )


def test_zero_paid_cash_remains_zero_while_percentage_is_unavailable():
    w, u, c, o = capex_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    for loc, concept in o.mappings.items():
        if concept != "capex_commitments_outstanding":
            rows[int(loc.split(":")[1]) - 1][1] = "0"
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _, p, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert p["ready"]
    available = {m.get("key", m["metric_id"]): m for m in p["metric_availability"]}
    assert available["capex_growth_share_pct"]["status"] == "blocked" and available[
        "capex_growth_share_pct"
    ]["reason_codes"] == ["ZERO_DENOMINATOR"]
    assert not any(
        m["key"] == "capex_growth_share_pct" for m in p["calculated_metrics"]
    )


def test_review_is_private_source_only_with_partial_confirmation_retained():
    w, _u, c, o = capex_case(confirmed=False)
    with pytest.raises(CapexReviewRequired) as exc:
        parse_upload(w, "capex.csv", c, o)
    assert exc.value.payload["schema"] == "financial-capex-review-v1"
    assert "calculated_metrics" not in exc.value.payload
    assert len(exc.value.payload["rows"]) == 5
    loc = exc.value.payload["rows"][0]["location"]
    o = o.model_copy(update={"capex_definitions": {loc: PAID_BASIS}})
    with pytest.raises(CapexReviewRequired) as exc:
        parse_upload(w, "capex.csv", c, o)
    assert (
        next(r for r in exc.value.payload["rows"] if r["location"] == loc)[
            "capex_definition_quote"
        ]
        == PAID_BASIS
    )


@pytest.mark.parametrize("bad", ["foreign", "invented", "mismatch"])
def test_source_quotes_must_be_verbatim_scoped_and_consistent(bad):
    w, _u, c, o = capex_case()
    d = dict(o.capex_definitions)
    if bad == "foreign":
        d["csv:500"] = PAID_BASIS
    elif bad == "invented":
        d["csv:7"] = "invented definition"
    else:
        d["csv:8"] = "cash paid for the same fixed asset purchases in the period"
    with pytest.raises(InvalidUpload):
        parse_upload(w, "capex.csv", c, o.model_copy(update={"capex_definitions": d}))


@pytest.mark.parametrize("kind", ["missing_quote", "mismatch", "negative"])
def test_normalizer_independently_requires_capex_definitions(kind):
    from saleslab_v2.normalization.financial import FinancialNormalizer

    w, _u, c, o = capex_case()
    parsed = parse_upload(w, "capex.csv", c, o)
    rows = list(parsed.batch.rows)
    i = next(i for i, r in enumerate(rows) if r.source_reference == "csv:9")
    changes = (
        {"definition_quote": None}
        if kind == "missing_quote"
        else (
            {"definition_quote": "another cash capex basis"}
            if kind == "mismatch"
            else {"text": "-1"}
        )
    )
    rows[i] = replace(rows[i], **changes)
    obs = FinancialNormalizer().normalize(rows, parsed.batch.metadata)
    observed = next(v for v in obs if v.source_reference == "csv:9")
    assert observed.normalization_status == "needs_confirmation"
    assert any(i.code.startswith("capex.") for i in observed.issues)


def test_quote_identity_preview_and_exact_normalization_contract():
    w, u, c, o = capex_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    d = {
        loc: (
            quote
            if quote == COMMIT_BASIS
            else "cash paid for the same fixed asset purchases in the period"
        )
        for loc, quote in o.capex_definitions.items()
    }
    q, z, *_ = upload_case(
        w, u, content=c, choices=o.model_copy(update={"capex_definitions": d})
    )
    assert (
        p.batch.document.source_id != q.batch.document.source_id
        and v["preview_hash"] != z["preview_hash"]
    )
    with pytest.raises(ValueError):
        UploadChoices(**(o.model_dump() | {"normalization_version": "1"}))
    for version in map(str, range(1, 11)):
        data = {
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "planning_version": version,
            "normalization_version": "2",
        }
        assert "capex_definitions" not in UploadChoices(**data).contract_payload()
        with pytest.raises(ValueError):
            UploadChoices(**data, capex_definitions={})


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
def test_capex_methods_and_commitments_survive_audit_and_report_parity(language):
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    w, u, c, o = capex_case(language=language, report_language=language)
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"]
    request, _ = prepare_upload(p, o, u, "preview")
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, context, _, draft, audit, _) = report_fixture(
            language, growth=False
        )
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert len(context["capex_definitions"]) == 5
    assert "growth_definitions" not in context and "debt_definitions" not in context
    assert report["methodology"]["capex_definitions"] == context["capex_definitions"]
    for evidence in (
        context["evidence"],
        draft["financial_snapshot"],
        report["evidence"],
    ):
        assert {
            "capex_cash_total",
            "capex_cash_maintenance",
            "capex_cash_growth",
            "capex_commitments_outstanding",
            "capex_maintenance_share_pct",
            "capex_growth_share_pct",
        } <= {e["concept_id"] for e in evidence}
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert artifacts["parity"]["status"] == "pass"
    assert PAID_BASIS in artifacts["html"] and COMMIT_BASIS in artifacts["html"]


def test_duplicate_classification_does_not_select_a_source_silently():
    w, u, c, o = capex_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows.append(rows[8])
    loc = "csv:" + str(len(rows))
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    o = o.model_copy(
        update={
            "mappings": {**o.mappings, loc: "capex_cash_maintenance"},
            "capex_definitions": {**o.capex_definitions, loc: PAID_BASIS},
        }
    )
    _, p, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert not p["ready"] and not any(
        m["key"] == "capex_maintenance_share_pct" for m in p["calculated_metrics"]
    )


def test_explicit_cash_rounding_is_independent_and_bound_to_preview():
    w, u, c, o = changed_case("capex_cash_growth", 1, "50.01")
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    _, z, *_ = upload_case(
        w,
        u,
        content=c,
        choices=o.model_copy(update={"capex_rounding_tolerance": "0.01"}),
    )
    assert not v["ready"] and z["ready"] and v["preview_hash"] != z["preview_hash"]
    _, unchanged, *_ = upload_case(
        w,
        u,
        content=c,
        choices=o.model_copy(update={"cash_rounding_tolerance": "1000"}),
    )
    assert not unchanged["ready"]


def test_commitments_alone_do_not_require_or_infer_a_cash_partition():
    w, u, c, o = capex_case(
        changes={
            key: None
            for key in (
                "capex_cash_total",
                "capex_cash_maintenance",
                "capex_cash_growth",
            )
        }
    )
    _, p, *_ = upload_case(w, u, content=c, choices=o)
    assert p["ready"]
    values = {
        m["key"]: m["exact_value"]
        for m in p["calculated_metrics"]
        if m["status"] == "calculated"
    }
    assert Decimal(values["capex_commitments_outstanding"]) == 120
    assert not any(
        key.startswith("capex_cash_") or key.endswith("_share_pct") for key in values
    )
    assert all(r["status"] == "not_planned" for r in p["capex_reconciliations"])


def test_independent_cash_source_also_requires_the_shared_definition():
    w, _u, c, o = capex_case()
    d = dict(o.capex_definitions)
    del d["csv:7"]
    with pytest.raises(CapexReviewRequired) as exc:
        parse_upload(w, "capex.csv", c, o.model_copy(update={"capex_definitions": d}))
    assert any(r["concept_id"] == "capex" for r in exc.value.payload["rows"])
    from saleslab_v2.normalization.financial import FinancialNormalizer

    parsed = parse_upload(w, "capex.csv", c, o)
    rows = list(parsed.batch.rows)
    i = next(i for i, r in enumerate(rows) if r.source_reference == "csv:7")
    rows[i] = replace(rows[i], definition_quote=None)
    result = FinancialNormalizer().normalize(rows, parsed.batch.metadata)
    assert any(
        i.code == "capex.definition_required"
        for obs in result
        if obs.source_reference == "csv:7"
        for i in obs.issues
    )


def test_approved_casefolded_aliases_classify_methodology_by_source_identity():
    from report_fixtures import report_fixture

    from saleslab_v2.i18n.financial_v8 import CAPEX_LABELS

    w, u, c, o = capex_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    defs = {}
    mapping = {}
    for loc, concept in o.mappings.items():
        i = int(loc.split(":")[1]) - 1
        if concept == "capex":
            rows[i][
                0
            ] = "purchases of property plant and equipment | PURCHASES OF FIXED ASSETS"
            mapping[loc] = "capex"
            defs[loc] = "PURCHASES OF FIXED ASSETS"
        else:
            rows[i][0] = CAPEX_LABELS[concept][1].upper()
            defs[loc] = (
                "OUTSTANDING FIXED-ASSET PURCHASE COMMITMENTS"
                if concept == "capex_commitments_outstanding"
                else "PURCHASES OF FIXED ASSETS"
            )
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    p, v, *_ = upload_case(
        w,
        u,
        content=out.getvalue().encode(),
        choices=o.model_copy(update={"mappings": mapping, "capex_definitions": defs}),
    )
    assert v["ready"]
    request, _ = prepare_upload(
        p,
        o.model_copy(update={"mappings": mapping, "capex_definitions": defs}),
        u,
        "preview",
    )
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, context, _, _, audit, _) = report_fixture(growth=False)
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert (
        len(context["capex_definitions"]) == 5 and "growth_definitions" not in context
    )
    assert context["capex_definitions"] == report["methodology"]["capex_definitions"]


def test_complete_capital_growth_debt_capex_bundle_stays_within_report_limits():
    import json

    from analyst_fixtures import model_output
    from capex_upload_fixtures import complete_capex_case
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    w, u, c, o = complete_capex_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"]
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
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert (
        len(context["growth_definitions"]) == 8
        and len(context["debt_definitions"]) == 5
        and len(context["capex_definitions"]) == 5
    )
    assert len(json.dumps(context, ensure_ascii=True).encode()) < 256_000
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert (
        artifacts["parity"]["status"] == "pass"
        and artifacts["parity"]["page_count"] <= 120
    )
    assert PAID_BASIS in artifacts["html"] and COMMIT_BASIS in artifacts["html"]
