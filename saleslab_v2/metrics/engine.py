from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from uuid import UUID, uuid5

from saleslab_v2.domain.normalization import (
    canonical_dimensions,
    require_finite_decimal,
)
from saleslab_v2.domain.value_objects import Period

from .definitions import MetricDefinition, MetricRegistry
from .rounding import POLICIES
from .trace import TRACE_NAMESPACE, CalculationTrace, TraceInput

ENGINE_VERSION = "metric-engine-v1"
DIVISION_POLICY = "decimal-significant-digits-v1:100"


class MetricCalculationError(ValueError):
    pass


class MissingInputError(MetricCalculationError):
    pass


class IncompatibleInputError(MetricCalculationError):
    pass


class ZeroDenominatorError(MetricCalculationError):
    pass


@dataclass(frozen=True)
class MetricInput:
    observation_id: UUID
    metric_id: str
    value: Decimal
    unit: str
    currency: str | None
    period: Period
    scope: str
    dimensions: tuple[tuple[str, str], ...] = ()
    source_references: tuple[str, ...] = ()
    provenance: tuple[tuple[str, str], ...] = ()
    normalization_status: str = "confirmed"
    variant: str | None = None

    def __post_init__(self):
        if not isinstance(self.observation_id, UUID):
            raise TypeError("observation_id must be UUID")
        require_finite_decimal(self.value, "input value")
        if self.unit not in {"monetary", "percentage", "ratio", "days"}:
            raise ValueError("unsupported input unit")
        if self.unit == "monetary" and not self.currency:
            raise ValueError("monetary input requires currency")
        if self.unit != "monetary" and self.currency is not None:
            raise ValueError("non-monetary input cannot carry currency")
        object.__setattr__(self, "dimensions", canonical_dimensions(self.dimensions))
        object.__setattr__(self, "provenance", canonical_dimensions(self.provenance))
        object.__setattr__(
            self, "source_references", tuple(sorted(set(self.source_references)))
        )
        if self.normalization_status not in {
            "confirmed",
            "calculated",
            "needs_confirmation",
            "unresolved",
            "reported_unverified",
        }:
            raise ValueError("unsupported normalization status")


@dataclass(frozen=True)
class MetricObservation:
    observation_id: UUID
    metric_id: str
    metric_definition_version: str
    value: Decimal
    rounded_value: Decimal | None
    unit: str
    currency: str | None
    period: Period
    scope: str
    dimensions: tuple[tuple[str, str], ...]
    source_references: tuple[str, ...]
    provenance: tuple[tuple[str, str], ...]
    calculation_trace_id: UUID
    calculation_trace_hash: str
    status: str = "calculated"


class MetricEngine:
    def __init__(self, registry: MetricRegistry):
        self.registry = registry

    def calculate(
        self, metric_id: str, inputs: dict[str, MetricInput], version: str = "1"
    ) -> tuple[MetricObservation, CalculationTrace]:
        definition = self.registry.get(metric_id, version)
        if set(inputs) != set(definition.inputs):
            missing = sorted(set(definition.inputs) - set(inputs))
            extra = sorted(set(inputs) - set(definition.inputs))
            raise MissingInputError(f"input mismatch; missing={missing}, extra={extra}")
        ordered = tuple(inputs[name] for name in definition.inputs)
        self._validate(definition, ordered)
        values = tuple(item.value for item in ordered)
        with localcontext() as context:
            context.prec = self._precision(definition.operation, values)
            result = (
                Decimal(
                    (ordered[0].period.end_date - ordered[0].period.start_date).days + 1
                )
                if definition.operation == "calendar_days"
                else self._apply(definition.operation, values)
            )
        require_finite_decimal(result, "calculated value")
        policy = POLICIES.get(definition.rounding_policy)
        rounded = policy.apply(result) if policy else None
        trace_inputs = tuple(
            TraceInput(
                role,
                item.observation_id,
                item.metric_id,
                item.value,
                item.unit,
                item.currency,
                item.period.identity,
                item.scope,
                item.dimensions,
                item.source_references,
                item.provenance,
                item.normalization_status,
                item.variant,
            )
            for role, item in zip(definition.inputs, ordered)
        )
        trace = CalculationTrace(
            metric_id,
            version,
            definition.canonical_formula,
            trace_inputs,
            result,
            rounded,
            policy.policy_id if policy else None,
            policy.version if policy else None,
            ENGINE_VERSION,
            definition.unit_family,
            ordered[0].currency if definition.unit_family == "monetary" else None,
            ordered[0].period.identity,
            ordered[0].scope,
            ordered[0].dimensions,
            (
                DIVISION_POLICY
                if definition.operation in {"percent", "ratio", "growth", "days", "mix"}
                else None
            ),
        )
        refs = tuple(
            sorted({ref for item in ordered for ref in item.source_references})
        )
        provenance = tuple(
            sorted({pair for item in ordered for pair in item.provenance})
        )
        currency = ordered[0].currency if definition.unit_family == "monetary" else None
        observation = MetricObservation(
            uuid5(TRACE_NAMESPACE, "observation:" + trace.trace_hash),
            metric_id,
            version,
            result,
            rounded,
            definition.unit_family,
            currency,
            ordered[0].period,
            ordered[0].scope,
            ordered[0].dimensions,
            refs,
            provenance,
            trace.trace_id,
            trace.trace_hash,
        )
        return observation, trace

    def _validate(
        self, definition: MetricDefinition, inputs: tuple[MetricInput, ...]
    ) -> None:
        first = inputs[0]
        if first.scope not in definition.scope_rules:
            raise IncompatibleInputError("scope is not allowed")
        for item in inputs[1:]:
            if item.scope != first.scope or item.dimensions != first.dimensions:
                raise IncompatibleInputError(
                    "scope/dimensions mismatch; aggregation is forbidden"
                )
        for contract, item in zip(definition.operand_contracts, inputs):
            if contract.value_constraint == "non_positive" and item.value > 0:
                raise IncompatibleInputError(f"{contract.role} must be nonpositive")
            if contract.value_constraint == "non_negative" and item.value < 0:
                raise IncompatibleInputError(f"{contract.role} must be nonnegative")
            if contract.value_constraint == "positive" and item.value <= 0:
                raise IncompatibleInputError(f"{contract.role} must be positive")
            if item.metric_id not in contract.metric_ids:
                raise IncompatibleInputError(
                    f"{contract.role} metric identity is not permitted"
                )
            if item.unit != contract.unit_family:
                raise IncompatibleInputError(
                    f"{contract.role} requires {contract.unit_family}"
                )
            if item.normalization_status not in contract.normalization_statuses:
                raise IncompatibleInputError(
                    f"{contract.role} normalization status is not permitted"
                )
            if item.variant not in contract.variants:
                raise IncompatibleInputError(
                    f"{contract.role} variant is not permitted"
                )
            dimensions = dict(item.dimensions)
            if any(dimensions.get(k) != v for k, v in contract.required_dimensions):
                raise IncompatibleInputError(
                    f"{contract.role} required dimensions are absent"
                )
        if definition.period_rule != "standard":
            self._validate_comparison_periods(definition.period_rule, inputs)
        elif definition.operation in {"growth", "growth_delta"}:
            current, prior = inputs
            a, b = current.period, prior.period
            duration_a = (
                (a.end_date.year - a.start_date.year) * 12
                + a.end_date.month
                - a.start_date.month
            )
            duration_b = (
                (b.end_date.year - b.start_date.year) * 12
                + b.end_date.month
                - b.start_date.month
            )
            if (
                (a.granularity, a.fiscal_year_end_month, duration_a)
                != (b.granularity, b.fiscal_year_end_month, duration_b)
                or a.comparison_basis != "current"
                or b.comparison_basis != "comparative"
                or a.fiscal_year is not None
                and b.fiscal_year != a.fiscal_year - 1
                or a.fiscal_year is None
                and b.end_date.year != a.end_date.year - 1
                or (a.start_date.month, a.end_date.month)
                != (b.start_date.month, b.end_date.month)
            ):
                raise IncompatibleInputError(
                    "growth periods are not structurally comparable"
                )
        elif any(item.period.identity != first.period.identity for item in inputs):
            raise IncompatibleInputError("period mismatch")
        monetary = [item for item in inputs if item.unit == "monetary"]
        if monetary and any(
            (item.currency, item.unit) != (monetary[0].currency, "monetary")
            for item in monetary
        ):
            raise IncompatibleInputError("unit/currency mismatch")
        if (
            definition.operation in {"percent", "ratio", "growth", "days"}
            and inputs[1].value == 0
        ):
            raise ZeroDenominatorError("denominator is zero")

    @staticmethod
    def _validate_comparison_periods(rule, inputs):
        current, prior = (i.period for i in inputs)
        if rule == "opening_stock":
            if (
                current.comparison_basis != "current"
                or prior.comparison_basis != "current"
                or current.fiscal_year_end_month != prior.fiscal_year_end_month
                or (current.fiscal_year is None) != (prior.fiscal_year is None)
                or prior.end_date.toordinal() + 1 != current.start_date.toordinal()
            ):
                raise IncompatibleInputError(
                    "opening stock must immediately precede the current interval in the same fiscal calendar"
                )
            return
        if (
            current.comparison_basis != "current"
            or prior.comparison_basis not in {"current", "comparative"}
            or current.granularity != prior.granularity
            or current.fiscal_year_end_month != prior.fiscal_year_end_month
            or (current.fiscal_year is None) != (prior.fiscal_year is None)
        ):
            raise IncompatibleInputError("comparison calendar or basis differs")
        if rule == "previous_month":
            adjacent = (
                current.start_date.year * 12
                + current.start_date.month
                - prior.start_date.year * 12
                - prior.start_date.month
                == 1
            )
            valid = current.granularity == "month" and adjacent
        else:
            valid = (
                current.granularity in {"month", "ytd"}
                and current.start_date.year == prior.start_date.year + 1
                and current.end_date.year == prior.end_date.year + 1
                and current.start_date.month == prior.start_date.month
                and current.end_date.month == prior.end_date.month
                and (
                    current.fiscal_year is None
                    or current.fiscal_year == prior.fiscal_year + 1
                )
            )
        if not valid:
            raise IncompatibleInputError(
                "comparison periods do not satisfy the declared rule"
            )

    @staticmethod
    def _precision(operation: str, values: tuple[Decimal, ...]) -> int:
        if operation in {"percent", "ratio", "growth", "days", "mix"}:
            return 100
        # Exact add/subtract/multiply needs every integer and fractional place,
        # including gaps such as 1E+1000 - 1.
        left = max((v.adjusted() for v in values), default=0)
        right = min((v.as_tuple().exponent for v in values), default=0)
        return max(100, left - right + 3)

    @staticmethod
    def _apply(operation: str, v: tuple[Decimal, ...]) -> Decimal:
        if operation == "sum":
            return sum(v, Decimal(0))
        if operation == "identity":
            return v[0]
        if operation in {"subtract", "growth_delta"}:
            return v[0] - v[1]
        if operation == "add":
            return v[0] + v[1]
        if operation == "percent":
            return v[0] / v[1] * Decimal(100)
        if operation == "ratio":
            return v[0] / v[1]
        if operation == "growth":
            return (v[0] - v[1]) / abs(v[1]) * Decimal(100)
        if operation == "days":
            return v[0] / v[1] * v[2]
        if operation == "add_subtract":
            return v[0] + v[1] - v[2]
        if operation == "mix":
            denominator = v[0] + v[1]
            if denominator == 0:
                raise ZeroDenominatorError("mix denominator is zero")
            return v[0] / denominator * Decimal(100)
        raise MetricCalculationError(f"unsupported operation {operation}")
