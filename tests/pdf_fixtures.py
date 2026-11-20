"""Digital text PDF fixtures with explicit source review; no OCR or model calls."""

import io

from reportlab.pdfgen import canvas

from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.pdf_contracts import PdfImport, PdfLayoutRequest
from saleslab_v2.product.pdf_source import PdfReviewRequired, inspect_pdf


def pdf_content(pages=None):
    pages = pages or [
        [("revenue", "1000"), ("net income", "100")],
        [("total assets", "1500"), ("total liabilities", "1000"), ("equity", "500")],
    ]
    out = io.BytesIO()
    pdf = canvas.Canvas(out, invariant=1, pageCompression=0)
    for rows in pages:
        pdf.setFont("Courier", 10)
        y = 780
        pdf.drawString(50, y, "Financial statement")
        y -= 20
        pdf.drawString(50, y, "Account".ljust(40) + "Amount")
        for label, amount in rows:
            y -= 20
            pdf.drawString(50, y, label.ljust(40) + amount)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def pending_pdf(content=None, layouts=None, **kwargs):
    content = pdf_content() if content is None else content
    layouts = layouts or PdfLayoutRequest(
        pages=[{"page": 1, "cuts": [40]}, {"page": 2, "cuts": [40]}]
    )
    inspection, _ = inspect_pdf("statements.pdf", content, layouts)
    tables = []
    for sheet in inspection["sheets"]:
        statement = "income_statement" if sheet["page"] == 1 else "balance_sheet"
        tables.append(
            {
                "sheet": sheet["sheet"],
                "header_row": 2,
                "first_row": 3,
                "last_row": sheet["row_count"],
                "fields": {
                    "line": {"column": "A"},
                    "amount": {"column": "B"},
                    **{
                        k: {"constant": v}
                        for k, v in {
                            "period": "2026",
                            "statement_type": statement,
                            "scope": "consolidated",
                            "currency": "EUR",
                            "scale": "raw",
                            "comparison_basis": "current",
                        }.items()
                    },
                },
            }
        )
    options = {
        "source_language": "en",
        "format_locale": "en-GB",
        "report_language": "pt",
    }
    options.update(kwargs)
    return (
        content,
        UploadChoices(
            **options,
            normalization_version="2",
            planning_version="8",
            target_period="2026",
            pdf_import={
                "inspection_hash": inspection["inspection_hash"],
                "pages": layouts.model_dump()["pages"],
                "tables": tables,
                "excluded_content_confirmed": True,
            }
        ),
        inspection,
    )


def confirmed_pdf(workspace_id, content=None, **kwargs):
    content, choices, inspection = pending_pdf(content, **kwargs)
    try:
        parse_upload(workspace_id, "statements.pdf", content, choices)
    except PdfReviewRequired as exc:
        review = exc.payload
    else:
        raise AssertionError("PDF source review must precede calculation")
    selected = choices.pdf_import.model_dump()
    selected.update(
        review_hash=review["review_hash"],
        confirmed_rows=[r["location"] for r in review["review_rows"]],
    )
    return (
        content,
        choices.model_copy(update={"pdf_import": PdfImport.model_validate(selected)}),
        inspection,
        review,
    )


def browser_pdf_fixture():
    import base64
    from uuid import uuid4

    from product_fixtures import upload_case

    workspace = uuid4()
    content, choices, inspection, review = confirmed_pdf(workspace)
    raw, _ = inspect_pdf(
        "statements.pdf", content, PdfLayoutRequest(pages=[{"page": 1}, {"page": 2}])
    )
    return {
        "content": base64.b64encode(content).decode(),
        "inspection": inspection,
        "raw_inspection": raw,
        "selection": choices.pdf_import.model_dump(),
        "review": review,
        "preview": upload_case(
            workspace, content=content, filename="statements.pdf", choices=choices
        )[1],
    }
