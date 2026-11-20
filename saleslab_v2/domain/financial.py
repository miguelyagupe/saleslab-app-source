"""Canonical, persistence-independent financial normalization contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID

from .currencies import MONETARY_ISO_4217_CODES
from .enums import NullReason
from .normalization import (
    canonical_decimal,
    canonical_dimensions,
    exact_ratio_matches,
    require_finite_decimal,
)
from .value_objects import Period


@dataclass(frozen=True)
class NormalizationIssue:
    code: str
    field: str
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if not isinstance(self.code, str) or not isinstance(self.field, str):
            raise TypeError("issue code and field must be strings")
        if not self.code.strip() or not self.field.strip():
            raise ValueError("issue code and field must not be empty")
        canonical = canonical_dimensions(self.details)
        object.__setattr__(self, "details", canonical)


@dataclass(frozen=True)
class NormalizedObservation:
    concept_id: str | None
    semantic_type: str | None
    value: Decimal | None
    null_reason: NullReason | None
    currency: str | None
    scale: str | None
    unit: str | None
    numerator: Decimal | None = field(default=None, kw_only=True)
    denominator: Decimal | None = field(default=None, kw_only=True)
    sign: str | None
    expected_sign: str | None
    period: Period | None
    statement_type: str
    scope: str
    original_name: str
    original_text: str
    source_document_id: UUID
    source_content_hash: str
    source_reference: str
    source_language: str
    format_locale: str
    confidence: Decimal
    normalization_status: str = "normalized"
    issues: tuple[NormalizationIssue, ...] = ()
    parser_version: str = "financial-normalizer-v1"
    terminology_version: str = "financial-terminology-v1"
    dimensions: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.value is not None:
            require_finite_decimal(self.value, "value")
        require_finite_decimal(self.confidence, "confidence")
        for field_name, item in (
            ("numerator", self.numerator),
            ("denominator", self.denominator),
        ):
            if item is not None:
                require_finite_decimal(item, field_name)
        if (self.numerator is None) != (self.denominator is None):
            raise ValueError("ratio operands must be supplied together")
        if (self.value is None) == (self.null_reason is None):
            raise ValueError("exactly one of value and null_reason is required")
        if self.null_reason is not None and not isinstance(
            self.null_reason, NullReason
        ):
            raise TypeError("null_reason must use the approved enum")
        if not isinstance(self.source_document_id, UUID):
            raise TypeError("source_document_id must be UUID")
        if not Decimal(0) <= self.confidence <= Decimal(1):
            raise ValueError("confidence must be Decimal between 0 and 1")
        if len(self.source_content_hash) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.source_content_hash
        ):
            raise ValueError("source_content_hash must be SHA-256 hex")
        if (
            not self.source_reference.strip()
            or self.source_reference != self.source_reference.strip()
        ):
            raise ValueError("source_reference must be a non-empty canonical reference")
        if self.source_language not in {"pt", "en", "es", "fr"}:
            raise ValueError("source_language must use the approved allowlist")
        if self.format_locale not in {"pt-PT", "en-GB", "es-ES", "fr-FR"}:
            raise ValueError("format_locale must use the approved allowlist")
        if self.currency is not None and self.currency not in MONETARY_ISO_4217_CODES:
            raise ValueError("currency must be an approved monetary ISO code")
        if self.scale not in {None, "raw", "thousands", "millions"}:
            raise ValueError("invalid scale")
        if self.unit not in {None, "monetary", "percentage"}:
            raise ValueError("unit must be monetary, percentage, or null")
        if (
            self.numerator is not None or self.denominator is not None
        ) and self.unit != "percentage":
            raise ValueError("ratio operands require the percentage unit")
        object.__setattr__(self, "dimensions", canonical_dimensions(self.dimensions))
        if not self.parser_version.strip() or not self.terminology_version.strip():
            raise ValueError("component versions must not be empty")
        if self.normalization_status not in {"normalized", "needs_confirmation"}:
            raise ValueError("invalid normalization_status")
        blocking = bool(self.issues)
        if self.normalization_status == "normalized" and blocking:
            raise ValueError("normalized observations cannot contain blocking issues")
        if self.normalization_status == "needs_confirmation" and not self.issues:
            raise ValueError("needs_confirmation requires a structured issue")
        if self.value is None:
            if any(
                item is not None
                for item in (
                    self.currency,
                    self.scale,
                    self.unit,
                    self.numerator,
                    self.denominator,
                    self.sign,
                )
            ):
                raise ValueError("null observations cannot carry quantitative metadata")
        if self.normalization_status == "normalized":
            if not self.concept_id or not self.semantic_type or self.period is None:
                raise ValueError(
                    "normalized observations require concept, semantic type, and period"
                )
            if (
                self.statement_type
                not in {
                    "income_statement",
                    "balance_sheet",
                    "cash_flow_statement",
                }
                and not (
                    self.terminology_version
                    in {
                        "financial-terminology-v5",
                        "financial-terminology-v6",
                        "financial-terminology-v7",
                        "financial-terminology-v8",
                        "financial-terminology-v9",
                        "financial-terminology-v10",
                    }
                    and self.statement_type
                    in {"debt_schedule", "lease_schedule", "working_capital_detail"}
                )
                and not (
                    self.terminology_version
                    in {
                        "financial-terminology-v8",
                        "financial-terminology-v9",
                        "financial-terminology-v10",
                    }
                    and self.statement_type == "capex_schedule"
                )
            ) and not (
                self.terminology_version
                in {"financial-terminology-v9", "financial-terminology-v10"}
                and self.statement_type == "roic_schedule"
            ):
                raise ValueError("invalid statement_type")
            if self.scope not in {"consolidated", "segment", "eliminations"}:
                raise ValueError("invalid scope")
            if self.scope == "segment" and not self.dimensions:
                raise ValueError("normalized segment observations require dimensions")
            if self.unit == "monetary" and (
                self.currency is None or self.scale is None
            ):
                raise ValueError(
                    "normalized monetary values require currency and scale"
                )
        if (
            self.unit == "percentage"
            and self.normalization_status == "normalized"
            and not self.ratio_complete
        ):
            raise ValueError(
                "normalized percentages require complete consistent operands"
            )

    @property
    def ratio_complete(self) -> bool:
        """Whether percentage evidence contains a valid, consistent ratio base."""
        return bool(
            self.unit == "percentage"
            and self.value is not None
            and self.numerator is not None
            and self.denominator not in (None, Decimal(0))
            and exact_ratio_matches(self.value, self.numerator, self.denominator)
        )

    @property
    def semantic_hash(self) -> str:
        """Hash canonical economics, not localized presentation labels."""
        payload: dict[str, Any] = {
            "concept_id": self.concept_id,
            "semantic_type": self.semantic_type,
            "value": canonical_decimal(self.value) if self.value is not None else None,
            "null_reason": self.null_reason.value if self.null_reason else None,
            "currency": self.currency,
            "scale": self.scale,
            "unit": self.unit,
            "numerator": (
                canonical_decimal(self.numerator)
                if self.numerator is not None
                else None
            ),
            "denominator": (
                canonical_decimal(self.denominator)
                if self.denominator is not None
                else None
            ),
            "sign": self.sign,
            "expected_sign": self.expected_sign,
            "period": (
                {
                    "start": self.period.start_date.isoformat(),
                    "end": self.period.end_date.isoformat(),
                    "granularity": self.period.granularity,
                    "fiscal_year": self.period.fiscal_year,
                    "comparison_basis": self.period.comparison_basis,
                    "fiscal_year_end_month": self.period.fiscal_year_end_month,
                }
                if self.period
                else None
            ),
            "statement_type": self.statement_type,
            "scope": self.scope,
            "dimensions": list(self.dimensions),
            "source_document_id": str(self.source_document_id),
            "source_content_hash": self.source_content_hash,
            "source_reference": self.source_reference,
            "issues": [
                {"code": issue.code, "field": issue.field, "details": issue.details}
                for issue in sorted(
                    self.issues, key=lambda item: (item.code, item.field, item.details)
                )
            ],
            "parser_version": self.parser_version,
            "terminology_version": self.terminology_version,
            "normalization_status": self.normalization_status,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode()).hexdigest()

    @property
    def economic_identity_hash(self) -> str:
        """Identity of the economics only, deliberately excluding provenance/issues."""
        payload = {
            "concept_id": self.concept_id,
            "semantic_type": self.semantic_type,
            "value": canonical_decimal(self.value) if self.value is not None else None,
            "null_reason": self.null_reason.value if self.null_reason else None,
            "currency": self.currency,
            "unit": self.unit,
            "numerator": (
                canonical_decimal(self.numerator)
                if self.numerator is not None
                else None
            ),
            "denominator": (
                canonical_decimal(self.denominator)
                if self.denominator is not None
                else None
            ),
            "sign": self.sign,
            "expected_sign": self.expected_sign,
            "period": self.period.identity if self.period else None,
            "statement_type": self.statement_type,
            "scope": self.scope,
            "dimensions": self.dimensions,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
