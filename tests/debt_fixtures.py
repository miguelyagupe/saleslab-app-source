"""Synthetic aggregate debt partitions; no real exposures or benchmarks."""

import csv
import io
from uuid import uuid4

from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v7 import FINANCIAL_TERMS_V7
from saleslab_v2.product.contracts import UploadChoices

BASIS = "all contractual outstanding principal excluding leases at reporting date; before hedging; maturities after reporting date"
VALUES = {
    "debt_eligible_principal": "1000",
    "debt_fixed_principal": "600",
    "debt_variable_principal": "400",
    "debt_due_within_12_months": "250",
    "debt_due_after_12_months": "750",
}


def debt_case(
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
    terms = {t.concept_id: t for t in FINANCIAL_TERMS_V7}
    mappings, definitions = {}, {}
    for concept, value in VALUES.items():
        loc = "csv:" + str(len(rows) + 1)
        mappings[loc], definitions[loc] = concept, BASIS
        row = [
            terms[concept].labels[LanguageCode(language)] + " | " + BASIS,
            value,
            "2026",
            "debt_schedule",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ]
        row = (changes or {}).get(concept, row)
        if row is not None:
            rows.append(row)
        else:
            del mappings[loc]
            del definitions[loc]
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    return (
        workspace,
        user,
        out.getvalue().encode(),
        UploadChoices(
            source_language=language,
            format_locale={"pt": "pt-PT", "en": "en-GB", "es": "es-ES", "fr": "fr-FR"}[
                language
            ],
            report_language=report_language,
            planning_version="10",
            normalization_version="2",
            target_period="2026",
            mappings=mappings,
            debt_definitions=definitions if confirmed else None,
        ),
    )


def browser_debt_fixture():
    import base64

    from saleslab_v2.product.debt_source import DebtReviewRequired
    from saleslab_v2.product.ingestion import parse_upload

    w, u, c, o = debt_case()
    try:
        parse_upload(
            w, "statements.csv", c, o.model_copy(update={"debt_definitions": None})
        )
    except DebtReviewRequired as exc:
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
            choices=o.model_copy(update={"mappings": {}, "debt_definitions": None}),
        )[1],
    }
