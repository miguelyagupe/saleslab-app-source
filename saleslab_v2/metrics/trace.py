from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID, uuid5

from saleslab_v2.domain.normalization import canonical_decimal
from saleslab_v2.domain.idempotency import canonicalize

TRACE_NAMESPACE = UUID("76cff229-57d4-4faa-b595-6432564e8fd7")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class TraceInput:
    role: str
    observation_id: UUID
    metric_id: str
    value: object
    unit: str
    currency: str | None
    period: tuple[object, ...]
    scope: str
    dimensions: tuple[tuple[str, str], ...]
    source_references: tuple[str, ...]
    provenance: tuple[tuple[str, str], ...] = ()
    normalization_status: str = "confirmed"
    variant: str | None = None

    def payload(self) -> dict:
        return {
            "role": self.role,
            "observation_id": str(self.observation_id),
            "metric_id": self.metric_id,
            "value": canonical_decimal(self.value),
            "unit": self.unit,
            "currency": self.currency,
            "period": canonicalize(self.period),
            "scope": self.scope,
            "dimensions": canonicalize(self.dimensions),
            "source_references": sorted(self.source_references),
            "provenance": canonicalize(self.provenance),
            "normalization_status": self.normalization_status,
            "variant": self.variant,
        }


@dataclass(frozen=True)
class CalculationTrace:
    metric_id: str
    metric_definition_version: str
    canonical_expression: str
    inputs: tuple[TraceInput, ...]
    exact_output: object
    rounded_output: object | None
    rounding_policy: str | None
    rounding_version: str | None
    engine_version: str
    output_unit: str | None = None
    output_currency: str | None = None
    period: tuple[object, ...] | None = None
    scope: str | None = None
    dimensions: tuple[tuple[str, str], ...] = ()
    division_policy: str | None = None

    @property
    def payload(self) -> dict:
        return {
            "metric_id": self.metric_id,
            "metric_definition_version": self.metric_definition_version,
            "canonical_expression": self.canonical_expression,
            "inputs": [item.payload() for item in self.inputs],
            "exact_output": canonical_decimal(self.exact_output),
            "rounded_output": (
                canonical_decimal(self.rounded_output)
                if self.rounded_output is not None
                else None
            ),
            "rounding_policy": self.rounding_policy,
            "rounding_version": self.rounding_version,
            "engine_version": self.engine_version,
            "output_unit": self.output_unit,
            "output_currency": self.output_currency,
            "period": canonicalize(self.period),
            "scope": self.scope,
            "dimensions": canonicalize(self.dimensions),
            "division_policy": self.division_policy,
        }

    @property
    def trace_hash(self) -> str:
        return sha256(canonical_json(self.payload).encode()).hexdigest()

    @property
    def trace_id(self) -> UUID:
        return uuid5(TRACE_NAMESPACE, self.trace_hash)
