"""Reported operating working capital and signed balance change, never cash flow."""

from copy import deepcopy

from .catalog import TerminologyRegistry, _entry
from .financial_v9 import FINANCIAL_TERMS_V9

WORKING_CAPITAL_LABELS = {
    "reported_operating_nwc": (
        "fundo de maneio operacional reportado conforme perímetro confirmado",
        "reported operating net working capital under the confirmed perimeter",
        "capital circulante operativo publicado según el perímetro confirmado",
        "fonds de roulement opérationnel publié selon le périmètre confirmé",
    ),
    "reported_operating_nwc_change": (
        "variação reportada do fundo de maneio operacional entre saldos final e inicial",
        "reported operating net working capital closing minus opening balance change",
        "variación publicada del capital circulante operativo entre saldos final e inicial",
        "variation publiée du fonds de roulement opérationnel entre soldes final et initial",
    ),
}
WORKING_CAPITAL_COMPONENTS = (
    "current_operating_assets",
    "current_operating_liabilities",
)
FINANCIAL_TERMS_V10 = (
    *deepcopy(FINANCIAL_TERMS_V9),
    *(
        _entry(
            key,
            "working_capital_detail",
            "monetary_stock" if key == "reported_operating_nwc" else "monetary_flow",
            "contextual",
            *((label,) for label in labels)
        )
        for key, labels in WORKING_CAPITAL_LABELS.items()
    ),
)


class FinancialTerminologyV10(TerminologyRegistry):
    version = "financial-terminology-v10"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V10)
