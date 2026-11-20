"""Bind a common quoted perimeter to operating balances and reported changes."""

import json

from saleslab_v2.i18n.financial_v10 import (
    WORKING_CAPITAL_COMPONENTS,
    WORKING_CAPITAL_LABELS,
)

from .contracts import InvalidUpload


class WorkingCapitalReviewRequired(InvalidUpload):
    def __init__(self, payload):
        super().__init__(
            "Confirm operating working capital perimeter and closing-minus-opening disclosure basis from the source."
        )
        if len(json.dumps(payload, ensure_ascii=True).encode()) > 256_000:
            raise InvalidUpload(
                "Working capital source review exceeds 256 KB; select fewer rows."
            )
        self.payload = payload


def check_working_capital_definitions(originals, concepts, choices):
    selected = choices.working_capital_definitions or {}
    has_disclosure = any(c in WORKING_CAPITAL_LABELS for c in concepts.values())
    required_concepts = (
        {*WORKING_CAPITAL_LABELS, *WORKING_CAPITAL_COMPONENTS}
        if has_disclosure
        else set()
    )
    required = {
        r["location"]: r
        for r in originals
        if concepts.get(r["location"]) in required_concepts
    }
    if set(selected) - set(required):
        raise InvalidUpload(
            "A working capital definition refers to a row outside the selected operating disclosure and components."
        )
    pending = False
    quotes = set()
    for loc, row in required.items():
        quote = selected.get(loc)
        if quote is None:
            pending = True
            continue
        if quote != quote.strip() or len(quote) < 10 or quote not in row["line"]:
            raise InvalidUpload(
                "Copy the working capital perimeter definition verbatim from the original row label (at least ten characters)."
            )
        row["working_capital_definition_quote"] = quote
        quotes.add(quote)
    if len(quotes) > 1:
        raise InvalidUpload(
            "Working capital components and disclosure use inconsistent source definitions; do not combine perimeters."
        )
    if pending:
        # Keep the normal mapping UI accessible before showing source confirmation.
        if any(c is None for c in concepts.values()):
            return
        raise WorkingCapitalReviewRequired(
            {
                "schema": "financial-working-capital-review-v1",
                "ready": False,
                "message": "Cite a mesma definição do perímetro operacional em todos os saldos e na variação publicada. Ativos excluem caixa e ativos não operacionais; passivos excluem dívida e leasing. A variação é saldo final menos inicial, incluindo efeitos não monetários: não é o efeito em caixa nem explica o CFO. Não deduzimos componentes ou parcelas em falta.",
                "rows": [
                    {**r, "concept_id": concepts[loc]} for loc, r in required.items()
                ],
            }
        )
