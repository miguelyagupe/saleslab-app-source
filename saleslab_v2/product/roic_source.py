"""Verbatim, source-bound decisions for reported ROIC operands."""

import json

from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.i18n.financial_v9 import ROIC_LABELS

from .contracts import InvalidUpload
from .roic_contracts import RoicSourceChoices


def bind_roic_sources(original_rows, choices: RoicSourceChoices):
    """Return a detached, hashed source decision; numbers stay in the metric engine.

    This binding confirms exact source text only. Period, scope, currency, scale,
    denominator and normalization checks remain mandatory at planning/evaluation.
    """
    from .contracts import InvalidUpload

    selected = choices.model_dump()
    rows = list(original_rows)
    required = {
        "nopat": (choices.nopat_location, choices.nopat_definition),
        "tax_assumptions": (
            choices.tax_assumptions_location,
            choices.tax_assumptions,
        ),
        "average_invested_capital": (
            choices.average_capital_location,
            choices.average_capital_definition,
        ),
    }
    evidence = {}
    for role, (location, quote) in required.items():
        matches = [r for r in rows if r.get("location") == location]
        if len(matches) != 1:
            raise InvalidUpload(
                "ROIC evidence requires one unique source row per location."
            )
        row = matches[0]
        if not isinstance(row.get("line"), str) or quote not in row["line"]:
            raise InvalidUpload(
                "Copy ROIC definitions and tax assumptions verbatim from the source."
            )
        evidence[role] = {
            "location": location,
            "quote": quote,
            "source_row_hash": semantic_hash(row),
        }
    payload = {
        "schema": "financial-roic-source-binding-v1",
        "choices": selected,
        "evidence": evidence,
    }
    return {**payload, "binding_hash": semantic_hash(payload)}


class RoicReviewRequired(InvalidUpload):
    def __init__(self, payload):
        super().__init__(
            "Confirm reported NOPAT, tax assumptions and period-average invested capital from the source."
        )
        if len(json.dumps(payload, ensure_ascii=True).encode()) > 256_000:
            raise InvalidUpload("ROIC review exceeds the source evidence limit.")
        self.payload = payload


def check_roic_sources(originals, concepts, choices, digest):
    required = {
        loc: concept for loc, concept in concepts.items() if concept in ROIC_LABELS
    }
    if not required and choices.roic_sources is None:
        return None
    if choices.roic_sources is None and any(
        list(required.values()).count(concept) != 1 for concept in ROIC_LABELS
    ):
        # Let the ordinary preview render mapping/ambiguity controls first.
        # Normalization keeps any unconfirmed ROIC operand blocked.
        return None
    if choices.roic_sources is None:
        raise RoicReviewRequired(
            {
                "schema": "financial-roic-review-v1",
                "ready": False,
                "message": "Confirme na fonte o NOPAT reportado após impostos operacionais, os pressupostos fiscais explícitos e o capital investido médio do mesmo período e perímetro. Copie as citações das rubricas originais. Não use EBIT, resultado líquido ou capital apenas no fecho como substitutos. O ROIC será do período, sem anualização.",
                "rows": [
                    {**r, "concept_id": concepts.get(r["location"])} for r in originals
                ],
                "required_roles": list(ROIC_LABELS),
            }
        )
    selected = choices.roic_sources
    expected = {
        selected.nopat_location: "nopat_with_explicit_tax_assumptions",
        selected.average_capital_location: "average_invested_capital",
    }
    if any(required.get(loc) != concept for loc, concept in expected.items()) or set(
        required
    ) != set(expected):
        raise InvalidUpload(
            "Confirm one unique source row for each ROIC operand; selections must match the mapped concepts."
        )
    locations = {*expected, selected.tax_assumptions_location}
    source_rows = [r for r in originals if r["location"] in locations]
    value = {
        "source_hash": digest,
        "binding": bind_roic_sources(source_rows, selected),
        "original_rows": source_rows,
    }
    serialized = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    if len(serialized.encode()) > 64_000:
        raise InvalidUpload(
            "ROIC source evidence exceeds 64 KB; use shorter verbatim quotations."
        )
    return serialized
