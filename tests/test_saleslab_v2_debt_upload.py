"""Aggregate rate/maturity source confirmation through audited report output."""

from decimal import Decimal
from unittest.mock import patch

import pytest
from debt_fixtures import BASIS, VALUES, debt_case, upload_case

from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.debt_source import DebtReviewRequired
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.planning import prepare_upload


def test_debt_partitions_calculate_and_reconcile():
    w, u, c, o = debt_case()
    _, p, *_ = upload_case(w, u, content=c, choices=o)
    assert p["ready"]
    values = {
        m["key"]: Decimal(m["exact_value"])
        for m in p["calculated_metrics"]
        if m["status"] == "calculated"
    }
    assert values["fixed_variable_mix"] == 60
    assert values["variable_rate_share_pct"] == 40
    assert values["maturity_concentration"] == 25
    assert {r["rule_id"]: r["status"] for r in p["debt_reconciliations"]} == {
        "REC-DEBT-RATE-001": "pass",
        "REC-DEBT-MATURITY-001": "pass",
    }


@pytest.mark.parametrize("concept", list(VALUES))
def test_missing_principal_is_never_zero_or_inferred(concept):
    w, u, c, o = debt_case(changes={concept: None})
    _, p, *_ = upload_case(w, u, content=c, choices=o)
    available = {m.get("key", m["metric_id"]): m for m in p["metric_availability"]}
    keys = (
        ["fixed_variable_mix", "variable_rate_share_pct"]
        if concept in {"debt_fixed_principal", "debt_variable_principal"}
        else (
            ["maturity_concentration"]
            if concept != "debt_eligible_principal"
            else [
                "fixed_variable_mix",
                "variable_rate_share_pct",
                "maturity_concentration",
            ]
        )
    )
    for key in keys:
        assert available[key]["status"] == "not_planned"
        assert concept in {r["concept_id"] for r in available[key]["requirements"]}


@pytest.mark.parametrize(
    "field,value", [(1, "-1"), (1, ""), (2, "2025"), (5, "USD"), (7, "comparative")]
)
def test_invalid_or_incompatible_principal_blocks_partition(field, value):
    w, u, c, o = debt_case()
    import csv
    import io

    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows[7][field] = value
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _, p, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert (
        not any(
            m["key"] == "fixed_variable_mix" and m["status"] == "calculated"
            for m in p["calculated_metrics"]
        )
        or not p["ready"]
    )


@pytest.mark.parametrize(
    "concept", ["debt_fixed_principal", "debt_due_after_12_months"]
)
def test_material_partition_residual_blocks_publication(concept):
    w, u, c, o = debt_case()
    import csv
    import io

    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    loc = next(loc for loc, key in o.mappings.items() if key == concept)
    rows[int(loc.split(":")[1]) - 1][1] = "10"
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _, p, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert not p["ready"]
    assert any(r["status"] != "pass" for r in p["debt_reconciliations"])


def test_review_is_source_only_and_partial_quotes_survive():
    w, _u, c, o = debt_case(confirmed=False)
    with pytest.raises(DebtReviewRequired) as exc:
        parse_upload(w, "debt.csv", c, o)
    review = exc.value.payload
    assert review["schema"] == "financial-debt-review-v1" and not review["ready"]
    assert len(review["rows"]) == 5 and "calculated_metrics" not in review
    loc = review["rows"][0]["location"]
    o = o.model_copy(update={"debt_definitions": {loc: BASIS}})
    with pytest.raises(DebtReviewRequired) as exc:
        parse_upload(w, "debt.csv", c, o)
    assert (
        next(r for r in exc.value.payload["rows"] if r["location"] == loc)[
            "debt_definition_quote"
        ]
        == BASIS
    )


@pytest.mark.parametrize("bad", ["foreign row", "not in source"])
def test_foreign_or_unquoted_confirmation_rejected(bad):
    w, _u, c, o = debt_case()
    d = dict(o.debt_definitions)
    if bad == "foreign row":
        d["csv:500"] = "source quote"
    else:
        d[next(iter(d))] = "invented basis"
    with pytest.raises(InvalidUpload):
        parse_upload(w, "debt.csv", c, o.model_copy(update={"debt_definitions": d}))


def test_old_plans_reject_new_choices_and_keep_payload_omitted():
    for version in map(str, range(1, 10)):
        data = {
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "planning_version": version,
            "normalization_version": "2",
        }
        assert "debt_definitions" not in UploadChoices(**data).contract_payload()
        with pytest.raises(ValueError):
            UploadChoices(**data, debt_definitions={})


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
def test_debt_definitions_reach_independent_audit_and_all_report_formats(language):
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    w, u, c, o = debt_case(language=language, report_language=language)
    parsed, _, *_ = upload_case(w, u, content=c, choices=o)
    request, _ = prepare_upload(parsed, o, u, "preview")
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, context, _, draft, audit, _) = report_fixture(
            language, growth=False
        )
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert len(report["methodology"]["debt_definitions"]) == 5
    assert report["methodology"]["debt_definitions"] == context["debt_definitions"]
    assert all(d["definition_quote"] == BASIS for d in context["debt_definitions"])
    for evidence in [
        context["evidence"],
        draft["financial_snapshot"],
        report["evidence"],
    ]:
        assert {
            "fixed_variable_mix",
            "variable_rate_share_pct",
            "maturity_concentration",
        } <= {e["concept_id"] for e in evidence}
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert artifacts["parity"]["status"] == "pass"
    assert BASIS in artifacts["html"]


@pytest.mark.parametrize("kind", ["missing_quote", "different_quote", "negative"])
def test_core_normalizer_independently_enforces_debt_confirmations(kind):
    from dataclasses import replace

    from saleslab_v2.normalization.financial import FinancialNormalizer

    w, _u, c, o = debt_case()
    parsed = parse_upload(w, "debt.csv", c, o)
    rows = list(parsed.batch.rows)
    i = next(i for i, r in enumerate(rows) if r.source_reference == "csv:8")
    changes = (
        {"definition_quote": None}
        if kind == "missing_quote"
        else (
            {"definition_quote": "another explicit principal basis"}
            if kind == "different_quote"
            else {"text": "-1"}
        )
    )
    rows[i] = replace(rows[i], **changes)
    observations = FinancialNormalizer().normalize(rows, parsed.batch.metadata)
    observed = next(v for v in observations if v.source_reference == "csv:8")
    assert observed.normalization_status == "needs_confirmation"
    assert any(i.code.startswith("debt.") for i in observed.issues)


def test_source_quote_is_bound_to_identity_and_preview_even_with_same_numbers():
    w, u, c, o = debt_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    other = o.model_copy(
        update={
            "debt_definitions": {
                loc: "outstanding principal excluding leases at reporting date"
                for loc in o.debt_definitions
            }
        }
    )
    q, z, *_ = upload_case(w, u, content=c, choices=other)
    assert p.batch.document.source_id != q.batch.document.source_id
    assert v["preview_hash"] != z["preview_hash"]


@pytest.mark.parametrize("amount", ["0", "-1"])
def test_nonpositive_total_does_not_produce_debt_percentages(amount):
    import csv
    import io

    w, u, c, o = debt_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    for row in rows[6:]:
        row[1] = amount
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _, p, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert not p["ready"]
    assert not any(
        m["key"] == "fixed_variable_mix" and m["status"] == "calculated"
        for m in p["calculated_metrics"]
    )


def test_duplicate_schedule_candidates_are_not_silently_selected():
    import csv
    import io

    w, u, c, o = debt_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows.append(rows[7])
    loc = "csv:" + str(len(rows))
    mapping = {**o.mappings, loc: "debt_fixed_principal"}
    definitions = {**o.debt_definitions, loc: BASIS}
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    _, p, *_ = upload_case(
        w,
        u,
        content=out.getvalue().encode(),
        choices=o.model_copy(
            update={"mappings": mapping, "debt_definitions": definitions}
        ),
    )
    assert not p["ready"]
    assert not any(m["key"] == "fixed_variable_mix" for m in p["calculated_metrics"])


def test_explicit_rounding_allowance_is_base_currency_and_bound_to_preview():
    import csv
    import io

    w, u, c, o = debt_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows[7][1] = "600.01"
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    content = out.getvalue().encode()
    _, v, *_ = upload_case(w, u, content=content, choices=o)
    _, z, *_ = upload_case(
        w,
        u,
        content=content,
        choices=o.model_copy(update={"debt_rounding_tolerance": "0.01"}),
    )
    assert not v["ready"] and z["ready"]
    assert v["preview_hash"] != z["preview_hash"]


def test_new_plan_requires_exact_normalization():
    _w, _u, _c, o = debt_case()
    with pytest.raises(ValueError):
        UploadChoices(**(o.model_dump() | {"normalization_version": "1"}))


def test_casefolded_approved_source_aliases_keep_debt_methodology_classification():
    import csv
    import io

    from report_fixtures import report_fixture

    from saleslab_v2.i18n.financial_v7 import DEBT_LABELS

    w, u, c, o = debt_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    for loc, concept in o.mappings.items():
        rows[int(loc.split(":")[1]) - 1][0] = DEBT_LABELS[concept][1].upper()
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    o = o.model_copy(
        update={
            "mappings": {},
            "debt_definitions": {
                loc: "PRINCIPAL EXCLUDING LEASES" for loc in o.debt_definitions
            },
        }
    )
    parsed, v, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert v["ready"]
    request, _ = prepare_upload(parsed, o, u, "preview")
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, context, _, _, audit, _) = report_fixture(growth=False)
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert len(context["debt_definitions"]) == 5
    assert "growth_definitions" not in context
    assert report["methodology"]["debt_definitions"] == context["debt_definitions"]
