"""Human-confirmed, verbatim source definitions for monetary growth bases."""

import json

from .contracts import InvalidUpload

BASES = ("revenue_constant_currency", "revenue_like_for_like")
CONTRIBUTIONS = (
    "growth_lfl_contribution",
    "growth_expansion_acquisition_contribution",
    "growth_fx_contribution",
    "growth_other_contribution",
)
GROWTH_CONCEPTS = (*BASES, *CONTRIBUTIONS)


class GrowthReviewRequired(InvalidUpload):
    def __init__(self, payload):
        super().__init__(
            "Confirm the verbatim source definitions and comparable perimeter before calculating growth."
        )
        if len(json.dumps(payload, ensure_ascii=True).encode()) > 256_000:
            raise InvalidUpload(
                "Growth source review exceeds 256 KB; select fewer financial rows."
            )
        self.payload = payload


def check_definitions(originals, concepts, choices):
    selected = choices.growth_definitions or {}
    required = {
        r["location"]: r
        for r in originals
        if concepts.get(r["location"]) in GROWTH_CONCEPTS
    }
    if set(selected) - set(required):
        raise InvalidUpload(
            "A growth definition references a row outside the selected monetary growth concepts."
        )
    pending, grouped = [], {}
    for location, row in required.items():
        concept = concepts[location]
        quote = selected.get(location)
        if quote is None:
            pending.append({**row, "concept_id": concept})
            continue
        if quote != quote.strip() or len(quote) < 10 or quote not in row["line"]:
            raise InvalidUpload(
                "Copy the growth definition verbatim from the original row label (at least ten characters)."
            )
        row["growth_definition_quote"] = quote
        grouped.setdefault(
            "contribution_bridge" if concept in CONTRIBUTIONS else concept, set()
        ).add(quote)
    if pending:
        raise GrowthReviewRequired(
            {
                "schema": "financial-growth-review-v1",
                "ready": False,
                "message": "Cite a definição ou o perímetro documentado em cada rubrica de crescimento. As bases atual e comparativa de cada método devem citar a mesma definição; os contributos da ponte devem citar a mesma base reportada. Sem esta prova, corrija a fonte; não transforme percentagens isoladas em montantes.",
                "rows": [
                    {**r, "concept_id": concepts[loc]} for loc, r in required.items()
                ],
            }
        )
    if any(len(quotes) > 1 for quotes in grouped.values()):
        raise InvalidUpload(
            "Growth bases or bridge contributions have different quoted definitions; do not compare or combine them."
        )
