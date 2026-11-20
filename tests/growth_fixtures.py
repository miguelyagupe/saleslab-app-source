"""Synthetic JM-FIN-005 proportions, never a business benchmark or real disclosure."""

import csv
import io
from uuid import uuid4

from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v6 import FINANCIAL_TERMS_V6
from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.growth_source import BASES, CONTRIBUTIONS

CC = "same reported group perimeter; exchange rates fixed at 2025"
LFL = "same sites active in both 2025 and 2026; excludes all new sites"
BRIDGE = "group revenue bridge 2025 to 2026; signed contributions in EUR"


def growth_case(
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
        csv.reader(
            io.StringIO(
                csv_content(
                    language=language, changes={"revenue": {"amount": "1093"}}
                ).decode()
            ),
            delimiter=";",
        )
    )
    terms = {t.concept_id: t for t in FINANCIAL_TERMS_V6}
    rows.append(
        [
            terms["revenue"].labels[LanguageCode(language)],
            "1000",
            "2025",
            "income_statement",
            "consolidated",
            "EUR",
            "raw",
            "comparative",
        ]
    )
    mappings, definitions = {}, {}
    values = [
        (BASES[0], "1049", "2026", "current", CC),
        (BASES[0], "1000", "2025", "comparative", CC),
        (BASES[1], "1006", "2026", "current", LFL),
        (BASES[1], "1000", "2025", "comparative", LFL),
        *[
            (key, value, "2026", "current", BRIDGE)
            for key, value in zip(CONTRIBUTIONS, ("6", "43", "44", "0"))
        ],
    ]
    for concept, amount, period, basis, quote in values:
        location = "csv:" + str(len(rows) + 1)
        mappings[location] = concept
        definitions[location] = quote
        row = [
            terms[concept].labels[LanguageCode(language)] + " | " + quote,
            amount,
            period,
            "income_statement",
            "consolidated",
            "EUR",
            "raw",
            basis,
        ]
        row = (changes or {}).get(location, row)
        rows.append(row)
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    content = out.getvalue().encode()
    choices = UploadChoices(
        source_language=language,
        format_locale={"en": "en-GB", "pt": "pt-PT", "es": "es-ES", "fr": "fr-FR"}[
            language
        ],
        report_language=report_language,
        planning_version="9",
        normalization_version="2",
        target_period="2026",
        mappings=mappings,
        growth_definitions=definitions if confirmed else None,
    )
    return workspace, user, content, choices


def _growth_browser_view(w, u, c, o):
    from saleslab_v2.product.growth_source import GrowthReviewRequired
    from saleslab_v2.product.ingestion import parse_upload

    try:
        parse_upload(
            w, "statements.csv", c, o.model_copy(update={"growth_definitions": None})
        )
    except GrowthReviewRequired as exc:
        review = exc.payload
    return {
        "choices": o.model_dump(),
        "review": review,
        "preview": upload_case(w, u, content=c, choices=o)[1],
        "unmapped_preview": upload_case(
            w,
            u,
            content=c,
            choices=o.model_copy(update={"mappings": {}, "growth_definitions": None}),
        )[1],
    }


def browser_growth_fixture():
    import base64

    from saleslab_v2.product.ingestion import HEADERS, parse_upload
    from saleslab_v2.product.tabular import inspect_upload
    from saleslab_v2.product.tabular_contracts import TabularImport

    w, u, c, o = growth_case()
    inspection = inspect_upload("statements.csv", c, ";")
    selection = {
        "version": "1",
        "inspection_hash": inspection["inspection_hash"],
        "delimiter": ";",
        "tables": [
            {
                "sheet": 1,
                "header_row": 1,
                "first_row": 2,
                "last_row": inspection["sheets"][0]["row_count"],
                "fields": {
                    role: {"column": chr(65 + i)} for i, role in enumerate(HEADERS)
                },
            }
        ],
        "excluded_content_confirmed": True,
    }
    blank = o.model_copy(
        update={
            "mappings": {},
            "growth_definitions": None,
            "tabular_import": TabularImport.model_validate(selection),
        }
    )
    parsed = parse_upload(w, "statements.csv", c, blank)
    mappings, definitions = {}, {}
    for number, row in enumerate(parsed.original_rows, 2):
        old = "csv:" + str(number)
        if old in o.mappings:
            mappings[row["location"]] = o.mappings[old]
            definitions[row["location"]] = o.growth_definitions[old]
    prepared = blank.model_copy(
        update={"mappings": mappings, "growth_definitions": definitions}
    )
    return {
        "content": base64.b64encode(c).decode(),
        **_growth_browser_view(w, u, c, o),
        "inspection": inspection,
        "selection": selection,
        "prepared": _growth_browser_view(w, u, c, prepared),
    }


def complete_growth_case():
    from capital_fixtures import capital_content

    w, u, c, o = growth_case()
    growth = list(csv.reader(io.StringIO(c.decode()), delimiter=";"))
    rows = list(csv.reader(io.StringIO(capital_content().decode()), delimiter=";"))
    growth[6][1] = "907"  # Existing reported revenue 1000 less the explicit bridge 93.
    mappings, definitions = {}, {}
    for number, row in enumerate(growth[6:], 7):
        old, location = "csv:" + str(number), "csv:" + str(len(rows) + 1)
        if old in o.mappings:
            mappings[location] = o.mappings[old]
            definitions[location] = o.growth_definitions[old]
        rows.append(row)
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    return (
        w,
        u,
        out.getvalue().encode(),
        o.model_copy(update={"mappings": mappings, "growth_definitions": definitions}),
    )
