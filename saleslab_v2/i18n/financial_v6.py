"""Additive monetary growth bases; no percentage aliases or inferred organic growth."""

from copy import deepcopy

from .catalog import TerminologyRegistry, _entry
from .financial_v5 import FINANCIAL_TERMS_V5

GROWTH_LABELS = {
    "revenue_constant_currency": (
        "receitas a câmbio constante conforme definição confirmada",
        "constant currency revenue under a confirmed definition",
        "ingresos a tipo de cambio constante según definición confirmada",
        "chiffre d’affaires à change constant selon définition confirmée",
    ),
    "revenue_like_for_like": (
        "receitas like-for-like com perímetro comparável confirmado",
        "like-for-like revenue with a confirmed comparable perimeter",
        "ingresos comparables con perímetro confirmado",
        "chiffre d’affaires comparable à périmètre confirmé",
    ),
    "growth_lfl_contribution": (
        "contributo monetário LFL para a variação reportada",
        "monetary LFL contribution to reported revenue change",
        "contribución monetaria comparable a la variación publicada",
        "contribution monétaire comparable à la variation publiée",
    ),
    "growth_expansion_acquisition_contribution": (
        "contributo monetário de expansão e aquisições para a variação reportada",
        "monetary expansion and acquisition contribution to reported revenue change",
        "contribución monetaria de expansión y adquisiciones a la variación publicada",
        "contribution monétaire d’expansion et acquisitions à la variation publiée",
    ),
    "growth_fx_contribution": (
        "contributo monetário cambial para a variação reportada",
        "monetary FX contribution to reported revenue change",
        "contribución monetaria de divisas a la variación publicada",
        "contribution monétaire de change à la variation publiée",
    ),
    "growth_other_contribution": (
        "outros contributos monetários para a variação reportada",
        "other monetary contributions to reported revenue change",
        "otras contribuciones monetarias a la variación publicada",
        "autres contributions monétaires à la variation publiée",
    ),
}
FINANCIAL_TERMS_V6 = (
    *deepcopy(FINANCIAL_TERMS_V5),
    *(
        _entry(
            key,
            "income_statement",
            "monetary_flow",
            "contextual",
            *((label,) for label in labels)
        )
        for key, labels in GROWTH_LABELS.items()
    ),
)


class FinancialTerminologyV6(TerminologyRegistry):
    version = "financial-terminology-v6"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V6)
