"""Pinned extension for explicit current balance-sheet classifications."""

from copy import deepcopy

from .catalog import FINANCIAL_TERMS, TerminologyRegistry, _entry

FINANCIAL_TERMS_V2 = (
    *deepcopy(FINANCIAL_TERMS),
    _entry(
        "current_assets",
        "balance_sheet",
        "monetary_stock",
        "debit",
        ("ativo corrente", "ativos correntes"),
        ("current assets",),
        ("activos corrientes",),
        ("actif courant", "actifs courants"),
    ),
    _entry(
        "current_liabilities",
        "balance_sheet",
        "monetary_stock",
        "credit",
        ("passivo corrente", "passivos correntes"),
        ("current liabilities",),
        ("pasivos corrientes",),
        ("passif courant", "passifs courants"),
    ),
)


class FinancialTerminologyV2(TerminologyRegistry):
    version = "financial-terminology-v2"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V2)
