"""Explicit F-02/F-03/F-04 seams; never assign an operand's requested identity."""

from dataclasses import replace
from uuid import uuid5

from saleslab_v2.metrics.engine import IncompatibleInputError, MetricInput
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.reconciliation.contracts import ReconciliationInput

ALIASES = {
    "revenue": "reported_revenue",
    "cfo": "reported_cfo",
    "ebit": "ebit_confirmed",
    "cash_and_equivalents": "cash_and_cash_equivalents",
}
VARIANTS = {
    "lease_liabilities": ("lease_bridge",),
    "debt_drawdowns": ("debt_bridge",),
    "debt_repayments": ("debt_bridge",),
    "debt_fx_other": ("debt_bridge",),
    "lease_additions": ("lease_bridge",),
    "lease_modifications": ("lease_bridge",),
    "lease_interest": ("lease_bridge",),
    "lease_payments": ("lease_bridge",),
    "lease_fx_other": ("lease_bridge",),
    "ebitda_adjustments": ("adjustment_bridge",),
    "ebitda_statutory": ("statutory", "ebitda_statutory"),
    "ebitda_adjusted": ("adjusted", "ebitda_adjusted"),
    "net_debt": ("excluding_leases",),
    "net_debt_incl_leases": ("including_leases",),
    "ebit_confirmed": ("ebit",),
}


def identity_for(identity, permitted, period=None):
    if identity in permitted:
        return identity
    if identity == "revenue" and period is not None:
        temporal = {"current": "revenue_current", "comparative": "revenue_prior"}.get(
            period.comparison_basis
        )
        if temporal in permitted:
            return temporal
    alias = ALIASES.get(identity)
    return alias if alias in permitted else identity


def variant_for(identity, permitted, definition_version=None):
    variants = (
        ("excluding_leases",)
        if identity == "gross_debt" and definition_version == "2"
        else VARIANTS.get(identity, ())
    )
    options = set(variants) & set(permitted)
    if len(options) == 1:
        return options.pop()
    return None


def normalized_id(observation):
    return uuid5(TRACE_NAMESPACE, "normalized:" + observation.semantic_hash)


def metric_input(observation, contract):
    if hasattr(observation, "normalization_status"):
        if (
            observation.normalization_status != "normalized"
            or observation.value is None
            or observation.period is None
            or observation.issues
        ):
            raise IncompatibleInputError("source observation is not calculation-ready")
        identity = identity_for(
            observation.concept_id, contract.metric_ids, observation.period
        )
        return MetricInput(
            normalized_id(observation),
            identity,
            observation.value,
            observation.unit,
            observation.currency,
            observation.period,
            observation.scope,
            observation.dimensions,
            (observation.source_reference,),
            (
                (
                    "normalization:" + str(normalized_id(observation)),
                    observation.semantic_hash,
                ),
            ),
            "confirmed",
            variant_for(identity, contract.variants),
        )
    return MetricInput(
        observation.observation_id,
        identity_for(observation.metric_id, contract.metric_ids, observation.period),
        observation.value,
        observation.unit,
        observation.currency,
        observation.period,
        observation.scope,
        observation.dimensions,
        observation.source_references,
        observation.provenance,
        "calculated",
        variant_for(
            observation.metric_id,
            contract.variants,
            observation.metric_definition_version,
        ),
    )


def reconciliation_input(role, observation, requirement):
    if hasattr(observation, "normalization_status"):
        value = ReconciliationInput.from_normalized_observation(
            role, normalized_id(observation), observation
        )
        # F-02 values are already expanded to base currency. Preserve the source
        # scale in its audit stage, but compare base values against a raw policy.
        return replace(value, scale="raw" if value.unit == "monetary" else "unit")
    return ReconciliationInput(
        role,
        observation.observation_id,
        observation.metric_id,
        observation.value,
        observation.unit,
        observation.currency,
        "raw" if observation.unit == "monetary" else "unit",
        observation.period.identity,
        observation.scope,
        observation.dimensions,
        "calculated",
        observation.source_references,
        observation.provenance,
        variant_for(
            observation.metric_id,
            requirement.variants,
            observation.metric_definition_version,
        ),
        observation.calculation_trace_id,
        observation.calculation_trace_hash,
    )


def scope_metric(workspace_id, run_id, observation, trace):
    return replace(
        observation,
        observation_id=uuid5(
            TRACE_NAMESPACE, f"observation:{workspace_id}:{run_id}:{trace.trace_hash}"
        ),
        calculation_trace_id=uuid5(
            TRACE_NAMESPACE, f"{workspace_id}:{run_id}:{trace.trace_hash}"
        ),
    )
