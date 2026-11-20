"""Explicit profitability statements with independent reported EBITDA values."""

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v4 import FINANCIAL_TERMS_V4
from saleslab_v2.product.contracts import UploadChoices

TERMS = {t.concept_id: t for t in FINANCIAL_TERMS_V4}
VALUES = (
    ("ebit", "200"),
    ("cfo", "300"),
    ("cost_of_sales", "600"),
    ("operating_costs", "810"),
    ("other_operating_income_expense", "10"),
    ("net_finance_cost", "60"),
    ("income_tax", "40"),
    ("depreciation_and_amortization", "50"),
    ("ebitda_adjustments", "20"),
    ("reported_statutory_ebitda", "250"),
    ("reported_adjusted_ebitda", "270"),
)


def profitability_choices(**updates):
    return UploadChoices(
        **{
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "planning_version": "7",
            "normalization_version": "2",
            "target_period": "2026",
            **updates,
        }
    )


def profitability_rows(
    *, language="en", scale="raw", period="2026", changes=None, omit=()
):
    rows = []
    for concept, amount in VALUES:
        if concept in omit:
            continue
        t = TERMS[concept]
        row = {
            "line": t.labels[LanguageCode(language)],
            "amount": amount,
            "period": period,
            "statement_type": t.statement_type,
            "scope": "consolidated",
            "currency": "EUR",
            "scale": scale,
            "comparison_basis": "current",
        }
        row.update((changes or {}).get(concept, {}))
        rows.append(tuple(row.values()))
    return rows
