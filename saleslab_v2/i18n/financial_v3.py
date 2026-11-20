"""Pinned extension for an explicitly reported cash FX/other movement."""

from copy import deepcopy

from .catalog import TerminologyRegistry, _entry
from .financial_v2 import FINANCIAL_TERMS_V2

FINANCIAL_TERMS_V3 = (
    *deepcopy(FINANCIAL_TERMS_V2),
    _entry(
        "cash_fx_other",
        "cash_flow_statement",
        "monetary_flow",
        "contextual",
        ("efeitos cambiais e outros movimentos de caixa",),
        ("cash foreign exchange and other movements",),
        ("efectos de cambio y otros movimientos de efectivo",),
        ("effets de change et autres mouvements de trésorerie",),
    ),
)


class FinancialTerminologyV3(TerminologyRegistry):
    version = "financial-terminology-v3"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V3)
