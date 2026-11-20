"""Two-phase text PDF extraction, confirmation, provenance and bounded failure."""

import io
import json
import subprocess
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from pdf_fixtures import confirmed_pdf, pdf_content, pending_pdf
from product_fixtures import upload_case
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from saleslab_v2.product.contracts import InvalidUpload
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.pdf_contracts import PdfImport, PdfLayoutRequest
from saleslab_v2.product.pdf_source import PdfReviewRequired, _decode, inspect_pdf
from saleslab_v2.product.planning import prepare_upload


def test_source_review_precedes_financial_metrics_and_requires_every_row():
    content, choices, inspection = pending_pdf()
    with pytest.raises(PdfReviewRequired) as raised:
        parse_upload(uuid4(), "statements.pdf", content, choices)
    review = raised.value.payload
    assert review["schema"] == "financial-pdf-review-v1"
    assert review["ready"] is False
    assert "calculated_metrics" not in review
    assert len(review["review_rows"]) == 5
    assert review["review_rows"][0]["extracted_text"].startswith("revenue")
    selected = choices.pdf_import.model_dump()
    selected.update(
        review_hash=review["review_hash"],
        confirmed_rows=[r["location"] for r in review["review_rows"]][:-1],
    )
    incomplete = choices.model_copy(
        update={"pdf_import": PdfImport.model_validate(selected)}
    )
    with pytest.raises(PdfReviewRequired):
        parse_upload(uuid4(), "statements.pdf", content, incomplete)
    assert inspection["pdf"]["engine"].startswith("pypdf:")
    assert inspection["pdf"]["excluded_pages"] == []


def test_confirmed_pdf_preserves_page_lines_and_original_file_in_evidence():
    w = uuid4()
    content, choices, _inspection, review = confirmed_pdf(w)
    parsed, preview, *_ = upload_case(
        w, content=content, filename="statements.pdf", choices=choices
    )
    assert preview["ready"]
    assert parsed.batch.document.source_format.value == "pdf"
    assert (
        parsed.batch.document.parser_version
        == "financial-pdf-text-v1:" + _inspection["pdf"]["engine"]
    )
    assert parsed.original_rows[0]["location"] == "p1:L3:B"
    assert parsed.original_rows[0]["page"] == 1
    assert parsed.original_rows[0]["pdf_line"] == 3
    for role, binding in parsed.original_rows[0]["source_cells"].items():
        if "span" in binding:
            start, end = binding["span"]
            assert (
                parsed.original_rows[0]["extracted_text"][start:end].strip()
                == parsed.original_rows[0][role]
            )
    assert preview["source_import"]["review_hash"] == review["review_hash"]
    assert json.loads(json.dumps(parsed.import_manifest)) == parsed.import_manifest
    margin = next(
        m for m in preview["calculated_metrics"] if m["metric_id"] == "net_margin_pct"
    )
    assert Decimal(margin["exact_value"]) == 10
    assert (
        margin["calculation_evidence"]["inputs"][0]["sources"][0]["location"]
        == "p1:L4:B"
    )


@pytest.mark.parametrize(
    "language,locale,label,income,amount",
    (
        ("pt", "pt-PT", "receita", "resultado líquido", "1.234,50"),
        ("en", "en-GB", "revenue", "net income", "1,234.50"),
        ("es", "es-ES", "ingresos", "resultado neto", "1.234,50"),
        ("fr", "fr-FR", "chiffre d'affaires", "résultat net", "1 234,50"),
    ),
)
@pytest.mark.parametrize("report", ("pt", "en", "es", "fr"))
def test_pdf_source_language_locale_and_report_language_remain_independent(
    language, locale, label, income, amount, report
):
    w = uuid4()
    content = pdf_content([[(label, amount), (income, "100")]])
    layouts = PdfLayoutRequest(pages=[{"page": 1, "cuts": [40]}])
    content, choices, _, _ = confirmed_pdf(
        w,
        content,
        layouts=layouts,
        source_language=language,
        format_locale=locale,
        report_language=report,
    )
    choices = choices.model_copy(update={"mappings": {"p1:L3:B": "revenue"}})
    _, preview, *_ = upload_case(
        w, content=content, filename="statements.pdf", choices=choices
    )
    assert preview["ready"]
    revenue = next(r for r in preview["rows"] if r["concept_id"] == "revenue")
    assert Decimal(revenue["value"]) == Decimal("1234.50")


@pytest.mark.parametrize(
    "change",
    (
        {"format_locale": "pt-PT"},
        {"source_currency": "EUR"},
        {"accounting_parentheses": True},
        {"source_scale": "raw"},
    ),
)
def test_source_interpretation_changes_require_fresh_row_review(change):
    w = uuid4()
    content, choices, _, review = confirmed_pdf(w)
    changed = choices.model_copy(update=change)
    with pytest.raises(PdfReviewRequired) as raised:
        parse_upload(w, "statements.pdf", content, changed)
    assert raised.value.payload["review_hash"] != review["review_hash"]


def test_column_boundaries_source_pages_and_row_values_are_bound_to_approval():
    w = uuid4()
    content, choices, _, review = confirmed_pdf(w)
    selected = choices.pdf_import.model_dump()
    selected["pages"][0]["cuts"] = [39]
    with pytest.raises(InvalidUpload, match="inspect again"):
        parse_upload(
            w,
            "statements.pdf",
            content,
            choices.model_copy(
                update={"pdf_import": PdfImport.model_validate(selected)}
            ),
        )
    selected = choices.pdf_import.model_dump()
    selected["tables"][0]["fields"]["period"]["constant"] = "2025"
    with pytest.raises(PdfReviewRequired) as raised:
        parse_upload(
            w,
            "statements.pdf",
            content,
            choices.model_copy(
                update={"pdf_import": PdfImport.model_validate(selected)}
            ),
        )
    assert raised.value.payload["review_hash"] != review["review_hash"]
    altered = pdf_content(
        [
            [("revenue", "999"), ("net income", "100")],
            [
                ("total assets", "1500"),
                ("total liabilities", "1000"),
                ("equity", "500"),
            ],
        ]
    )
    with pytest.raises(InvalidUpload, match="inspect again"):
        parse_upload(w, "statements.pdf", altered, choices)


@pytest.mark.parametrize(
    "pages",
    (
        [],
        [{"page": 0}],
        [{"page": 301}],
        [{"page": 1}, {"page": 1}],
        [{"page": 2}, {"page": 1}],
        [{"page": True}],
        [{"page": 1, "cuts": [20, 10]}],
        [{"page": 1, "cuts": [1, 1]}],
        [{"page": 1, "cuts": [True]}],
        [{"page": 1, "cuts": [2001]}],
    ),
)
def test_pdf_page_and_character_layout_contracts_are_strict(pages):
    with pytest.raises(ValueError):
        PdfLayoutRequest(pages=pages)


def test_encrypted_image_only_rotated_and_invalid_pdf_sources_are_rejected():
    source = pdf_content()
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(source)))
    writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(InvalidUpload, match="Encrypted"):
        inspect_pdf(
            "statements.pdf", out.getvalue(), PdfLayoutRequest(pages=[{"page": 1}])
        )
    out = io.BytesIO()
    c = canvas.Canvas(out)
    c.showPage()
    c.save()
    with pytest.raises(InvalidUpload, match="no extractable text"):
        inspect_pdf(
            "statements.pdf", out.getvalue(), PdfLayoutRequest(pages=[{"page": 1}])
        )
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(source)))
    writer.pages[0].rotate(90)
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(InvalidUpload, match="unrotated"):
        inspect_pdf(
            "statements.pdf", out.getvalue(), PdfLayoutRequest(pages=[{"page": 1}])
        )
    with pytest.raises(InvalidUpload):
        inspect_pdf(
            "statements.pdf", b"%PDF-invalid", PdfLayoutRequest(pages=[{"page": 1}])
        )


def test_rotated_text_is_rejected_even_when_page_rotation_is_zero():
    out = io.BytesIO()
    c = canvas.Canvas(out)
    c.rotate(30)
    c.drawString(50, 100, "revenue 1000")
    c.save()
    with pytest.raises(InvalidUpload, match="orientation"):
        inspect_pdf(
            "statements.pdf", out.getvalue(), PdfLayoutRequest(pages=[{"page": 1}])
        )


def test_unselected_pages_are_excluded_explicitly_and_not_required_to_have_text():
    content = pdf_content()
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(content)))
    writer.add_blank_page(width=500, height=800)
    out = io.BytesIO()
    writer.write(out)
    description, _ = inspect_pdf(
        "statements.pdf", out.getvalue(), PdfLayoutRequest(pages=[{"page": 1}])
    )
    assert description["pdf"]["page_count"] == 3
    assert description["pdf"]["excluded_pages"] == [2, 3]
    with pytest.raises(InvalidUpload, match="existing pages"):
        inspect_pdf("statements.pdf", content, PdfLayoutRequest(pages=[{"page": 3}]))


def test_decoder_timeout_and_process_failures_are_sanitized_and_credentials_not_forwarded():
    layout = PdfLayoutRequest(pages=[{"page": 1}])
    with patch(
        "saleslab_v2.product.pdf_source.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["decoder"], 12),
    ), pytest.raises(InvalidUpload, match="limits"):
        _decode(pdf_content(), layout)
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "never-forward"}), patch(
        "saleslab_v2.product.pdf_source.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, b"", b""),
    ) as run, pytest.raises(InvalidUpload):
        _decode(pdf_content(), layout)
    assert "ANTHROPIC_API_KEY" not in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["timeout"] == 12
    assert isinstance(run.call_args.args[0], list)


def test_pdf_import_does_not_modify_legacy_hash_or_allow_two_preparation_contracts():
    from tabular_fixtures import import_choices

    legacy = import_choices()
    _content, choices, _ = pending_pdf()
    with pytest.raises(ValueError):
        type(choices).model_validate(
            {
                **choices.model_dump(),
                "tabular_import": legacy.tabular_import.model_dump(),
            }
        )
    assert "pdf_import" not in legacy.contract_payload()


def test_confirmed_pdf_reaches_independent_audit_and_complete_html_pdf_publication():
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    w = uuid4()
    content, choices, _, _ = confirmed_pdf(w)
    parsed, _, _, _, user = upload_case(
        w, content=content, filename="statements.pdf", choices=choices
    )
    request, _ = prepare_upload(parsed, choices, user, "preview")
    with patch("report_fixtures.request", return_value=request):
        report, (_, _, _, _, _, audit, checks) = report_fixture(growth=False)
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert not any(c["status"] == "block" for c in checks)
    assert report["source_index"][0]["parser_version"].startswith(
        "financial-pdf-text-v1:pypdf:"
    )
    report["render_version"] = "financial-render-v4"
    assert render_channels(report)["parity"]["status"] == "pass"


def test_pdf_requires_exact_normalization_and_retains_long_signed_source_values():
    w = uuid4()
    amount = "-123456789012345678901234567890123456789"
    content = pdf_content([[("revenue", amount), ("net income", "100")]])
    content, choices, _, _ = confirmed_pdf(
        w, content, layouts=PdfLayoutRequest(pages=[{"page": 1, "cuts": [40]}])
    )
    _parsed, preview, *_ = upload_case(
        w, content=content, filename="statements.pdf", choices=choices
    )
    revenue = next(row for row in preview["rows"] if row["concept_id"] == "revenue")
    assert Decimal(revenue["value"]) == Decimal(amount)
    with pytest.raises(InvalidUpload, match="normalization version 2"):
        parse_upload(
            w,
            "statements.pdf",
            content,
            choices.model_copy(update={"normalization_version": "1"}),
        )


def test_existing_prepared_csv_request_and_preview_hashes_remain_pinned():
    from uuid import UUID

    from product_fixtures import csv_content
    from tabular_fixtures import import_choices

    from saleslab_v2.product.ingestion import HEADERS

    content = csv_content()
    fields = {role: {"column": chr(65 + index)} for index, role in enumerate(HEADERS)}
    choices = import_choices(
        content,
        filename="mapped.csv",
        delimiter=";",
        tables=[
            {
                "sheet": 1,
                "header_row": 1,
                "first_row": 2,
                "last_row": 6,
                "fields": fields,
            }
        ],
    )
    w = UUID("10000000-0000-4000-8000-000000000001")
    u = UUID("10000000-0000-4000-8000-000000000002")
    parsed, preview, *_ = upload_case(
        w, u, content=content, filename="mapped.csv", choices=choices
    )
    request, _ = prepare_upload(parsed, choices, u, "preview")
    assert (
        preview["preview_hash"]
        == "0342cc64372e01081a6fe0e271cb084b96a04988397f8372a2da14abcb0be113"
    )
    assert (
        request.fingerprint
        == "5843d0ceb496b928cc89439f7b583595abb8a83d0f404d14ebb2545cc75f40c0"
    )


def test_concurrent_pdf_preparation_is_bounded_without_spawning_more_decoders():
    from saleslab_v2.product.pdf_source import _DECODERS, PdfDecoderBusy

    with _DECODERS, patch(
        "saleslab_v2.product.pdf_source.subprocess.run"
    ) as run, pytest.raises(PdfDecoderBusy, match="busy"):
        _decode(pdf_content(), PdfLayoutRequest(pages=[{"page": 1}]))
    run.assert_not_called()
