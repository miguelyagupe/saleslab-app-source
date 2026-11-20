from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class OperandContract:
    """Semantic contract for one operand role (not merely its dict key)."""

    role: str
    metric_ids: tuple[str, ...]
    unit_family: str
    normalization_statuses: tuple[str, ...] = ("confirmed", "calculated")
    variants: tuple[str | None, ...] = (None,)
    required_dimensions: tuple[tuple[str, str], ...] = ()
    value_constraint: str = "any"

    def __post_init__(self):
        if not self.metric_ids:
            raise ValueError("operand metric IDs are required")
        if self.unit_family not in {"monetary", "percentage", "ratio", "days"}:
            raise ValueError("unsupported operand unit family")
        if self.value_constraint not in {
            "any",
            "non_positive",
            "non_negative",
            "positive",
        }:
            raise ValueError("unsupported operand value constraint")


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    version: str
    semantics: str
    canonical_formula: str
    inputs: tuple[str, ...]
    operation: str
    unit_family: str
    aggregation_rule: str = "same_scope_only"
    scope_rules: tuple[str, ...] = ("consolidated", "segment")
    allowed_modules: tuple[str, ...] = ("financial",)
    source_priority: tuple[str, ...] = ("normalized", "calculated")
    rounding_policy: str | None = None
    operand_contracts: tuple[OperandContract, ...] = ()
    period_rule: str = "standard"

    def __post_init__(self):
        if self.period_rule not in {
            "standard",
            "previous_month",
            "prior_year",
            "opening_stock",
        }:
            raise ValueError("unsupported metric period rule")
        if self.period_rule == "opening_stock" and self.operation != "subtract":
            raise ValueError("opening stock changes require subtraction")
        if self.period_rule != "standard" and (
            self.operation not in {"subtract", "growth"} or len(self.inputs) != 2
        ):
            raise ValueError("period comparisons require two subtract/growth operands")
        if not self.metric_id or not self.version or not self.canonical_formula:
            raise ValueError("metric identity, version, and formula are required")
        if not self.inputs or len(set(self.inputs)) != len(self.inputs):
            raise ValueError("metric inputs must be a non-empty unique tuple")
        if self.unit_family not in {"monetary", "percentage", "ratio", "days"}:
            raise ValueError("unsupported output unit family")
        if not self.operand_contracts:
            object.__setattr__(
                self,
                "operand_contracts",
                tuple(
                    OperandContract(role, (role,), "monetary") for role in self.inputs
                ),
            )
        if tuple(item.role for item in self.operand_contracts) != self.inputs:
            raise ValueError("operand contracts must match input roles in order")


class MetricRegistry:
    """Immutable registry keyed by semantic metric identity and version."""

    def __init__(self, definitions: Iterable[MetricDefinition]):
        values = tuple(definitions)
        mapping = {(item.metric_id, item.version): item for item in values}
        if len(mapping) != len(values):
            raise ValueError("duplicate metric definition/version")
        self._definitions: Mapping[tuple[str, str], MetricDefinition] = (
            MappingProxyType(mapping)
        )
        self._validate_dependencies()

    def _validate_dependencies(self) -> None:
        calculated = {key[0] for key in self._definitions}
        graph = {
            definition.metric_id: tuple(i for i in definition.inputs if i in calculated)
            for definition in self._definitions.values()
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError(f"metric dependency cycle at {node}")
            if node in visited:
                return
            visiting.add(node)
            for child in graph.get(node, ()):
                visit(child)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)

    def get(self, metric_id: str, version: str = "1") -> MetricDefinition:
        try:
            return self._definitions[(metric_id, version)]
        except KeyError as exc:
            raise KeyError(f"unknown metric definition {metric_id}@{version}") from exc

    @property
    def definitions(self) -> tuple[MetricDefinition, ...]:
        return tuple(self._definitions.values())
