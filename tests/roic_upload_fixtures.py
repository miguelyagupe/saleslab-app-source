"""Synthetic reported operands with independently cited ROIC source definitions."""

import csv
import io

from product_fixtures import csv_content, upload_case

from saleslab_v2.i18n.financial_v9 import ROIC_LABELS
from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.roic_contracts import RoicSourceChoices

NOPAT = "reported operating profit after operating taxes; no financing or exceptional tax items"
TAX = "operating taxes use the explicit 20 percent source assumption; not the effective group tax rate"
AVERAGE = "reported time-weighted average operating invested capital over the same reporting period; excludes non-operating assets"


def roic_case(
    workspace=None,
    user=None,
    *,
    language="en",
    report_language="pt",
    confirmed=True,
    changes=None
):
    from uuid import uuid4

    rows = list(
        csv.reader(io.StringIO(csv_content(language=language).decode()), delimiter=";")
    )
    mappings = {}
    for concept, amount, definition in [
        ("nopat_with_explicit_tax_assumptions", "80", NOPAT + " | " + TAX),
        ("average_invested_capital", "400", AVERAGE),
    ]:
        row = [
            ROIC_LABELS[concept][("pt", "en", "es", "fr").index(language)]
            + " | "
            + definition,
            amount,
            "2026",
            "roic_schedule",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ]
        for field, value in (changes or {}).get(concept, {}).items():
            row[field] = value
        mappings["csv:" + str(len(rows) + 1)] = concept
        rows.append(row)
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    choices = UploadChoices(
        source_language=language,
        format_locale={"pt": "pt-PT", "en": "en-GB", "es": "es-ES", "fr": "fr-FR"}[
            language
        ],
        report_language=report_language,
        planning_version="12",
        normalization_version="2",
        target_period="2026",
        mappings=mappings,
        roic_sources=(
            RoicSourceChoices(
                nopat_location="csv:7",
                nopat_definition=NOPAT,
                tax_assumptions_location="csv:7",
                tax_assumptions=TAX,
                average_capital_location="csv:8",
                average_capital_definition=AVERAGE,
            )
            if confirmed
            else None
        ),
    )
    return workspace or uuid4(), user or uuid4(), out.getvalue().encode(), choices


def browser_roic_fixture():
    import base64

    from saleslab_v2.product.ingestion import parse_upload
    from saleslab_v2.product.roic_source import RoicReviewRequired

    w, u, c, o = roic_case()
    try:
        parse_upload(
            w, "statements.csv", c, o.model_copy(update={"roic_sources": None})
        )
    except RoicReviewRequired as exc:
        review = exc.payload
    return {
        "content": base64.b64encode(c).decode(),
        "choices": o.model_dump(),
        "review": review,
        "preview": upload_case(w, u, content=c, choices=o)[1],
        "partial_preview": upload_case(
            w,
            u,
            content=c,
            choices=o.model_copy(
                update={
                    "mappings": {"csv:7": "nopat_with_explicit_tax_assumptions"},
                    "roic_sources": None,
                }
            ),
        )[1],
        "unmapped_preview": upload_case(
            w,
            u,
            content=c,
            choices=o.model_copy(update={"mappings": {}, "roic_sources": None}),
        )[1],
    }
