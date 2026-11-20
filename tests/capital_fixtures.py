"""Explicit liquidity classifications, period averages and complete debt/lease movements."""

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v5 import FINANCIAL_TERMS_V5
from saleslab_v2.product.contracts import UploadChoices

TERMS = {t.concept_id: t for t in FINANCIAL_TERMS_V5}
VALUES = (
    ("current_assets", "800"),
    ("current_liabilities", "200"),
    ("eligible_unrestricted_cash", "100"),
    ("eligible_trade_receivables", "60"),
    ("other_permitted_liquid_assets", "40"),
    ("current_operating_assets", "300"),
    ("current_operating_liabilities", "180"),
    ("average_trade_receivables", "100"),
    ("average_inventory", "120"),
    ("average_trade_payables", "90"),
    ("purchases", "720"),
    ("interest_expense", "40"),
    ("debt_excluding_leases", "600"),
    ("lease_liabilities", "120"),
    ("debt_drawdowns", "150"),
    ("debt_repayments", "70"),
    ("debt_fx_other", "20"),
    ("lease_additions", "30"),
    ("lease_modifications", "-5"),
    ("lease_interest", "10"),
    ("lease_payments", "20"),
    ("lease_fx_other", "5"),
)


def capital_choices(**updates):
    return UploadChoices(
        **{
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
            "planning_version": "8",
            "normalization_version": "2",
            "target_period": "2026",
            **updates,
        }
    )


def capital_rows(
    *, language="en", scale="raw", period="2026", opening="2025", changes=None, omit=()
):
    rows = []
    data = [
        *((c, amount, period) for c, amount in VALUES),
        ("opening_debt", "500", opening),
        ("opening_lease", "100", opening),
    ]
    for key, amount, interval in data:
        if key in omit:
            continue
        concept = {
            "opening_debt": "debt_excluding_leases",
            "opening_lease": "lease_liabilities",
        }.get(key, key)
        t = TERMS[concept]
        row = {
            "line": t.labels[LanguageCode(language)],
            "amount": amount,
            "period": interval,
            "statement_type": t.statement_type,
            "scope": "consolidated",
            "currency": "EUR",
            "scale": scale,
            "comparison_basis": "current",
        }
        row.update((changes or {}).get(key, {}))
        rows.append(tuple(row.values()))
    return rows


BASE_BALANCES = {"total_assets": "1500", "total_liabilities": "1000", "equity": "500"}


def capital_content(
    *,
    language="en",
    scale="raw",
    period="2026",
    opening="2025",
    changes=None,
    omit=(),
    extra=(),
    base_changes=None,
    profit_changes=None,
    profit_omit=()
):
    from product_fixtures import csv_content
    from profitability_fixtures import profitability_rows

    base = {
        c: {"period": period, "scale": scale}
        for c in ("revenue", "net_income", *BASE_BALANCES)
    }
    for c, amount in BASE_BALANCES.items():
        base[c]["amount"] = amount
    for c, updates in (base_changes or {}).items():
        base[c].update(updates)
    return csv_content(
        language=language,
        changes=base,
        extra=[
            *profitability_rows(
                language=language,
                scale=scale,
                period=period,
                changes=profit_changes,
                omit=profit_omit,
            ),
            *capital_rows(
                language=language,
                scale=scale,
                period=period,
                opening=opening,
                changes=changes,
                omit=omit,
            ),
            *extra,
        ],
    )
