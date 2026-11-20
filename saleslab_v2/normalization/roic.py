"""Independent ROIC evidence and semantic validation, also applied during replay."""

import json
from dataclasses import replace

from saleslab_v2.domain.financial import NormalizationIssue
from saleslab_v2.i18n.financial_v9 import ROIC_LABELS


def validate_roic_observations(rows, source, observations):
    from saleslab_v2.product.roic_source import RoicSourceChoices, bind_roic_sources

    indexes = [i for i, o in enumerate(observations) if o.concept_id in ROIC_LABELS]
    if not indexes:
        return observations
    code = None
    try:
        value = json.loads(source.roic_binding or "null")
        if (
            set(value) != {"source_hash", "binding", "original_rows"}
            or value["source_hash"] != source.content_hash
        ):
            raise ValueError("source mismatch")
        binding = value["binding"]
        choice = RoicSourceChoices.model_validate(binding["choices"])
        if bind_roic_sources(value["original_rows"], choice) != binding:
            raise ValueError("binding mismatch")
        by_location = {r.source_reference: (r, o) for r, o in zip(rows, observations)}
        expected = {
            choice.nopat_location: "nopat_with_explicit_tax_assumptions",
            choice.average_capital_location: "average_invested_capital",
        }
        if {rows[i].source_reference for i in indexes} != set(expected):
            raise ValueError("unconfirmed or duplicate operands")
        for loc, concept in expected.items():
            if by_location[loc][1].concept_id != concept:
                raise ValueError("operand identity mismatch")
        for original in value["original_rows"]:
            r, _ = by_location[original["location"]]
            fields = {
                "amount": r.text,
                "period": r.period_label,
                "statement_type": r.statement_type,
                "scope": r.scope,
                "currency": r.currency or "",
                "scale": r.scale or "",
                "comparison_basis": r.comparison_basis,
            }
            if any(original[k] != v for k, v in fields.items()):
                raise ValueError("stale row evidence")
        a, b, tax = (
            by_location[loc][1]
            for loc in (
                choice.nopat_location,
                choice.average_capital_location,
                choice.tax_assumptions_location,
            )
        )
        if any(
            o.period is None
            or o.period.identity != a.period.identity
            or o.scope != a.scope
            or o.dimensions != a.dimensions
            for o in (a, b, tax)
        ):
            code = "roic.incompatible_period_or_scope"
        elif a.period.comparison_basis != "current":
            code = "roic.current_period_required"
        elif a.currency != b.currency or a.unit != "monetary" or b.unit != "monetary":
            code = "roic.incompatible_currency_or_unit"
        elif b.value is not None and b.value <= 0:
            code = "roic.positive_average_required"
    except (TypeError, ValueError, KeyError, AttributeError, IndexError):
        code = "roic.source_confirmation_required"
    if code:
        for i in indexes:
            o = observations[i]
            observations[i] = replace(
                o,
                normalization_status="needs_confirmation",
                issues=o.issues + (NormalizationIssue(code, "original_name"),),
            )
    return observations
