"""Reported NOPAT and explicit period-average invested capital; no tax inference."""

from copy import deepcopy

from .catalog import TerminologyRegistry, _entry
from .financial_v8 import FINANCIAL_TERMS_V8

ROIC_LABELS = {
    "nopat_with_explicit_tax_assumptions": (
        "NOPAT reportado com pressupostos fiscais explícitos",
        "reported NOPAT with explicit tax assumptions",
        "NOPAT publicado con supuestos fiscales explícitos",
        "NOPAT publié avec hypothèses fiscales explicites",
    ),
    "average_invested_capital": (
        "capital investido médio reportado para o período",
        "reported average invested capital for the period",
        "capital invertido medio publicado para el período",
        "capital investi moyen publié pour la période",
    ),
}
FINANCIAL_TERMS_V9 = (
    *deepcopy(FINANCIAL_TERMS_V8),
    *(
        _entry(
            key,
            "roic_schedule",
            "monetary_stock" if key == "average_invested_capital" else "monetary_flow",
            "contextual",
            *((label,) for label in labels)
        )
        for key, labels in ROIC_LABELS.items()
    ),
)


class FinancialTerminologyV9(TerminologyRegistry):
    version = "financial-terminology-v9"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V9)
