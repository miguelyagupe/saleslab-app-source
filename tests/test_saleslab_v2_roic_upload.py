"""ROIC source semantics, exact arithmetic, audit, channels and replay."""

import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest
from roic_upload_fixtures import AVERAGE, NOPAT, TAX, roic_case, upload_case

from saleslab_v2.normalization.financial import FinancialNormalizer
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.roic_source import RoicReviewRequired


def test_reported_roic_is_exact_and_not_annualized_or_inferred_from_ebit():
    w, u, c, o = roic_case()
    parsed, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"]
    result = next(m for m in v["calculated_metrics"] if m["key"] == "roic")
    assert Decimal(result["exact_value"]) == 20
    request, _ = prepare_upload(parsed, o, u, "preview")
    assert next(m for m in request.metrics if m.key == "roic").version == "2"
    assert (
        len(json.loads(parsed.batch.metadata.roic_binding)["binding"]["evidence"]) == 3
    )


@pytest.mark.parametrize(
    "field,value",
    [
        (1, "0"),
        (1, "-400"),
        (1, "400%"),
        (1, ""),
        (2, "2025"),
        (5, "USD"),
        (7, "comparative"),
    ],
)
def test_invalid_average_or_incompatible_base_never_produces_roic(field, value):
    w, u, c, o = roic_case(changes={"average_invested_capital": {field: value}})
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    assert not v["ready"]
    assert not any(
        m["key"] == "roic" and m["status"] == "calculated"
        for m in v["calculated_metrics"]
    )
    available = next(m for m in v["metric_availability"] if m.get("key") == "roic")
    assert available["status"] != "calculated"


@pytest.mark.parametrize(
    "amount,result",
    [
        ("0", "0"),
        ("-80", "-20"),
        ("80.000000000000000000000000000004", "20.000000000000000000000000000001"),
    ],
)
def test_zero_negative_and_high_precision_nopat_are_preserved(amount, result):
    w, u, c, o = roic_case(changes={"nopat_with_explicit_tax_assumptions": {1: amount}})
    _, v, *_ = upload_case(w, u, content=c, choices=o)
    assert v["ready"]
    assert Decimal(
        next(m["exact_value"] for m in v["calculated_metrics"] if m["key"] == "roic")
    ) == Decimal(result)


def test_missing_sources_requires_private_review_without_calculation():
    w, _u, c, o = roic_case(confirmed=False)
    with pytest.raises(RoicReviewRequired) as exc:
        parse_upload(w, "statements.csv", c, o)
    assert exc.value.payload["schema"] == "financial-roic-review-v1"
    assert (
        not exc.value.payload["ready"] and "calculated_metrics" not in exc.value.payload
    )
    assert len(exc.value.payload["required_roles"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"nopat_location": "csv:3"},
        {"tax_assumptions_location": "csv:500"},
        {"nopat_definition": "invented NOPAT definition"},
        {"average_capital_location": "csv:4"},
    ],
)
def test_source_selection_and_quotations_must_match_selected_roles(change):
    w, _u, c, o = roic_case()
    selected = o.roic_sources.model_copy(update=change)
    with pytest.raises(InvalidUpload):
        parse_upload(
            w, "statements.csv", c, o.model_copy(update={"roic_sources": selected})
        )


@pytest.mark.parametrize(
    "tamper",
    ["missing", "binding", "source_hash", "amount", "period", "mapping", "duplicate"],
)
def test_normalizer_independently_rejects_stale_or_unbound_evidence(tamper):
    w, _u, c, o = roic_case()
    p = parse_upload(w, "statements.csv", c, o)
    rows = list(p.batch.rows)
    meta = p.batch.metadata
    i = next(i for i, r in enumerate(rows) if r.source_reference == "csv:7")
    if tamper == "missing":
        meta = replace(meta, roic_binding=None)
    elif tamper in ("binding", "source_hash"):
        evidence = json.loads(meta.roic_binding)
        if tamper == "binding":
            evidence["binding"]["choices"]["tax_assumptions"] = "fabricated assumption"
        else:
            evidence["source_hash"] = "0" * 64
        meta = replace(meta, roic_binding=json.dumps(evidence))
    elif tamper == "amount":
        rows[i] = replace(rows[i], text="100")
    elif tamper == "period":
        rows[i] = replace(rows[i], period_label="2025")
    elif tamper == "mapping":
        rows[i] = replace(rows[i], name="not a confirmed NOPAT operand")
    else:
        rows.append(replace(rows[i], source_reference="csv:9"))
    obs = FinancialNormalizer().normalize(rows, meta)
    assert any(
        o.concept_id == "average_invested_capital"
        and o.normalization_status == "needs_confirmation"
        for o in obs
    )


def test_contract_preserves_old_payload_and_requires_exact_normalization():
    _, _, _, o = roic_case()
    for v in map(str, range(1, 12)):
        data = o.model_dump() | {"planning_version": v, "roic_sources": None}
        assert "roic_sources" not in UploadChoices(**data).contract_payload()
        with pytest.raises(ValueError):
            UploadChoices(**(data | {"roic_sources": o.roic_sources.model_dump()}))
    with pytest.raises(ValueError):
        UploadChoices(**(o.model_dump() | {"normalization_version": "1"}))


def test_changed_quote_changes_source_identity_and_preview_even_with_same_values():
    w, u, c, o = roic_case()
    p, v, *_ = upload_case(w, u, content=c, choices=o)
    changed = o.model_copy(
        update={
            "roic_sources": o.roic_sources.model_copy(
                update={
                    "nopat_definition": "reported operating profit after operating taxes"
                }
            )
        }
    )
    q, z, *_ = upload_case(w, u, content=c, choices=changed)
    assert (
        p.batch.document.source_id != q.batch.document.source_id
        and v["preview_hash"] != z["preview_hash"]
    )


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
def test_audited_roic_report_contains_tax_and_average_evidence_in_both_channels(
    language,
):
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    w, u, c, o = roic_case(language=language, report_language=language)
    p, _v, *_ = upload_case(w, u, content=c, choices=o)
    request, _ = prepare_upload(p, o, u, "preview")
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, context, _, draft, audit, _) = report_fixture(
            language, growth=False
        )
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert report["methodology"]["roic_sources"] == context["roic_sources"]
    assert not any(
        key in context
        for key in ("growth_definitions", "capex_definitions", "debt_definitions")
    )
    for evidence in [
        context["evidence"],
        draft["financial_snapshot"],
        report["evidence"],
    ]:
        assert "roic" in {e["concept_id"] for e in evidence}
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert artifacts["parity"]["status"] == "pass"
    for text in [NOPAT, TAX, AVERAGE]:
        assert text in artifacts["html"]


def test_json_roundtrip_restores_evidence_and_identical_request_fingerprint():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from saleslab_v2.domain.idempotency import canonicalize
    from saleslab_v2.pipeline.replay import restore_request

    w, u, c, o = roic_case()
    p, _v, *_ = upload_case(w, u, content=c, choices=o)
    request, _ = prepare_upload(p, o, u, "preview")
    restored = restore_request(
        json.loads(json.dumps(canonicalize(request.semantic_payload()))),
        run=SimpleNamespace(
            created_at=datetime.now(UTC),
            workspace_id=w,
            created_by_user_id=u,
            idempotency_key="preview",
            language_context={"format_locale": o.format_locale},
        ),
    )
    assert restored.fingerprint == request.fingerprint
    observations = FinancialNormalizer().normalize(
        list(restored.sources[0].rows), restored.sources[0].metadata
    )
    assert all(not o.issues for o in observations)


def test_explicit_scales_are_normalized_and_monthly_roic_is_not_annualized():
    w, u, c, o = roic_case(
        changes={
            "nopat_with_explicit_tax_assumptions": {2: "March 2026"},
            "average_invested_capital": {1: "0.4", 2: "March 2026", 6: "thousands"},
        }
    )
    _, v, *_ = upload_case(
        w, u, content=c, choices=o.model_copy(update={"target_period": "March 2026"})
    )
    assert v["ready"]
    assert (
        Decimal(
            next(
                m["exact_value"] for m in v["calculated_metrics"] if m["key"] == "roic"
            )
        )
        == 20
    )


def test_roic_absent_from_source_remains_an_explicit_gap_without_inventing_values():
    from product_fixtures import csv_content

    _, _, _, o = roic_case()
    _, v, *_ = upload_case(
        content=csv_content(),
        choices=o.model_copy(update={"mappings": {}, "roic_sources": None}),
    )
    assert v["ready"]
    available = next(m for m in v["metric_availability"] if m.get("key") == "roic")
    assert available["status"] == "not_planned"
    assert {r["concept_id"] for r in available["requirements"]} == {
        "nopat_with_explicit_tax_assumptions",
        "average_invested_capital",
    }


def test_complete_financial_bundle_with_roic_preserves_audit_and_channel_limits():
    import csv
    import io

    from analyst_fixtures import model_output
    from capex_upload_fixtures import complete_capex_case
    from report_fixtures import report_fixture

    from saleslab_v2.product.roic_contracts import RoicSourceChoices
    from saleslab_v2.reports.render import render_channels

    w, u, c, o = complete_capex_case()
    _, _, rc, ro = roic_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    extra = list(csv.reader(io.StringIO(rc.decode()), delimiter=";"))
    mappings = dict(o.mappings)
    locations = {}
    for old, concept in ro.mappings.items():
        loc = "csv:" + str(len(rows) + 1)
        locations[concept] = loc
        mappings[loc] = concept
        rows.append(extra[int(old.split(":")[1]) - 1])
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    nopat = locations["nopat_with_explicit_tax_assumptions"]
    o = o.model_copy(
        update={
            "planning_version": "12",
            "mappings": mappings,
            "roic_sources": RoicSourceChoices(
                nopat_location=nopat,
                nopat_definition=NOPAT,
                tax_assumptions_location=nopat,
                tax_assumptions=TAX,
                average_capital_location=locations["average_invested_capital"],
                average_capital_definition=AVERAGE,
            ),
        }
    )
    p, v, *_ = upload_case(w, u, content=out.getvalue().encode(), choices=o)
    assert v["ready"]
    assert "ebit_margin_pct" in v["planned_metrics"]
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
    assert len(json.dumps(context, ensure_ascii=True).encode()) < 256_000
    assert len(context["roic_sources"]) == 1
    assert {"capex_definitions", "debt_definitions", "growth_definitions"} <= set(
        context
    )
    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert (
        artifacts["parity"]["status"] == "pass"
        and artifacts["parity"]["page_count"] <= 120
    )
    assert TAX in artifacts["html"]


def test_oversized_unicode_evidence_is_a_validation_error_not_an_internal_error():
    import csv
    import io

    w, _u, c, o = roic_case()
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    quote = "á" * 1800
    rows[6][0] = "Reported NOPAT | " + quote
    rows[7][0] = "Average capital | " + quote
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    source = o.roic_sources.model_copy(
        update={
            "nopat_definition": quote,
            "tax_assumptions": quote,
            "average_capital_definition": quote,
        }
    )
    with pytest.raises(InvalidUpload, match="64 KB"):
        parse_upload(
            w,
            "statements.csv",
            out.getvalue().encode(),
            o.model_copy(update={"roic_sources": source}),
        )


def test_internal_roic_metadata_cannot_use_the_legacy_number_parser():
    w, _u, c, o = roic_case()
    p = parse_upload(w, "statements.csv", c, o)
    with pytest.raises(ValueError, match="exact normalization"):
        replace(p.batch.metadata, normalization_version="1")


@pytest.mark.parametrize("first", ["csv:7", "csv:8"])
def test_partial_roic_mapping_keeps_ordinary_preview_reachable_before_source_review(
    first,
):
    w, u, c, o = roic_case()
    partial = o.model_copy(
        update={"mappings": {first: o.mappings[first]}, "roic_sources": None}
    )
    _p, v, *_ = upload_case(w, u, content=c, choices=partial)
    assert not v["ready"]
    assert any(row["concept_id"] is None for row in v["rows"])
    assert {option["concept_id"] for option in v["mapping_options"]} >= {
        "nopat_with_explicit_tax_assumptions",
        "average_invested_capital",
    }
    with pytest.raises(RoicReviewRequired):
        parse_upload(
            w, "statements.csv", c, o.model_copy(update={"roic_sources": None})
        )
    assert upload_case(w, u, content=c, choices=o)[1]["ready"]
