"""Explicit aggregate principal classifications, excluding leases and hedging."""

from copy import deepcopy

from .catalog import TerminologyRegistry, _entry
from .financial_v6 import FINANCIAL_TERMS_V6

DEBT_LABELS = {
    "debt_eligible_principal": (
        "principal total elegível da dívida sem leasing",
        "total eligible debt principal excluding leases",
        "principal total elegible de deuda sin arrendamientos",
        "principal total éligible de dette hors locations",
    ),
    "debt_fixed_principal": (
        "principal da dívida sem leasing a taxa fixa antes de hedge",
        "fixed-rate debt principal excluding leases before hedging",
        "principal de deuda a tipo fijo sin arrendamientos antes de cobertura",
        "principal de dette à taux fixe hors locations avant couverture",
    ),
    "debt_variable_principal": (
        "principal da dívida sem leasing a taxa variável antes de hedge",
        "variable-rate debt principal excluding leases before hedging",
        "principal de deuda a tipo variable sin arrendamientos antes de cobertura",
        "principal de dette à taux variable hors locations avant couverture",
    ),
    "debt_due_within_12_months": (
        "principal da dívida sem leasing vencível nos doze meses após a data de reporte",
        "debt principal excluding leases due within twelve months after the reporting date",
        "principal de deuda sin arrendamientos con vencimiento en los doce meses tras la fecha de reporte",
        "principal de dette hors locations exigible dans les douze mois après la date de clôture",
    ),
    "debt_due_after_12_months": (
        "principal da dívida sem leasing vencível após os doze meses seguintes à data de reporte",
        "debt principal excluding leases due after twelve months following the reporting date",
        "principal de deuda sin arrendamientos con vencimiento posterior a los doce meses tras la fecha de reporte",
        "principal de dette hors locations exigible après les douze mois suivant la date de clôture",
    ),
}
FINANCIAL_TERMS_V7 = (
    *deepcopy(FINANCIAL_TERMS_V6),
    *(
        _entry(
            key,
            "debt_schedule",
            "monetary_stock",
            "contextual",
            *((label,) for label in labels)
        )
        for key, labels in DEBT_LABELS.items()
    ),
)


class FinancialTerminologyV7(TerminologyRegistry):
    version = "financial-terminology-v7"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V7)
