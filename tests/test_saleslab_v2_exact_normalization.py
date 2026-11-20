"""Pinned exact-sign parsing without upgrading legacy requests or replay."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, Inexact, Rounded, localcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.normalization.exact_numbers import ExactLocaleNumberParser
from saleslab_v2.normalization.financial import FinancialNormalizer
from saleslab_v2.normalization.numbers import LocaleNumberParser
from saleslab_v2.pipeline.replay import restore_request
from saleslab_v2.product.api import choices_from_json
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.planning import prepare_upload

DIGITS = "123456789012345678901234567890.123456789"


def choices(**updates):
    return UploadChoices(
        **{
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "normalization_version": "2",
            **updates,
        }
    )


@pytest.mark.parametrize("precision", (1, 6, 28, 100))
@pytest.mark.parametrize(
    "locale,decimal", (("pt-PT", ","), ("en-GB", "."), ("es-ES", ","), ("fr-FR", ","))
)
@pytest.mark.parametrize("notation", ("minus", "parentheses"))
def test_exact_sign_ignores_precision_traps_and_exponent_bounds(
    precision, locale, decimal, notation
):
    raw = DIGITS.replace(".", decimal)
    raw = "-" + raw if notation == "minus" else "(" + raw + ")"
    with localcontext() as context:
        context.prec = precision
        context.Emax = 3
        context.Emin = -3
        context.clamp = 1
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        before = (
            context.prec,
            context.Emax,
            context.Emin,
            context.clamp,
            context.traps.copy(),
            context.flags.copy(),
        )
        result = ExactLocaleNumberParser().parse(
            raw, locale=locale, accounting_parentheses=True
        )
        assert result.value == Decimal("-" + DIGITS)
        assert result.issues == ()
        assert result.sign == "negative"
        after = (
            context.prec,
            context.Emax,
            context.Emin,
            context.clamp,
            context.traps.copy(),
            context.flags.copy(),
        )
        assert before == after


@pytest.mark.parametrize(
    "raw,confirmation",
    (
        (None, None),
        ("N/A", None),
        ("-", None),
        ("100.00", None),
        ("(100)", None),
        ("(-100)", True),
        ("(100)", False),
        ("1,23.00", None),
        ("USD $100", True),
        ("100 million", None),
        ("-100%", None),
        ("(100%)", True),
    ),
)
def test_frozen_grammar_currency_scale_null_and_sign_checks_are_shared(
    raw, confirmation
):
    options = {"locale": "en-GB", "accounting_parentheses": confirmation}
    with localcontext() as context:
        context.prec = 100
        assert ExactLocaleNumberParser().parse(
            raw, **options
        ) == LocaleNumberParser().parse(raw, **options)


@pytest.mark.parametrize("precision", (6, 28, 100))
@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize(
    "scale,factor", (("raw", 1), ("thousands", 1000), ("millions", 1000000))
)
def test_product_v2_preserves_all_source_digits_and_scale_with_pinned_observation_version(
    precision, language, scale, factor
):
    raw = "(" + DIGITS + ")"
    with localcontext() as context:
        context.prec = precision
        parsed, preview, _, _, _ = upload_case(
            content=csv_content(
                changes={"net_income": {"amount": raw, "scale": scale}}
            ),
            choices=choices(report_language=language, accounting_parentheses=True),
        )
        assert preview["ready"]
        row = next(r for r in preview["rows"] if r["concept_id"] == "net_income")
        assert row["parser_version"] == "financial-normalizer-v2"
        assert row["original_input"]["amount"] == raw
        assert parsed.batch.metadata.normalization_version == "2"
    with localcontext() as context:
        context.prec = 100
        assert Decimal(row["value"]) == Decimal("-" + DIGITS) * factor


def test_legacy_context_sensitive_result_and_explicit_guard_remain_unchanged():
    with localcontext() as context:
        context.prec = 28
        legacy = LocaleNumberParser().parse("-" + DIGITS, locale="en-GB")
        assert legacy.value == Decimal("-1.234567890123456789012345679E+29")
        assert ExactLocaleNumberParser().parse(
            "-" + DIGITS, locale="en-GB"
        ).value == Decimal("-" + DIGITS)
        with pytest.raises(InvalidUpload, match="supported precision"):
            upload_case(
                content=csv_content(
                    changes={"net_income": {"amount": "(" + DIGITS + ")"}}
                ),
                choices=choices(normalization_version="1", accounting_parentheses=True),
            )
        parsed, _, _, _, _ = upload_case(
            content=csv_content(changes={"net_income": {"amount": "-" + DIGITS}}),
            choices=choices(normalization_version="1"),
        )
        obs = FinancialNormalizer().normalize(
            list(parsed.batch.rows), parsed.batch.metadata
        )
        assert (
            next(r for r in obs if r.concept_id == "net_income").value == legacy.value
        )


@pytest.mark.parametrize("version", ("1", "2"))
def test_request_serialization_snapshot_and_replay_pin_version_and_keep_legacy_shape(
    version,
):
    opts = choices(normalization_version=version)
    parsed, preview, _, _, user = upload_case(choices=opts)
    request, _ = prepare_upload(parsed, opts, user, "preview")
    metadata = request.semantic_payload()["sources"][0]["metadata"]
    if version == "1":
        assert "normalization_version" not in metadata
        assert "normalization_version" not in opts.contract_payload()
    else:
        assert (
            metadata["normalization_version"]
            == opts.contract_payload()["normalization_version"]
            == "2"
        )
    run = SimpleNamespace(
        workspace_id=request.workspace_id,
        created_by_user_id=user,
        idempotency_key="preview",
        created_at=datetime.now(UTC),
        language_context={"format_locale": "en-GB"},
    )
    restored = restore_request(canonicalize(request.semantic_payload()), run=run)
    assert restored.fingerprint == request.snapshot().fingerprint == request.fingerprint
    assert restored.sources[0].metadata.normalization_version == version
    assert (
        prepare_upload(parsed, opts, user, "preview")[1]["preview_hash"]
        == preview["preview_hash"]
    )


def test_version_alone_changes_bindings_without_changing_file_or_small_values():
    workspace, user = uuid4(), uuid4()
    hashes, request_hashes = set(), set()
    for version in ("1", "2"):
        parsed, preview, _, _, _ = upload_case(
            workspace, user, choices=choices(normalization_version=version)
        )
        request, _ = prepare_upload(
            parsed, choices(normalization_version=version), user, "preview"
        )
        hashes.add(preview["preview_hash"])
        request_hashes.add(request.fingerprint)
        assert (
            next(r for r in preview["rows"] if r["concept_id"] == "revenue")["value"]
            == "1000"
        )
    assert len(hashes) == len(request_hashes) == 2


@pytest.mark.parametrize("invalid", (None, 2, True, "3", "", []))
def test_unsupported_versions_are_rejected_in_api_and_source_metadata(invalid):
    with pytest.raises(InvalidUpload):
        choices_from_json(
            json.dumps({**choices().model_dump(), "normalization_version": invalid}),
            UploadChoices,
        )
    parsed, *_ = upload_case()
    with pytest.raises(ValueError):
        replace(parsed.batch.metadata, normalization_version=invalid)


def test_full_bounded_cell_and_reused_normalizer_do_not_leak_version_or_context():
    raw = "-" + "9" * 1999
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert ExactLocaleNumberParser().parse(raw, locale="en-GB").value == Decimal(
            raw
        )
    parsed, *_ = upload_case(
        choices=choices(),
        content=csv_content(changes={"net_income": {"amount": "-" + DIGITS}}),
    )
    normalizer = FinancialNormalizer()
    with localcontext() as context:
        context.prec = 6
        exact = normalizer.normalize(list(parsed.batch.rows), parsed.batch.metadata)
        old = normalizer.normalize(
            list(parsed.batch.rows),
            replace(parsed.batch.metadata, normalization_version="1"),
        )
        again = normalizer.normalize(list(parsed.batch.rows), parsed.batch.metadata)
    income = lambda rows: next(r for r in rows if r.concept_id == "net_income")
    assert income(exact).value == income(again).value == Decimal("-" + DIGITS)
    assert income(old).value == Decimal("-1.23457E+29")
    assert income(old).parser_version == "financial-normalizer-v1"
    assert income(again).parser_version == "financial-normalizer-v2"
