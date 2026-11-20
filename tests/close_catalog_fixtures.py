"""Monthly operating-disclosure evidence for explicit reconciliation policies."""

import csv
import io

from product_fixtures import upload_case
from working_capital_fixtures import working_capital_case

from saleslab_v2.product.month_end import MonthEndChoices


def close_catalog_case(workspace=None, user=None, *, required=None, changes=None):
    w, u, content, choices, _locations = working_capital_case(
        workspace, user, changes=changes
    )
    rows = list(csv.reader(io.StringIO(content.decode()), delimiter=";"))
    for row in rows[1:]:
        row[2] = "September 2026" if row[2] == "2025" else "October 2026"
    prior = "csv:" + str(len(rows) + 1)
    rows.append(
        [
            "revenue",
            "900",
            "September 2026",
            "income_statement",
            "consolidated",
            "EUR",
            "raw",
            "comparative",
        ]
    )
    out = io.StringIO()
    csv.writer(out, delimiter=";").writerows(rows)
    content = out.getvalue().encode()
    choices = choices.model_copy(update={"target_period": "October 2026"})
    parsed, preview, *_ = upload_case(w, u, content=content, choices=choices)
    close = MonthEndChoices(
        preview_hash=preview["preview_hash"],
        target_month="2026-10",
        policy_id="source-confirmed-monthly-disclosure",
        policy_version="1",
        currency="EUR",
        fiscal_year_start_month=1,
        required_statements=["income_statement", "balance_sheet"],
        required_reconciliations=required
        or ["REC-WC-CLOSING-001", "REC-WC-CHANGE-001"],
        required_tasks=["review_sources"],
        required_comparisons=["month_on_month"],
        absolute_variation_threshold="10000",
        percentage_variation_threshold="50",
        materiality_basis="Explicit synthetic test policy only",
        comparisons=[
            {
                "key": "revenue-mom",
                "current": "csv:2",
                "prior": prior,
                "relation": "month_on_month",
            }
        ],
        confirmations=[
            {"task_id": "review_sources", "status": "complete", "evidence": ["csv:2"]}
        ],
    )
    return parsed, choices, close, content, u
