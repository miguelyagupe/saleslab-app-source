"""Explicit operating balance perimeter and independently reported balance change."""

import csv
import io
from uuid import uuid4

from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v10 import FINANCIAL_TERMS_V10
from saleslab_v2.product.contracts import UploadChoices

BASIS = "operating current assets exclude cash and non-operating assets; operating current liabilities exclude debt and leases; the same perimeter applies to opening and closing balances; reported change is closing minus opening, not a cash flow"
VALUES = (
    ("opening_assets", "current_operating_assets", "250", "2025"),
    ("opening_liabilities", "current_operating_liabilities", "150", "2025"),
    ("opening_nwc", "reported_operating_nwc", "100", "2025"),
    ("closing_assets", "current_operating_assets", "300", "2026"),
    ("closing_liabilities", "current_operating_liabilities", "180", "2026"),
    ("closing_nwc", "reported_operating_nwc", "120", "2026"),
    ("change", "reported_operating_nwc_change", "20", "2026"),
)


def working_capital_case(
    workspace=None,
    user=None,
    *,
    language="en",
    report_language="pt",
    confirmed=True,
    changes=None,
    missing=()
):
    rows = list(
        csv.reader(io.StringIO(csv_content(language=language).decode()), delimiter=";")
    )
    mappings = {}
    definitions = {}
    locations = {}
    terms = {t.concept_id: t for t in FINANCIAL_TERMS_V10}
    for key, concept, amount, period in VALUES:
        if key in missing:
            continue
        term = terms[concept]
        row = [
            term.labels[LanguageCode(language)] + " | " + BASIS,
            amount,
            period,
            term.statement_type,
            "consolidated",
            "EUR",
            "raw",
            "current",
        ]
        for field, value in (changes or {}).get(key, {}).items():
            row[field] = value
        loc = "csv:" + str(len(rows) + 1)
        rows.append(row)
        mappings[loc] = concept
        definitions[loc] = BASIS
        locations[key] = loc
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    choices = UploadChoices(
        source_language=language,
        format_locale={"pt": "pt-PT", "en": "en-GB", "es": "es-ES", "fr": "fr-FR"}[
            language
        ],
        report_language=report_language,
        planning_version="13",
        normalization_version="2",
        target_period="2026",
        mappings=mappings,
        working_capital_definitions=definitions if confirmed else None,
    )
    return (
        workspace or uuid4(),
        user or uuid4(),
        out.getvalue().encode(),
        choices,
        locations,
    )


def browser_working_capital_fixture():
    import base64

    from saleslab_v2.product.ingestion import parse_upload
    from saleslab_v2.product.working_capital_source import WorkingCapitalReviewRequired

    w, u, c, o, locs = working_capital_case()
    try:
        parse_upload(
            w,
            "statements.csv",
            c,
            o.model_copy(update={"working_capital_definitions": None}),
        )
    except WorkingCapitalReviewRequired as exc:
        review = exc.payload
    return {
        "content": base64.b64encode(c).decode(),
        "choices": o.model_dump(),
        "review": review,
        "locations": locs,
        "preview": upload_case(w, u, content=c, choices=o)[1],
        "partial_preview": upload_case(
            w,
            u,
            content=c,
            choices=o.model_copy(
                update={
                    "mappings": {locs["closing_nwc"]: "reported_operating_nwc"},
                    "working_capital_definitions": None,
                }
            ),
        )[1],
        "unmapped_preview": upload_case(
            w,
            u,
            content=c,
            choices=o.model_copy(
                update={"mappings": {}, "working_capital_definitions": None}
            ),
        )[1],
    }
