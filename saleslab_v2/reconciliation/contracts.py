"""Immutable, presentation-independent reconciliation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping
from uuid import UUID

from saleslab_v2.domain.normalization import (
    canonical_decimal,
    canonical_dimensions,
    require_finite_decimal,
)
from saleslab_v2.metrics.trace import canonical_json


class ReconciliationError(ValueError):
    """Base error for an execution that cannot safely be reconciled."""


class InputContractError(ReconciliationError):
    pass


class PolicyRequiredError(ReconciliationError):
    pass


class RelativeDeltaKind(str, Enum):
    DEFINED = "defined"
    ZERO_EXPECTED = "undefined_zero_expected"


class TemporalRole(str, Enum):
    SAME_PERIOD = "same_period"
    STOCK_OPENING = "stock_opening"
    FLOW_INTERVAL = "flow_interval"
    STOCK_CLOSING = "stock_closing"


@dataclass(frozen=True)
class OperandRequirement:
    role: str
    identities: tuple[str, ...]
    variants: tuple[str | None, ...] = (None,)
    required: bool = True
    statuses: tuple[str, ...] = ("normalized", "calculated")
    units: tuple[str, ...] = ("monetary",)
    currency_applicable: bool = True
    scale_applicable: bool = True
    scopes: tuple[str, ...] = ("consolidated", "segment")
    temporal_role: TemporalRole = TemporalRole.SAME_PERIOD


@dataclass(frozen=True)
class Equation:
    expected_terms: tuple[tuple[str, int], ...]
    observed_role: str
    equation_id: str = "equation"


@dataclass(frozen=True)
class CapabilityEffect:
    capability_id: str
    effect_version: str
    blocked: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityEvidence:
    """Minimal, F-04-only proof for the two causal capability gates."""

    evidence_id: UUID
    capability_id: str
    claim_type: str
    evidence_references: tuple[str, ...]
    status: str
    workspace_id: UUID
    run_id: UUID
    period: tuple[object, ...]
    dimensions: tuple[tuple[str, str], ...]
    fingerprint: str = ""

    def __post_init__(self):
        if self.capability_id not in {
            "call_growth_organic",
            "attribute_leasing_to_expansion",
        }:
            raise InputContractError("unsupported capability evidence")
        expected_claim = {
            "call_growth_organic": "drivers_support_organic",
            "attribute_leasing_to_expansion": "lease_additions_due_to_expansion",
        }[self.capability_id]
        if self.claim_type != expected_claim:
            raise InputContractError("evidence does not directly support the claim")
        if self.status not in {"confirmed", "warning", "critical", "blocking"}:
            raise InputContractError("unsupported capability evidence status")
        if not self.evidence_references or any(
            not value.strip() for value in self.evidence_references
        ):
            raise InputContractError("explicit evidence references are required")
        object.__setattr__(self, "dimensions", canonical_dimensions(self.dimensions))
        expected = self.semantic_fingerprint()
        if self.fingerprint and self.fingerprint != expected:
            raise InputContractError("invalid capability evidence fingerprint")
        object.__setattr__(self, "fingerprint", expected)

    def payload(self, *, include_fingerprint: bool = True) -> dict:
        payload = {
            "evidence_id": str(self.evidence_id),
            "capability_id": self.capability_id,
            "claim_type": self.claim_type,
            "evidence_references": tuple(sorted(self.evidence_references)),
            "status": self.status,
            "workspace_id": str(self.workspace_id),
            "run_id": str(self.run_id),
            "period": self.period,
            "dimensions": self.dimensions,
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def semantic_fingerprint(self) -> str:
        return sha256(
            canonical_json(self.payload(include_fingerprint=False)).encode()
        ).hexdigest()


@dataclass(frozen=True)
class ReconciliationEquationResult:
    equation_id: str
    expected: Decimal
    observed: Decimal
    signed_delta: Decimal
    absolute_delta: Decimal
    relative_delta: Decimal | None
    relative_delta_kind: RelativeDeltaKind
    status: str
    severity: str
    reason_codes: tuple[str, ...]

    def payload(self) -> dict:
        return {
            **self.__dict__,
            "expected": canonical_decimal(self.expected),
            "observed": canonical_decimal(self.observed),
            "signed_delta": canonical_decimal(self.signed_delta),
            "absolute_delta": canonical_decimal(self.absolute_delta),
            "relative_delta": (
                canonical_decimal(self.relative_delta)
                if self.relative_delta is not None
                else None
            ),
            "relative_delta_kind": self.relative_delta_kind.value,
        }


@dataclass(frozen=True)
class ReconciliationRule:
    rule_id: str
    version: str
    description: str
    operands: tuple[OperandRequirement, ...]
    equations: tuple[Equation, ...]
    capability_id: str | None = None
    capability_effect_version: str | None = None

    def __post_init__(self):
        roles = tuple(item.role for item in self.operands)
        if not self.rule_id.startswith("REC-") or not self.version:
            raise ValueError("stable rule ID and version are required")
        if len(roles) != len(set(roles)) or not self.equations:
            raise ValueError("rule roles must be unique and equations non-empty")
        used = {r for eq in self.equations for r, _ in eq.expected_terms} | {
            eq.observed_role for eq in self.equations
        }
        if not used <= set(roles):
            raise ValueError("equations may only use declared operands")
        optional = {item.role for item in self.operands if not item.required}
        if used & optional:
            raise ValueError("equations cannot reference optional operands")


@dataclass(frozen=True)
class ReconciliationInput:
    role: str
    observation_id: UUID
    identity: str
    value: Decimal
    unit: str
    currency: str | None
    scale: str
    period: tuple[object, ...]
    scope: str
    dimensions: tuple[tuple[str, str], ...]
    status: str
    source_references: tuple[str, ...]
    provenance: tuple[tuple[str, str], ...]
    variant: str | None = None
    calculation_trace_id: UUID | None = None
    calculation_trace_hash: str | None = None

    def __post_init__(self):
        require_finite_decimal(self.value, "value")
        if self.status not in {"normalized", "confirmed", "calculated"}:
            raise InputContractError(
                "inputs must be normalized, confirmed, or calculated"
            )
        if not self.identity or not self.source_references:
            raise InputContractError(
                "input identity and source references are required"
            )
        if self.scope not in {"consolidated", "segment", "eliminations"}:
            raise InputContractError("unsupported input scope")
        object.__setattr__(self, "dimensions", canonical_dimensions(self.dimensions))
        if (
            self.calculation_trace_hash is not None
            and len(self.calculation_trace_hash) != 64
        ):
            raise InputContractError("calculation trace hash must be SHA-256 hex")

    def payload(self) -> dict:
        return {
            "role": self.role,
            "observation_id": str(self.observation_id),
            "identity": self.identity,
            "value": canonical_decimal(self.value),
            "unit": self.unit,
            "currency": self.currency,
            "scale": self.scale,
            "period": self.period,
            "scope": self.scope,
            "dimensions": self.dimensions,
            "status": self.status,
            "variant": self.variant,
            "source_references": sorted(self.source_references),
            "provenance": self.provenance,
            "calculation_trace_id": (
                str(self.calculation_trace_id) if self.calculation_trace_id else None
            ),
            "calculation_trace_hash": self.calculation_trace_hash,
        }

    @classmethod
    def from_normalized_observation(cls, role, observation_id, observation):
        """Build an input without erasing the canonical F-02 audit status."""
        if (
            observation.normalization_status != "normalized"
            or observation.value is None
            or observation.concept_id is None
            or observation.period is None
            or observation.semantic_type
            in {
                "reported_unverified",
                "reported_operating_profit_unconfirmed",
            }
            or observation.issues
        ):
            raise InputContractError("F-02 observation is not reconciliation-ready")
        return cls(
            role=role,
            observation_id=observation_id,
            identity=observation.concept_id,
            value=observation.value,
            unit=observation.unit,
            currency=observation.currency,
            scale=observation.scale,
            period=observation.period.identity,
            scope=observation.scope,
            dimensions=observation.dimensions,
            status=observation.normalization_status,
            source_references=(observation.source_reference,),
            provenance=(("normalization_hash", observation.semantic_hash),),
        )


@dataclass(frozen=True)
class TolerancePolicy:
    policy_id: str
    version: str
    unit: str
    currency: str | None
    scale: str
    rounding_absolute: Decimal
    warning_absolute: Decimal
    critical_absolute: Decimal
    warning_relative: Decimal
    critical_relative: Decimal
    materiality_basis: str

    def __post_init__(self):
        values = (
            self.rounding_absolute,
            self.warning_absolute,
            self.critical_absolute,
            self.warning_relative,
            self.critical_relative,
        )
        for value in values:
            require_finite_decimal(value, "tolerance")
            if value < 0:
                raise ValueError("tolerances cannot be negative")
        if (
            not self.rounding_absolute
            <= self.warning_absolute
            <= self.critical_absolute
        ):
            raise ValueError("absolute tolerance limits must be monotonic")
        if not self.warning_relative <= self.critical_relative:
            raise ValueError("relative tolerance limits must be monotonic")
        if not all(
            (
                self.policy_id,
                self.version,
                self.unit,
                self.scale,
                self.materiality_basis,
            )
        ):
            raise ValueError(
                "policy identity, applicability, and materiality are explicit"
            )

    @property
    def fingerprint(self) -> str:
        return sha256(canonical_json(self.payload()).encode()).hexdigest()

    def payload(self) -> dict:
        return {
            name: canonical_decimal(value) if isinstance(value, Decimal) else value
            for name, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class ReconciliationResult:
    rule_id: str
    rule_version: str
    policy_id: str
    policy_version: str
    policy_payload: dict
    policy_fingerprint: str
    input_references: tuple[dict, ...]
    input_fingerprint: str
    equations: tuple[ReconciliationEquationResult, ...]
    expected: Decimal
    observed: Decimal
    signed_delta: Decimal
    absolute_delta: Decimal
    relative_delta: Decimal | None
    relative_delta_kind: RelativeDeltaKind
    unit: str
    currency: str | None
    period: tuple[object, ...]
    scope: str
    dimensions: tuple[tuple[str, str], ...]
    variant: str | None
    status: str
    severity: str
    reason_codes: tuple[str, ...]
    capability_effects: tuple[CapabilityEffect, ...]
    capability_evidence: tuple[CapabilityEvidence, ...]
    source_references: tuple[str, ...]
    metric_references: tuple[str, ...]
    calculation_trace_references: tuple[str, ...]
    engine_version: str
    result_hash: str
    created_at: datetime

    def payload(self, *, include_hash: bool = True) -> dict:
        payload = dict(self.__dict__)
        payload["expected"] = canonical_decimal(self.expected)
        payload["observed"] = canonical_decimal(self.observed)
        payload["signed_delta"] = canonical_decimal(self.signed_delta)
        payload["absolute_delta"] = canonical_decimal(self.absolute_delta)
        payload["relative_delta"] = (
            canonical_decimal(self.relative_delta)
            if self.relative_delta is not None
            else None
        )
        payload["relative_delta_kind"] = self.relative_delta_kind.value
        payload["equations"] = tuple(item.payload() for item in self.equations)
        payload["capability_effects"] = tuple(
            item.__dict__ for item in self.capability_effects
        )
        payload["capability_evidence"] = tuple(
            item.payload() for item in self.capability_evidence
        )
        # Operational metadata is deliberately outside semantic identity/replay.
        payload.pop("created_at")
        if not include_hash:
            payload.pop("result_hash")
        return payload


class ReconciliationRegistry:
    def __init__(self, rules: Iterable[ReconciliationRule]):
        values = tuple(rules)
        mapping = {(r.rule_id, r.version): r for r in values}
        if len(mapping) != len(values):
            raise ValueError("duplicate reconciliation rule/version")
        self._rules: Mapping[tuple[str, str], ReconciliationRule] = MappingProxyType(
            mapping
        )

    def get(self, rule_id: str, version: str = "1") -> ReconciliationRule:
        try:
            return self._rules[(rule_id, version)]
        except KeyError as exc:
            raise KeyError(f"unknown reconciliation rule {rule_id}@{version}") from exc

    @property
    def rules(self) -> tuple[ReconciliationRule, ...]:
        return tuple(self._rules.values())
