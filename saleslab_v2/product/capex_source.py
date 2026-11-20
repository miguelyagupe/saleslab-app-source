"""Verbatim source definitions keep paid capex and commitments separate."""

import json

from saleslab_v2.i18n.financial_v8 import CAPEX_LABELS, CASH_CAPEX_CONCEPTS, COMMITMENT

from .contracts import InvalidUpload


class CapexReviewRequired(InvalidUpload):
    def __init__(self, payload):
        super().__init__(
            "Confirm cash capex classification and outstanding commitment definitions from the source."
        )
        if len(json.dumps(payload, ensure_ascii=True).encode()) > 256_000:
            raise InvalidUpload(
                "Capex source review exceeds 256 KB; select fewer financial rows."
            )
        self.payload = payload


def capex_required_concepts(concepts):
    return {
        *CAPEX_LABELS,
        *({"capex"} if set(concepts) & set(CASH_CAPEX_CONCEPTS) else set()),
    }


def check_capex_definitions(originals, concepts, choices):
    selected = choices.capex_definitions or {}
    required_concepts = capex_required_concepts(concepts.values())
    required = {
        r["location"]: r
        for r in originals
        if concepts.get(r["location"]) in required_concepts
    }
    if set(selected) - set(required):
        raise InvalidUpload(
            "A capex definition references a row outside the selected capex sources."
        )
    pending = False
    groups = {}
    for loc, row in required.items():
        quote = selected.get(loc)
        if quote is None:
            pending = True
            continue
        if quote != quote.strip() or len(quote) < 10 or quote not in row["line"]:
            raise InvalidUpload(
                "Copy the capex definition verbatim from the original row label (at least ten characters)."
            )
        row["capex_definition_quote"] = quote
        group = "commitment" if concepts[loc] == COMMITMENT else "cash"
        groups.setdefault(group, set()).add(quote)
    if pending:
        raise CapexReviewRequired(
            {
                "schema": "financial-capex-review-v1",
                "ready": False,
                "message": "Cite a definição da classificação manutenção/crescimento e da base de pagamentos em todas as rubricas de capex pago, incluindo a linha independente de aquisições nos fluxos de caixa quando presente. A mesma definição deve comprovar o total e as duas parcelas. Cite separadamente a definição dos compromissos por executar: não são pagamentos nem uma previsão de caixa. Se a fonte não comprova estas bases, corrija-a antes de calcular.",
                "rows": [
                    {**r, "concept_id": concepts[loc]} for loc, r in required.items()
                ],
            }
        )
    if any(len(q) > 1 for q in groups.values()):
        raise InvalidUpload(
            "Capex cash classifications or commitments have inconsistent quoted source definitions; do not combine them."
        )
