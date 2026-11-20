"""Explicit tabular uploads with recorded models; never uses paid provider calls."""

import csv
import io
import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql://unused/product_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-with-at-least-32-characters")

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.catalog import FINANCIAL_TERMS
from saleslab_v2.product.contracts import GenerateChoices, UploadChoices
from saleslab_v2.product.ingestion import HEADERS, parse_upload
from saleslab_v2.product.planning import prepare_upload


def csv_content(*, language="en", changes=None, extra=()):
    terms = {t.concept_id: t for t in FINANCIAL_TERMS}
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(HEADERS)
    for concept, value in (
        ("revenue", "1000"),
        ("net_income", "100"),
        ("total_assets", "100"),
        ("total_liabilities", "60"),
        ("equity", "40"),
    ):
        term = terms[concept]
        row = dict(
            zip(
                HEADERS,
                (
                    term.labels[LanguageCode(language)],
                    value,
                    "2026",
                    term.statement_type,
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                ),
            )
        )
        row.update((changes or {}).get(concept, {}))
        writer.writerow([row[h] for h in HEADERS])
    writer.writerows(extra)
    return buffer.getvalue().encode("utf-8")


def upload_case(
    workspace_id=None,
    user_id=None,
    *,
    content=None,
    choices=None,
    key=None,
    filename="statements.csv"
):
    workspace_id, user_id = workspace_id or uuid4(), user_id or uuid4()
    content = content if content is not None else csv_content()
    choices = choices or UploadChoices(
        source_language="en", format_locale="en-GB", report_language="pt"
    )
    parsed = parse_upload(workspace_id, filename, content, choices)
    _, preview = prepare_upload(parsed, choices, user_id, "preview")
    generate = GenerateChoices(
        **choices.model_dump(),
        preview_hash=preview["preview_hash"],
        attempt_key=key or str(uuid4())
    )
    return parsed, preview, generate, content, user_id
