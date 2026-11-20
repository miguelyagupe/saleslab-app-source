"""Explicit versioned comparison cases; no inferred acceptance thresholds."""

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from saleslab_v2.domain.idempotency import canonicalize, semantic_hash
from saleslab_v2.domain.normalization import canonical_decimal, require_finite_decimal
from saleslab_v2.pipeline.contracts import FinancialRequest, InputRef

SHADOW_VERSION = "financial-shadow-v1"


class InvalidShadow(ValueError):
    pass


class LegacyUnavailable(RuntimeError):
    pass


class LegacyRunner(Protocol):
    @property
    def configuration(self) -> dict: ...
    async def generate(self, shared_input: dict) -> dict: ...


@dataclass(frozen=True)
class MetricExpectation:
    step_key: str
    value: Decimal
    unit: str
    currency: str | None
    legacy_metric_id: str | None = None
    legacy_absolute_tolerance: Decimal | None = None

    def __post_init__(self):
        require_finite_decimal(self.value, "expected value")
        if len(canonical_decimal(self.value)) > 256:
            raise ValueError("expected metric exceeds numeric bound")
        if not self.step_key or not self.unit:
            raise ValueError("metric step and unit are required")
        if (self.legacy_metric_id is None) != (self.legacy_absolute_tolerance is None):
            raise ValueError("legacy comparison needs an explicit absolute tolerance")
        if self.legacy_absolute_tolerance is not None:
            require_finite_decimal(self.legacy_absolute_tolerance, "legacy tolerance")
            if len(canonical_decimal(self.legacy_absolute_tolerance)) > 256:
                raise ValueError("comparison tolerance exceeds numeric bound")
            if self.legacy_absolute_tolerance < 0:
                raise ValueError("negative comparison tolerance")

    def payload(self):
        return {
            "step_key": self.step_key,
            "value": canonical_decimal(self.value),
            "unit": self.unit,
            "currency": self.currency,
            "legacy_metric_id": self.legacy_metric_id,
            "legacy_absolute_tolerance": (
                canonical_decimal(self.legacy_absolute_tolerance)
                if self.legacy_absolute_tolerance is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ShadowCase:
    case_id: str
    dataset_version: str
    request: FinancialRequest
    legacy_bindings: tuple[tuple[str, InputRef], ...]
    expected_v2_status: str
    expected_legacy_status: str
    metrics: tuple[MetricExpectation, ...]
    required_capabilities: tuple[tuple[str, str], ...]
    expected_issue_codes: tuple[str, ...] = ()
    expected_audit_status: str | None = None

    def __post_init__(self):
        for value in (self.case_id, self.dataset_version):
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 100
            ):
                raise ValueError(
                    "case ID and dataset version must be explicit bounded strings"
                )
        if self.expected_v2_status not in {
            "completed",
            "blocked",
            "mapping_required",
            "analyzing",
            "auditing",
        }:
            raise ValueError("invalid expected V2 state")
        if self.expected_legacy_status not in {"completed", "failed"}:
            raise ValueError("invalid expected legacy state")
        if self.expected_audit_status not in {
            None,
            "approved",
            "approved_with_warnings",
            "revision_required",
            "blocked",
        }:
            raise ValueError("invalid expected audit status")
        if len(self.metrics) > 50 or len({m.step_key for m in self.metrics}) != len(
            self.metrics
        ):
            raise ValueError("duplicate or excessive metric assertions")
        names = [name for name, _ in self.legacy_bindings]
        if len(set(names)) != len(names) or not set(names) <= {
            "revenue_current",
            "revenue_previous",
            "cogs",
            "opex",
            "net_result",
        }:
            raise ValueError("duplicate or unsupported legacy form fields")
        if not self.required_capabilities or len(
            dict(self.required_capabilities)
        ) != len(self.required_capabilities):
            raise ValueError("explicit unique capability assertions are required")
        from saleslab_v2.domain.enums import Capability

        if any(
            cap not in {c.value for c in Capability}
            or state not in {"allowed", "conditional", "blocked"}
            for cap, state in self.required_capabilities
        ):
            raise ValueError("invalid expected capability decision")
        if self.expected_v2_status == "completed" and not self.metrics:
            raise ValueError(
                "successful golden cases require at least one exact metric assertion"
            )
        semantic_hash(self.payload())

    def payload(self):
        return {
            "case_id": self.case_id,
            "dataset_version": self.dataset_version,
            "request": canonicalize(self.request.semantic_payload()),
            "format_locale": self.request.language.format_locale.value,
            "legacy_bindings": [
                {
                    "field": n,
                    "source_id": str(r.source_id) if r.source_id else None,
                    "key": r.key,
                }
                for n, r in sorted(self.legacy_bindings)
            ],
            "expected_v2_status": self.expected_v2_status,
            "expected_legacy_status": self.expected_legacy_status,
            "metrics": [
                m.payload() for m in sorted(self.metrics, key=lambda m: m.step_key)
            ],
            "required_capabilities": dict(sorted(self.required_capabilities)),
            "expected_issue_codes": sorted(set(self.expected_issue_codes)),
            "expected_audit_status": self.expected_audit_status,
        }

    def snapshot(self):
        return deepcopy(self)


@dataclass(frozen=True)
class ShadowResult:
    run_id: UUID
    payload: dict
    replayed: bool
