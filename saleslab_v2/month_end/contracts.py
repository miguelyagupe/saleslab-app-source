"""Explicit monthly policy and evidence bindings; no inferred materiality thresholds."""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from uuid import UUID

from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.domain.normalization import (
    canonical_decimal,
    canonical_dimensions,
    require_finite_decimal,
)
from saleslab_v2.domain.value_objects import Money, Period
from saleslab_v2.normalization.currencies import ISO_4217_CODES
from saleslab_v2.pipeline.contracts import InputRef
from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1

MONTH_END_VERSION = "financial-month-end-v1"


class InvalidMonthEnd(ValueError):
    pass


def identifier(value):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 100
    ):
        raise ValueError("explicit bounded identifier required")


def unique(values):
    if not values or len(values) > 50 or len(values) != len(set(values)):
        raise ValueError("nonempty unique bounded policy requirements required")
    for value in values:
        identifier(value)


@dataclass(frozen=True)
class ClosePolicy:
    policy_id: str
    version: str
    currency: str
    scope: str
    dimensions: tuple[tuple[str, str], ...]
    fiscal_year_start_month: int
    required_statements: tuple[str, ...]
    required_reconciliations: tuple[str, ...]
    required_tasks: tuple[str, ...]
    required_comparisons: tuple[str, ...]
    absolute_variation_threshold: Decimal
    percentage_variation_threshold: Decimal
    materiality_basis: str

    def __post_init__(self):
        for value in (self.policy_id, self.version, self.materiality_basis):
            identifier(value)
        Money(Decimal(0), self.currency)
        if self.currency not in ISO_4217_CODES:
            raise ValueError("close policy currency must be supported by normalization")
        object.__setattr__(self, "dimensions", canonical_dimensions(self.dimensions))
        if (
            self.scope not in {"consolidated", "segment"}
            or self.scope == "segment"
            and not self.dimensions
        ):
            raise ValueError("explicit supported close scope required")
        if (
            type(self.fiscal_year_start_month) is not int
            or not 1 <= self.fiscal_year_start_month <= 12
        ):
            raise ValueError("explicit fiscal start month required")
        unique(self.required_statements)
        unique(self.required_reconciliations)
        unique(self.required_tasks)
        unique(self.required_comparisons)
        if not set(self.required_comparisons) <= {
            "month_on_month",
            "month_year_on_year",
            "ytd_year_on_year",
        }:
            raise ValueError("unsupported required comparison")
        if not set(self.required_statements) <= {
            "income_statement",
            "balance_sheet",
            "cash_flow_statement",
        }:
            raise ValueError("unsupported required statement")
        for rule in self.required_reconciliations:
            FINANCIAL_RULES_V1.get(rule)
        for value in (
            self.absolute_variation_threshold,
            self.percentage_variation_threshold,
        ):
            require_finite_decimal(value, "materiality threshold")
            if value <= 0 or len(canonical_decimal(value)) > 256:
                raise ValueError("positive bounded materiality threshold required")


@dataclass(frozen=True)
class ComparisonPlan:
    key: str
    current: InputRef
    prior: InputRef
    relation: str

    def __post_init__(self):
        identifier(self.key)
        if self.key.startswith("required:"):
            raise ValueError("required: is reserved for generated checklist entries")
        if self.relation not in {
            "month_on_month",
            "month_year_on_year",
            "ytd_year_on_year",
        }:
            raise ValueError("unsupported close comparison")
        for ref in (self.current, self.prior):
            if not isinstance(ref, InputRef) or not isinstance(ref.source_id, UUID):
                raise TypeError("close comparisons require original source rows")


@dataclass(frozen=True)
class MovementExpectation:
    key: str
    current: InputRef
    kind: str
    prior: InputRef | None = None

    def __post_init__(self):
        identifier(self.key)
        if self.kind not in {"nonzero_value", "changed_from_prior_month"}:
            raise ValueError("explicit movement expectation required")
        if self.kind == "nonzero_value" and self.prior is not None:
            raise ValueError("nonzero expectation does not compare balances")
        ComparisonPlan(
            self.key, self.current, self.prior or self.current, "month_on_month"
        )
        if self.kind == "changed_from_prior_month" and self.prior is None:
            raise ValueError("movement comparison requires prior evidence")


@dataclass(frozen=True)
class TaskConfirmation:
    task_id: str
    status: str
    evidence: tuple[InputRef, ...]

    def __post_init__(self):
        identifier(self.task_id)
        if self.status not in {"complete", "incomplete"}:
            raise ValueError("invalid task confirmation")
        if len(self.evidence) > 20 or self.status == "complete" and not self.evidence:
            raise ValueError("completed tasks require bounded source evidence")
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError("duplicate task evidence")
        if any(
            not isinstance(r, InputRef) or not isinstance(r.source_id, UUID)
            for r in self.evidence
        ):
            raise ValueError("tasks require original source references")


@dataclass(frozen=True)
class MonthEndRequest:
    workspace_id: UUID
    user_id: UUID
    pipeline_run_id: UUID
    period: Period
    policy: ClosePolicy
    comparisons: tuple[ComparisonPlan, ...]
    movements: tuple[MovementExpectation, ...] = ()
    confirmations: tuple[TaskConfirmation, ...] = ()

    def __post_init__(self):
        if any(
            not isinstance(v, UUID)
            for v in (self.workspace_id, self.user_id, self.pipeline_run_id)
        ):
            raise TypeError("workspace, actor and run must be UUIDs")
        if (
            self.period.granularity != "month"
            or self.period.comparison_basis != "current"
        ):
            raise ValueError("close target must be one current month")
        for values, field in (
            (self.comparisons, "key"),
            (self.movements, "key"),
            (self.confirmations, "task_id"),
        ):
            if len(values) > 50 or len({getattr(v, field) for v in values}) != len(
                values
            ):
                raise ValueError("duplicate or excessive close bindings")
        if any(c.task_id not in self.policy.required_tasks for c in self.confirmations):
            raise ValueError("confirmation is outside the declared checklist")

    def snapshot(self):
        value = deepcopy(self)
        return replace(
            value,
            period=replace(value.period),
            policy=replace(value.policy),
            comparisons=tuple(replace(c) for c in value.comparisons),
            movements=tuple(replace(m) for m in value.movements),
            confirmations=tuple(replace(c) for c in value.confirmations),
        )

    def payload(self):
        return canonicalize(asdict(self))


@dataclass(frozen=True)
class MonthEndResult:
    run_id: UUID
    payload: dict
    replayed: bool
