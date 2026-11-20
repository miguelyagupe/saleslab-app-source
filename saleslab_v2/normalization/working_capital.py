"""Confirm one operating perimeter across balance components and disclosures."""

from dataclasses import replace

from saleslab_v2.domain.financial import NormalizationIssue
from saleslab_v2.i18n.financial_v10 import (
    WORKING_CAPITAL_COMPONENTS,
    WORKING_CAPITAL_LABELS,
)


def validate_working_capital(rows, observations):
    if not any(o.concept_id in WORKING_CAPITAL_LABELS for o in observations):
        return observations
    required = {*WORKING_CAPITAL_LABELS, *WORKING_CAPITAL_COMPONENTS}
    selected = [
        (i, r.definition_quote)
        for i, (r, o) in enumerate(zip(rows, observations))
        if o.concept_id in required
    ]
    mismatch = len({quote for _, quote in selected if quote}) > 1
    for i, quote in selected:
        o = observations[i]
        errors = []
        if not quote:
            errors.append(
                NormalizationIssue(
                    "working_capital.definition_required", "original_name"
                )
            )
        if mismatch:
            errors.append(
                NormalizationIssue(
                    "working_capital.definition_mismatch", "original_name"
                )
            )
        if (
            o.concept_id in WORKING_CAPITAL_COMPONENTS
            and o.value is not None
            and o.value < 0
        ):
            errors.append(
                NormalizationIssue("working_capital.negative_component", "value")
            )
        if errors:
            observations[i] = replace(
                o,
                normalization_status="needs_confirmation",
                issues=o.issues + tuple(errors),
            )
    return observations
