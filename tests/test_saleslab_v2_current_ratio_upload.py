"""Pinned balance-sheet classifications and plan 5 current-ratio evidence."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.i18n.catalog import FINANCIAL_TERMS, TerminologyRegistry
from saleslab_v2.i18n.financial_v2 import FINANCIAL_TERMS_V2, FinancialTerminologyV2
from saleslab_v2.normalization.financial import FinancialNormalizer
from saleslab_v2.pipeline.replay import restore_request
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.preview import LABELS

TERMS = {t.concept_id: t for t in FINANCIAL_TERMS_V2}


def choices(**updates):
    return UploadChoices(
        **{
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "planning_version": "5",
            "normalization_version": "2",
            **updates,
        }
    )


def row(
    concept,
    value,
    *,
    language="en",
    period="2026",
    currency="EUR",
    scale="raw",
    basis="current",
    label=None
):
    return (
        label or TERMS[concept].labels[LanguageCode(language)],
        value,
        period,
        "balance_sheet",
        "consolidated",
        currency,
        scale,
        basis,
    )


@pytest.mark.parametrize("source_language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize("report_language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize("scale", ("raw", "thousands", "millions"))
def test_ratio_has_explicit_classifications_localized_label_and_source_evidence(
    source_language, report_language, scale
):
    content = csv_content(
        language=source_language,
        changes={
            c: {"scale": scale}
            for c in (
                "revenue",
                "net_income",
                "total_assets",
                "total_liabilities",
                "equity",
            )
        },
        extra=[
            row("current_assets", "40", language=source_language, scale=scale),
            row("current_liabilities", "20", language=source_language, scale=scale),
        ],
    )
    parsed, preview, _, _, _ = upload_case(
        content=content,
        choices=choices(
            source_language=source_language, report_language=report_language
        ),
    )
    assert preview["ready"]
    assert parsed.batch.metadata.terminology_version == "2"
    metric = next(
        m for m in preview["calculated_metrics"] if m["metric_id"] == "current_ratio"
    )
    assert Decimal(metric["exact_value"]) == 2
    assert (
        metric["label"]
        == LABELS["current_ratio"][("pt", "en", "es", "fr").index(report_language)]
    )
    evidence = metric["calculation_evidence"]
    assert evidence["canonical_expression"] == "current_assets/current_liabilities"
    assert metric["source_references"] == ["csv:7", "csv:8"]
    assert {i["sources"][0]["scale"] for i in evidence["inputs"]} == {scale}
    assert {i["sources"][0]["amount"] for i in evidence["inputs"]} == {"40", "20"}
    assert all(
        r["terminology_version"] == "financial-terminology-v2" for r in preview["rows"]
    )
    assert "current_assets" in {o["concept_id"] for o in preview["mapping_options"]}


def test_new_registry_preserves_all_old_entries_without_mutating_version_one():
    old, new = TerminologyRegistry(), FinancialTerminologyV2()
    assert FINANCIAL_TERMS_V2[: len(FINANCIAL_TERMS)] == FINANCIAL_TERMS
    assert len(FINANCIAL_TERMS_V2) == len(FINANCIAL_TERMS) + 2
    for entry in FINANCIAL_TERMS:
        for language, aliases in entry.aliases.items():
            for alias in aliases:
                assert (
                    old.lookup(alias, language, entry.statement_type)
                    == new.lookup(alias, language, entry.statement_type)
                    == entry
                )
    for concept in ("current_assets", "current_liabilities"):
        for language, aliases in TERMS[concept].aliases.items():
            assert old.lookup(aliases[0], language, "balance_sheet") is None
            assert (
                new.lookup(aliases[0], language, "balance_sheet").concept_id == concept
            )


@pytest.mark.parametrize(
    "extra,concept,state",
    (
        ([], "current_assets", "missing"),
        ([row("current_assets", "40")], "current_liabilities", "missing"),
        (
            [
                row("current_assets", "40"),
                row("current_assets", "50"),
                row("current_liabilities", "20"),
            ],
            "current_assets",
            "ambiguous",
        ),
        (
            [row("current_assets", "(40)"), row("current_liabilities", "20")],
            "current_assets",
            "needs_confirmation",
        ),
        (
            [
                row("current_assets", "40", period="2025", basis="comparative"),
                row("current_liabilities", "20"),
            ],
            "current_assets",
            "missing",
        ),
    ),
)
def test_totals_missing_duplicate_and_unconfirmed_balances_do_not_supply_current_operands(
    extra, concept, state
):
    _, preview, _, _, _ = upload_case(
        content=csv_content(extra=extra), choices=choices()
    )
    assert "current_ratio" not in preview["planned_metrics"]
    gap = next(
        m for m in preview["metric_availability"] if m["metric_id"] == "current_ratio"
    )
    assert (
        next(r for r in gap["requirements"] if r["concept_id"] == concept)["status"]
        == state
    )


@pytest.mark.parametrize(
    "liabilities,currency,period,expected",
    (
        ("0", "EUR", "2026", "blocked"),
        ("20", "USD", "2026", "blocked"),
        ("20", "EUR", "2025", "period_required"),
    ),
)
def test_denominator_currency_and_unselected_period_cannot_produce_ratio(
    liabilities, currency, period, expected
):
    _, preview, _, _, _ = upload_case(
        content=csv_content(
            extra=[
                row("current_assets", "40"),
                row(
                    "current_liabilities", liabilities, currency=currency, period=period
                ),
            ]
        ),
        choices=choices(),
    )
    gap = next(
        m for m in preview["metric_availability"] if m["metric_id"] == "current_ratio"
    )
    assert gap["status"] == expected
    assert not preview["ready"]


def test_mixed_original_scales_are_normalized_and_zero_assets_are_not_missing():
    for assets, expected in (("0.04", Decimal(2)), ("0", Decimal(0))):
        _, preview, _, _, _ = upload_case(
            content=csv_content(
                extra=[
                    row("current_assets", assets, scale="thousands"),
                    row("current_liabilities", "20"),
                ]
            ),
            choices=choices(),
        )
        metric = next(
            m
            for m in preview["calculated_metrics"]
            if m["metric_id"] == "current_ratio"
        )
        assert Decimal(metric["exact_value"]) == expected


def test_explicit_mapping_uses_only_the_selected_catalog_and_statement_context():
    content = csv_content(
        extra=[
            row("current_assets", "40", label="Our short-term assets"),
            row("current_liabilities", "20", label="Our short-term obligations"),
        ]
    )
    opts = choices(mappings={"csv:7": "current_assets", "csv:8": "current_liabilities"})
    _, preview, _, _, _ = upload_case(content=content, choices=opts)
    assert preview["ready"]
    assert (
        next(r for r in preview["rows"] if r["location"] == "csv:7")["original_input"][
            "line"
        ]
        == "Our short-term assets"
    )
    with pytest.raises(InvalidUpload, match="mapping must match"):
        upload_case(
            content=content, choices=opts.model_copy(update={"planning_version": "4"})
        )
    bad = content.replace(
        b"Our short-term assets;40;2026;balance_sheet",
        b"Our short-term assets;40;2026;income_statement",
    )
    with pytest.raises(InvalidUpload, match="mapping must match"):
        upload_case(content=bad, choices=opts)


@pytest.mark.parametrize("version", ("1", "2", "3", "4", "5"))
def test_catalog_pin_round_trip_and_default_omission_preserve_older_contracts(version):
    opts = choices(planning_version=version)
    parsed, preview, _, _, user = upload_case(choices=opts)
    request, _ = prepare_upload(parsed, opts, user, "preview")
    metadata = request.semantic_payload()["sources"][0]["metadata"]
    if version == "5":
        assert metadata["terminology_version"] == "2"
        assert len(preview["metric_availability"]) == 9
    else:
        assert "terminology_version" not in metadata
        assert "current_ratio" not in [
            m["metric_id"] for m in preview["metric_availability"]
        ]
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
    assert restored.fingerprint == request.snapshot().fingerprint == request.fingerprint
    assert restored.sources[0].metadata.terminology_version == (
        "2" if version == "5" else "1"
    )


def test_plan_four_hash_recorded_before_catalog_extension_is_unchanged():
    w = UUID("10000000-0000-4000-8000-000000000001")
    u = UUID("10000000-0000-4000-8000-000000000002")
    opts = choices(planning_version="4")
    parsed, preview, _, _, _ = upload_case(w, u, choices=opts)
    request, _ = prepare_upload(parsed, opts, u, "preview")
    assert (
        preview["preview_hash"]
        == "c5b5894678f98e72e776eb9e873c1989032c8086d6efe02c2694b4b490b62bfe"
    )
    assert (
        request.fingerprint
        == "38f48134f946dc59c2d4c152a3030b2155776b576424bbd92599ac9365d08863"
    )


def test_same_normalizer_switches_catalogs_without_leaking_current_classifications():
    parsed, *_ = upload_case(
        content=csv_content(extra=[row("current_assets", "40")]), choices=choices()
    )
    normalizer = FinancialNormalizer()
    new = normalizer.normalize(list(parsed.batch.rows), parsed.batch.metadata)
    old = normalizer.normalize(
        list(parsed.batch.rows), replace(parsed.batch.metadata, terminology_version="1")
    )
    assert (
        next(r for r in new if r.source_reference == "csv:7").concept_id
        == "current_assets"
    )
    assert next(r for r in old if r.source_reference == "csv:7").concept_id is None


@pytest.mark.parametrize("invalid", (None, 2, True, "11", "", []))
def test_unknown_source_catalog_versions_are_rejected(invalid):
    parsed, *_ = upload_case()
    with pytest.raises(ValueError):
        replace(parsed.batch.metadata, terminology_version=invalid)


def test_report_context_and_methodology_use_actual_catalog_without_changing_legacy_context():
    from unittest.mock import patch

    from pipeline_fixtures import request
    from report_fixtures import report_fixture

    old, (_, _, old_context, *_) = report_fixture()
    assert "terminology_versions" not in old_context
    assert old["methodology"]["terminology_version"] == "financial-terminology-v1"
    value = request(language="pt")
    value = replace(
        value,
        sources=tuple(
            replace(b, metadata=replace(b.metadata, terminology_version="2"))
            for b in value.sources
        ),
    )
    with patch("report_fixtures.request", return_value=value):
        report, (_, _, context, *_) = report_fixture()
    assert context["terminology_versions"] == ["financial-terminology-v2"]
    assert report["methodology"]["terminology_version"] == "financial-terminology-v2"
