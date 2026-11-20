"""Explicit synthetic monthly policy; never a default business materiality policy."""

import base64
import csv
import io
from uuid import uuid4

from product_fixtures import csv_content, upload_case

from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.month_end import MonthEndChoices, prepare_month_end


def month_end_case(workspace=None, user=None):
    workspace, user = workspace or uuid4(), user or uuid4()
    rows = list(csv.reader(io.StringIO(csv_content().decode()), delimiter=";"))
    for row in rows[1:]:
        row[2] = "October 2026"
    for amount, period, basis in (
        ("1100", "September 2026", "comparative"),
        ("800", "October 2025", "comparative"),
        ("10000", "YTD October 2026", "current"),
        ("8000", "YTD October 2025", "comparative"),
    ):
        rows.append(
            [
                "revenue",
                amount,
                period,
                "income_statement",
                "consolidated",
                "EUR",
                "raw",
                basis,
            ]
        )
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    content = out.getvalue().encode()
    choices = UploadChoices(
        source_language="en",
        format_locale="en-GB",
        report_language="pt",
        planning_version="8",
        normalization_version="2",
        target_period="October 2026",
    )
    parsed, preview, *_ = upload_case(workspace, user, content=content, choices=choices)
    close = MonthEndChoices(
        preview_hash=preview["preview_hash"],
        target_month="2026-10",
        policy_id="synthetic-monthly",
        policy_version="1",
        currency="EUR",
        fiscal_year_start_month=1,
        required_statements=["income_statement", "balance_sheet"],
        required_reconciliations=["REC-BS-001"],
        required_tasks=["review_sources"],
        required_comparisons=[
            "month_on_month",
            "month_year_on_year",
            "ytd_year_on_year",
        ],
        absolute_variation_threshold="10000",
        percentage_variation_threshold="50",
        materiality_basis="Synthetic test thresholds only",
        comparisons=[
            {
                "key": "revenue-mom",
                "current": "csv:2",
                "prior": "csv:7",
                "relation": "month_on_month",
            },
            {
                "key": "revenue-yoy",
                "current": "csv:2",
                "prior": "csv:8",
                "relation": "month_year_on_year",
            },
            {
                "key": "revenue-ytd",
                "current": "csv:9",
                "prior": "csv:10",
                "relation": "ytd_year_on_year",
            },
        ],
        movements=[
            {"key": "revenue-nonzero", "current": "csv:2", "kind": "nonzero_value"}
        ],
        confirmations=[
            {"task_id": "review_sources", "status": "complete", "evidence": ["csv:2"]}
        ],
    )
    return parsed, choices, close, preview, content, user


def browser_month_end_fixture():
    from datetime import UTC, datetime
    from uuid import UUID

    from saleslab_v2.product.month_end_catalog import reconciliation_catalog
    from saleslab_v2.product.month_end_saved import saved_payload

    parsed, choices, close, preview, content, user = month_end_case()
    result = prepare_month_end(parsed, choices, close, user)
    saved = saved_payload(
        result,
        UUID("33333333-3333-4333-8333-333333333333"),
        datetime(2026, 10, 28, tzinfo=UTC),
    )
    from saleslab_v2.product.planning import prepare_upload

    localized = {}
    for language in ("pt", "en", "es", "fr"):
        language_choices = choices.model_copy(update={"report_language": language})
        _, language_preview = prepare_upload(parsed, language_choices, user, "preview")
        language_close = close.model_copy(
            update={
                "preview_hash": language_preview["preview_hash"],
                "confirmations": [],
            }
        )
        localized[language] = saved_payload(
            prepare_month_end(parsed, language_choices, language_close, user),
            UUID("33333333-3333-4333-8333-333333333333"),
            datetime(2026, 10, 28, tzinfo=UTC),
        )
    return {
        "localized_saved_results": localized,
        "reconciliation_catalog": reconciliation_catalog(),
        "catalog_gap_result": prepare_month_end(
            parsed,
            choices,
            close.model_copy(
                update={
                    "required_reconciliations": ["REC-BS-001", "REC-WC-CLOSING-001"]
                }
            ),
            user,
        ),
        "saved_result": saved,
        "content": base64.b64encode(content).decode(),
        "choices": choices.model_dump(),
        "close": close.model_dump(),
        "preview": preview,
        "result": result,
        "incomplete_result": prepare_month_end(
            parsed, choices, close.model_copy(update={"confirmations": []}), user
        ),
    }
