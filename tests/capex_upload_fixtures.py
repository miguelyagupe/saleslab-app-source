"""Synthetic source-confirmed cash capex; commitments are not payments."""

import csv
import io
from uuid import uuid4

from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v8 import CASH_CAPEX_CONCEPTS, FINANCIAL_TERMS_V8
from saleslab_v2.product.contracts import UploadChoices

PAID_BASIS = "all cash paid for the same fixed asset purchases in the period; maintenance and growth exhaust this classification; excludes commitments and noncash additions"
COMMIT_BASIS = "outstanding fixed asset purchase commitments at the reporting date; excludes cash already paid; no payment dates or funding assumptions"
VALUES = {
    "capex": "-80",
    "capex_cash_total": "80",
    "capex_cash_maintenance": "30",
    "capex_cash_growth": "50",
    "capex_commitments_outstanding": "120",
}


def capex_case(
    workspace=None,
    user=None,
    *,
    language="en",
    report_language="pt",
    confirmed=True,
    changes=None
):
    workspace, user = workspace or uuid4(), user or uuid4()
    rows = list(
        csv.reader(io.StringIO(csv_content(language=language).decode()), delimiter=";")
    )
    terms = {t.concept_id: t for t in FINANCIAL_TERMS_V8}
    mappings = {}
    definitions = {}
    for concept, value in VALUES.items():
        quote = (
            COMMIT_BASIS if concept == "capex_commitments_outstanding" else PAID_BASIS
        )
        row = [
            terms[concept].labels[LanguageCode(language)] + " | " + quote,
            value,
            "2026",
            "cash_flow_statement" if concept == "capex" else "capex_schedule",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ]
        row = (changes or {}).get(concept, row)
        if row is None:
            continue
        loc = "csv:" + str(len(rows) + 1)
        mappings[loc] = concept
        definitions[loc] = quote
        rows.append(row)
    if not any(concept in CASH_CAPEX_CONCEPTS for concept in mappings.values()):
        definitions = {
            loc: quote for loc, quote in definitions.items() if mappings[loc] != "capex"
        }
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    choices = UploadChoices(
        source_language=language,
        format_locale={"pt": "pt-PT", "en": "en-GB", "es": "es-ES", "fr": "fr-FR"}[
            language
        ],
        report_language=report_language,
        planning_version="11",
        normalization_version="2",
        target_period="2026",
        mappings=mappings,
        capex_definitions=definitions if confirmed else None,
    )
    return workspace, user, out.getvalue().encode(), choices


def browser_capex_fixture():
    import base64

    from saleslab_v2.product.capex_source import CapexReviewRequired
    from saleslab_v2.product.ingestion import parse_upload

    w, u, c, o = capex_case()
    try:
        parse_upload(
            w, "statements.csv", c, o.model_copy(update={"capex_definitions": None})
        )
    except CapexReviewRequired as exc:
        review = exc.payload
    return {
        "content": base64.b64encode(c).decode(),
        "choices": o.model_dump(),
        "review": review,
        "preview": upload_case(w, u, content=c, choices=o)[1],
        "unmapped_preview": upload_case(
            w,
            u,
            content=c,
            choices=o.model_copy(update={"mappings": {}, "capex_definitions": None}),
        )[1],
    }


def complete_capex_case():
    from debt_fixtures import debt_case
    from growth_fixtures import complete_growth_case

    w, u, c, o = complete_growth_case()
    _, _, d, dc = debt_case(w, u)
    _, _, cx, cc = capex_case(w, u)
    rows = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    mappings = dict(o.mappings)
    debt_definitions = {}
    capex_definitions = {}
    for content, choices, definitions, field in (
        (d, dc, debt_definitions, "debt_definitions"),
        (cx, cc, capex_definitions, "capex_definitions"),
    ):
        additional = list(csv.reader(io.StringIO(content.decode()), delimiter=";"))
        for old, concept in choices.mappings.items():
            loc = "csv:" + str(len(rows) + 1)
            mappings[loc] = concept
            definitions[loc] = getattr(choices, field)[old]
            rows.append(additional[int(old.split(":")[1]) - 1])
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    return (
        w,
        u,
        out.getvalue().encode(),
        o.model_copy(
            update={
                "planning_version": "11",
                "mappings": mappings,
                "debt_definitions": debt_definitions,
                "capex_definitions": capex_definitions,
            }
        ),
    )
