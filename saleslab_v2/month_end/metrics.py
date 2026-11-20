"""Versioned source-line changes through the existing Decimal MetricEngine."""

from saleslab_v2.i18n.catalog import FINANCIAL_TERMS
from saleslab_v2.metrics.definitions import (
    MetricDefinition,
    MetricRegistry,
    OperandContract,
)

CONCEPTS = tuple(
    t.concept_id
    for t in FINANCIAL_TERMS
    if t.semantic_type in {"monetary_flow", "monetary_stock"}
)


def metric_id(concept, rule, operation):
    return f"month_end_{concept}_{rule}_{operation}"


def percentage_meets_threshold(delta, prior, threshold):
    """Exact absolute ratio comparison; no division or active Decimal context."""
    if prior == 0:
        return False
    numerator, numerator_denominator = delta.copy_abs().as_integer_ratio()
    denominator, denominator_denominator = prior.copy_abs().as_integer_ratio()
    cutoff, cutoff_denominator = threshold.as_integer_ratio()
    return (
        numerator * denominator_denominator * 100 * cutoff_denominator
        >= cutoff * denominator * numerator_denominator
    )


MONTH_END_METRICS = MetricRegistry(
    MetricDefinition(
        metric_id(concept, rule, operation),
        "1",
        f"Reported {concept} change; no causal or performance assessment",
        f"{expression}; concept={concept}; period_rule={rule}",
        ("current", "prior"),
        operation,
        unit,
        rounding_policy="percentage_2dp" if operation == "growth" else None,
        operand_contracts=tuple(
            OperandContract(role, (concept,), "monetary")
            for role in ("current", "prior")
        ),
        period_rule=rule,
    )
    for concept in CONCEPTS
    for rule in ("previous_month", "prior_year")
    for operation, expression, unit in (
        ("subtract", "current-prior", "monetary"),
        ("growth", "(current-prior)/abs(prior)*100", "percentage"),
    )
)
