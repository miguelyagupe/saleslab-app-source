"""A shared source definition binds aggregate rate and contractual maturity bases."""

import json

from saleslab_v2.i18n.financial_v7 import DEBT_LABELS

from .contracts import InvalidUpload


class DebtReviewRequired(InvalidUpload):
    def __init__(self, payload):
        super().__init__(
            "Confirm the shared debt principal, lease exclusion and reporting-date basis from the source."
        )
        if len(json.dumps(payload, ensure_ascii=True).encode()) > 256_000:
            raise InvalidUpload(
                "Debt source review exceeds 256 KB; select fewer financial rows."
            )
        self.payload = payload


def check_debt_definitions(originals, concepts, choices):
    selected = choices.debt_definitions or {}
    required = {
        r["location"]: r
        for r in originals
        if concepts.get(r["location"]) in DEBT_LABELS
    }
    if set(selected) - set(required):
        raise InvalidUpload(
            "A debt definition references a row outside the selected debt schedule concepts."
        )
    pending = False
    quotes = set()
    for location, row in required.items():
        quote = selected.get(location)
        if quote is None:
            pending = True
            continue
        if quote != quote.strip() or len(quote) < 10 or quote not in row["line"]:
            raise InvalidUpload(
                "Copy the debt definition verbatim from the original row label (at least ten characters)."
            )
        row["debt_definition_quote"] = quote
        quotes.add(quote)
    if pending:
        raise DebtReviewRequired(
            {
                "schema": "financial-debt-review-v1",
                "ready": False,
                "message": "Cite a mesma definição de principal, exclusão de leasing e data de referência em todas as rubricas. Taxa fixa/variável é a classificação contratual antes de hedge; os vencimentos abrangem o principal após a data de reporte. Se a fonte não comprova estas bases, corrija-a antes de calcular.",
                "rows": [
                    {**r, "concept_id": concepts[loc]} for loc, r in required.items()
                ],
            }
        )
    if len(quotes) > 1:
        raise InvalidUpload(
            "Debt classifications use different quoted principal or reporting-date bases; do not combine them."
        )
