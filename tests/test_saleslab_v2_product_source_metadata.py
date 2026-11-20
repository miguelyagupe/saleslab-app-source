"""Explicit source confirmations never rewrite cells or override conflicts."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from product_fixtures import csv_content, upload_case

from saleslab_v2.normalization.currencies import ISO_4217_CODES
from saleslab_v2.product.api import choices_from_json
from saleslab_v2.product.contracts import InvalidUpload, UploadChoices
from saleslab_v2.product.planning import prepare_upload

CONCEPTS = ("revenue", "net_income", "total_assets", "total_liabilities", "equity")


def choices(**updates):
    return UploadChoices(
        **{
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            **updates,
        }
    )


def missing_content(**updates):
    return csv_content(
        changes={c: {"currency": "", "scale": "", **updates} for c in CONCEPTS}
    )


@pytest.mark.parametrize("locale", ("pt-PT", "en-GB", "es-ES", "fr-FR"))
@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize(
    "scale,factor", (("raw", 1), ("thousands", 1000), ("millions", 1000000))
)
def test_confirmations_fill_missing_metadata_exactly_without_rewriting_rows(
    locale, language, scale, factor
):
    content = missing_content()
    parsed, preview, _, saved_content, _ = upload_case(
        content=content,
        choices=choices(
            format_locale=locale,
            report_language=language,
            source_currency="EUR",
            source_scale=scale,
        ),
    )
    assert saved_content == content
    assert parsed.batch.metadata.currency == "EUR"
    assert parsed.batch.metadata.scale == scale
    assert all(r.currency is None and r.scale is None for r in parsed.batch.rows)
    assert all(r["currency"] == r["scale"] == "" for r in parsed.original_rows)
    assert preview["ready"]
    revenue = next(r for r in preview["rows"] if r["concept_id"] == "revenue")
    assert (
        revenue["original_input"]["currency"]
        == revenue["original_input"]["scale"]
        == ""
    )
    assert revenue["currency"] == "EUR"
    assert Decimal(revenue["value"]) == 1000 * factor
    margin = next(
        m for m in preview["calculated_metrics"] if m["metric_id"] == "net_margin_pct"
    )
    assert Decimal(margin["exact_value"]) == 10


@pytest.mark.parametrize(
    "overrides,code",
    (
        ({}, "currency.missing"),
        ({"source_currency": "EUR"}, "scale.missing"),
        ({"source_scale": "raw"}, "currency.missing"),
    ),
)
def test_unconfirmed_fields_remain_blocked(overrides, code):
    _, preview, _, _, _ = upload_case(
        content=missing_content(), choices=choices(**overrides)
    )
    assert not preview["ready"]
    assert all(code in [i["code"] for i in r["issues"]] for r in preview["rows"])


@pytest.mark.parametrize(
    "changes,source,code",
    (
        ({"currency": "USD"}, {"source_currency": "EUR"}, "currency.conflict"),
        ({"amount": "USD 100"}, {"source_currency": "EUR"}, "currency.conflict"),
        ({"scale": "millions"}, {"source_scale": "raw"}, "scale.conflict"),
        ({"amount": "100 million"}, {"source_scale": "raw"}, "scale.conflict"),
    ),
)
def test_source_confirmation_cannot_override_explicit_row_or_text_conflicts(
    changes, source, code
):
    content = csv_content(changes={"net_income": changes})
    parsed, preview, _, _, _ = upload_case(content=content, choices=choices(**source))
    row = next(r for r in preview["rows"] if r["concept_id"] == "net_income")
    assert code in [i["code"] for i in row["issues"]]
    assert row["normalization_status"] == "needs_confirmation"
    assert not preview["ready"]
    assert row["original_input"] == next(
        r for r in parsed.original_rows if r["location"] == "csv:3"
    )


def test_confirmed_currency_disambiguates_dollar_only_with_compatible_currency():
    content = missing_content()
    content = content.replace(b"revenue;1000;", b"revenue;$1000;")
    _, preview, _, _, _ = upload_case(
        content=content, choices=choices(source_currency="CAD", source_scale="raw")
    )
    assert preview["ready"]
    assert preview["rows"][0]["currency"] == "CAD"
    _, invalid, _, _, _ = upload_case(
        content=content, choices=choices(source_currency="EUR", source_scale="raw")
    )
    assert not invalid["ready"]
    assert "currency.ambiguous_symbol" in [
        i["code"] for i in invalid["rows"][0]["issues"]
    ]


@pytest.mark.parametrize(
    "field,invalid",
    (
        ("source_currency", ""),
        ("source_currency", "eur"),
        ("source_currency", " EUR"),
        ("source_currency", "XXX"),
        ("source_currency", "XTS"),
        ("source_currency", "ZZZ"),
        ("source_currency", True),
        ("source_currency", 123),
        ("source_currency", []),
        ("source_scale", ""),
        ("source_scale", "Thousands"),
        ("source_scale", 1000),
        ("source_scale", True),
        ("source_scale", {}),
    ),
)
def test_source_choices_use_strict_existing_currency_and_scale_policy(field, invalid):
    with pytest.raises(InvalidUpload):
        choices_from_json(
            json.dumps({**choices().model_dump(), field: invalid}), UploadChoices
        )


def test_all_advertised_currencies_are_valid_choices_and_nulls_preserve_legacy_contract():
    for currency in ISO_4217_CODES:
        assert choices(source_currency=currency).source_currency == currency
    old = choices().contract_payload()
    assert "source_currency" not in old and "source_scale" not in old
    assert old == choices(source_currency=None, source_scale=None).contract_payload()


def test_changed_confirmations_are_bound_even_when_source_cells_already_agree():
    workspace, user = uuid4(), uuid4()
    hashes, request_hashes, source_hashes = set(), set(), set()
    for updates in (
        {},
        {"source_currency": "EUR"},
        {"source_scale": "raw"},
        {"source_currency": "EUR", "source_scale": "raw"},
    ):
        opts = choices(**updates)
        parsed, preview, _, _, _ = upload_case(workspace, user, choices=opts)
        request, _ = prepare_upload(parsed, opts, user, "preview")
        assert preview["ready"]
        hashes.add(preview["preview_hash"])
        request_hashes.add(request.fingerprint)
        source_hashes.add(preview["source_hash"])
    assert len(hashes) == len(request_hashes) == 4
    assert len(source_hashes) == 1


def test_xlsx_blank_metadata_remains_blank_in_original_evidence():
    import csv
    import io

    from openpyxl import Workbook

    book = Workbook()
    for row in csv.reader(io.StringIO(missing_content().decode()), delimiter=";"):
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    _, preview, _, _, _ = upload_case(
        filename="source.xlsx",
        content=stream.getvalue(),
        choices=choices(source_currency="GBP", source_scale="thousands"),
    )
    assert preview["ready"]
    row = preview["rows"][0]
    assert row["original_input"]["currency"] == row["original_input"]["scale"] == ""
    assert row["value"] == "1000000"
    assert row["currency"] == "GBP"
    assert row["location"] == "sheet1:2"
