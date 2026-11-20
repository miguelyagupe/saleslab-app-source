"""Confirmed cash capex classifications and separate outstanding commitments."""

from copy import deepcopy

from .catalog import TerminologyRegistry, _entry
from .financial_v7 import FINANCIAL_TERMS_V7

CAPEX_LABELS = {
    "capex_cash_total": (
        "total de aquisições de ativos fixos pagas conforme classificação confirmada",
        "total cash purchases of fixed assets under the confirmed classification",
        "total de adquisiciones pagadas de inmovilizado según clasificación confirmada",
        "total des acquisitions d’immobilisations payées selon la classification confirmée",
    ),
    "capex_cash_maintenance": (
        "aquisições de ativos fixos pagas para manutenção conforme definição confirmada",
        "cash purchases of fixed assets for maintenance under a confirmed definition",
        "adquisiciones pagadas de inmovilizado para mantenimiento según definición confirmada",
        "acquisitions d’immobilisations payées pour maintien selon une définition confirmée",
    ),
    "capex_cash_growth": (
        "aquisições de ativos fixos pagas para crescimento conforme definição confirmada",
        "cash purchases of fixed assets for growth under a confirmed definition",
        "adquisiciones pagadas de inmovilizado para crecimiento según definición confirmada",
        "acquisitions d’immobilisations payées pour croissance selon une définition confirmée",
    ),
    "capex_commitments_outstanding": (
        "compromissos de aquisição de ativos fixos por executar na data de reporte conforme definição confirmada",
        "outstanding fixed-asset purchase commitments at the reporting date under a confirmed definition",
        "compromisos pendientes de adquisición de inmovilizado a la fecha de reporte según definición confirmada",
        "engagements d’acquisition d’immobilisations restant à exécuter à la clôture selon une définition confirmée",
    ),
}
CASH_CAPEX_CONCEPTS = (
    "capex_cash_total",
    "capex_cash_maintenance",
    "capex_cash_growth",
)
COMMITMENT = "capex_commitments_outstanding"
FINANCIAL_TERMS_V8 = (
    *deepcopy(FINANCIAL_TERMS_V7),
    *(
        _entry(
            key,
            "capex_schedule",
            "monetary_stock" if key == COMMITMENT else "monetary_flow",
            "contextual",
            *((label,) for label in labels)
        )
        for key, labels in CAPEX_LABELS.items()
    ),
)


class FinancialTerminologyV8(TerminologyRegistry):
    version = "financial-terminology-v8"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V8)
