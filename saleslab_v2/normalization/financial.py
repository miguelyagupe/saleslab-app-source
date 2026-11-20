"""Orchestrates deterministic row normalization without calculations or I/O."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.domain.financial import NormalizationIssue, NormalizedObservation
from saleslab_v2.domain.language import LocaleTag
from saleslab_v2.domain.normalization import (
    canonical_dimensions,
    exact_power_of_ten,
    exact_ratio_matches,
    require_finite_decimal,
)
from saleslab_v2.i18n.catalog import TerminologyRegistry
from saleslab_v2.i18n.financial_v2 import FinancialTerminologyV2
from saleslab_v2.i18n.financial_v3 import FinancialTerminologyV3
from saleslab_v2.i18n.financial_v4 import FinancialTerminologyV4
from saleslab_v2.i18n.financial_v5 import FinancialTerminologyV5
from saleslab_v2.i18n.financial_v6 import FinancialTerminologyV6
from saleslab_v2.i18n.financial_v7 import DEBT_LABELS, FinancialTerminologyV7
from saleslab_v2.i18n.financial_v8 import (
    CAPEX_LABELS,
    CASH_CAPEX_CONCEPTS,
    COMMITMENT,
    FinancialTerminologyV8,
)
from saleslab_v2.i18n.financial_v9 import FinancialTerminologyV9
from saleslab_v2.i18n.financial_v10 import FinancialTerminologyV10

from .currencies import ISO_4217_CODES
from .exact_numbers import ExactLocaleNumberParser
from .mapping import SemanticMapper
from .numbers import LocaleNumberParser
from .periods import PeriodNormalizer

_SCALE_EXPONENT = {"raw": 0, "thousands": 3, "millions": 6}
_VALID_STATEMENTS = {"income_statement", "balance_sheet", "cash_flow_statement"}
_VALID_SCOPES = {"consolidated", "segment", "eliminations"}


@dataclass(frozen=True)
class SourceMetadata:
    source_document_id: UUID
    content_hash: str
    source_language: LanguageCode
    format_locale: LocaleTag
    currency: str | None = None
    scale: str | None = None
    accounting_parentheses: bool | None = None
    normalization_version: str = "1"
    terminology_version: str = "1"
    roic_binding: str | None = None

    def __post_init__(self):
        if not isinstance(self.terminology_version, str):
            raise ValueError("unsupported financial terminology version")
        if (
            self.terminology_version in {"9", "10"}
            and self.normalization_version != "2"
        ):
            raise ValueError("ROIC terminology requires exact normalization version 2")
        if self.roic_binding is not None and (
            not isinstance(self.roic_binding, str)
            or len(self.roic_binding.encode()) > 64_000
            or self.terminology_version not in {"9", "10"}
        ):
            raise ValueError("bounded ROIC evidence requires terminology version 9")
        if not isinstance(
            self.normalization_version, str
        ) or self.normalization_version not in {"1", "2"}:
            raise ValueError("unsupported financial normalization version")
        if not isinstance(
            self.terminology_version, str
        ) or self.terminology_version not in {
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
        }:
            raise ValueError("unsupported financial terminology version")
        if not isinstance(self.source_document_id, UUID):
            raise TypeError("source_document_id must be UUID")
        if len(self.content_hash) != 64 or any(
            c not in "0123456789abcdef" for c in self.content_hash
        ):
            raise ValueError("content_hash must be lowercase SHA-256 hex")
        if not isinstance(self.source_language, LanguageCode):
            raise TypeError(
                "source_language must use the approved LanguageCode allowlist"
            )
        if not isinstance(self.format_locale, LocaleTag):
            raise TypeError("format_locale must use the approved LocaleTag allowlist")
        if self.currency is not None and self.currency not in ISO_4217_CODES:
            raise ValueError("source currency must be an approved monetary ISO code")
        if self.scale is not None and self.scale not in _SCALE_EXPONENT:
            raise ValueError("source scale must be canonical")
        if self.accounting_parentheses is not None and not isinstance(
            self.accounting_parentheses, bool
        ):
            raise TypeError("accounting_parentheses must be bool or None")


@dataclass(frozen=True)
class FinancialRow:
    name: str
    text: str
    period_label: str
    statement_type: str
    scope: str
    currency: str | None = None
    scale: str | None = None
    fiscal_year_end_month: int | None = None
    comparison_basis: str = "current"
    dimensions: tuple[tuple[str, str], ...] = ()
    definition_quote: str | None = field(default=None, kw_only=True)
    numerator: Decimal | None = None
    denominator: Decimal | None = None
    source_reference: str = field(kw_only=True)

    def __post_init__(self):
        if self.definition_quote is not None and (
            not isinstance(self.definition_quote, str)
            or not 10 <= len(self.definition_quote) <= 2000
            or self.definition_quote != self.definition_quote.strip()
        ):
            raise ValueError("growth definition quote must be explicit and bounded")
        for field_name, item in (
            ("numerator", self.numerator),
            ("denominator", self.denominator),
        ):
            if item is not None:
                require_finite_decimal(item, field_name)
        if (self.numerator is None) != (self.denominator is None):
            raise ValueError("ratio operands must be supplied together")
        if (
            not self.source_reference.strip()
            or self.source_reference != self.source_reference.strip()
        ):
            raise ValueError("source_reference must be a non-empty canonical reference")
        for field_name in ("name", "period_label", "statement_type", "scope"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(f"{field_name} must be a non-empty trimmed string")
        if self.currency is not None and self.currency not in ISO_4217_CODES:
            raise ValueError("row currency must be an approved monetary ISO code")
        if self.scale is not None and self.scale not in _SCALE_EXPONENT:
            raise ValueError("row scale must be canonical")
        object.__setattr__(self, "dimensions", canonical_dimensions(self.dimensions))


class FinancialNormalizer:
    version = "financial-normalizer-v1"

    def __init__(self):
        self.number_parser = LocaleNumberParser()
        self.exact_number_parser = ExactLocaleNumberParser()
        self.period_normalizer = PeriodNormalizer()
        self.registry = TerminologyRegistry()
        self.mapper = SemanticMapper(self.registry)
        self.registry_v2 = FinancialTerminologyV2()
        self.mapper_v2 = SemanticMapper(self.registry_v2)
        self.registry_v3 = FinancialTerminologyV3()
        self.mapper_v3 = SemanticMapper(self.registry_v3)
        self.registry_v4 = FinancialTerminologyV4()
        self.mapper_v4 = SemanticMapper(self.registry_v4)
        self.registry_v5 = FinancialTerminologyV5()
        self.mapper_v5 = SemanticMapper(self.registry_v5)
        self.registry_v10 = FinancialTerminologyV10()
        self.mapper_v10 = SemanticMapper(self.registry_v10)
        self.registry_v9 = FinancialTerminologyV9()
        self.mapper_v9 = SemanticMapper(self.registry_v9)
        self.registry_v8 = FinancialTerminologyV8()
        self.mapper_v8 = SemanticMapper(self.registry_v8)
        self.registry_v7 = FinancialTerminologyV7()
        self.mapper_v7 = SemanticMapper(self.registry_v7)
        self.registry_v6 = FinancialTerminologyV6()
        self.mapper_v6 = SemanticMapper(self.registry_v6)

    def normalize(
        self, rows: list[FinancialRow], source: SourceMetadata
    ) -> list[NormalizedObservation]:
        observations = [self._normalize_row(row, source) for row in rows]
        if source.terminology_version in {"6", "7", "8", "9", "10"}:
            from saleslab_v2.i18n.financial_v6 import GROWTH_LABELS

            grouped = {}
            for index, (row, observation) in enumerate(zip(rows, observations)):
                if observation.concept_id in GROWTH_LABELS:
                    key = (
                        observation.concept_id
                        if observation.concept_id.startswith("revenue_")
                        else "monetary_bridge"
                    )
                    grouped.setdefault(key, []).append((index, row.definition_quote))
            for values in grouped.values():
                if len({quote for _, quote in values if quote}) > 1:
                    for index, _ in values:
                        o = observations[index]
                        observations[index] = replace(
                            o,
                            normalization_status="needs_confirmation",
                            issues=o.issues
                            + (
                                NormalizationIssue(
                                    "growth.definition_mismatch", "original_name"
                                ),
                            ),
                        )
        if source.terminology_version in {"7", "8", "9", "10"}:
            debt = [
                (i, r.definition_quote)
                for i, (r, o) in enumerate(zip(rows, observations))
                if o.concept_id in DEBT_LABELS
            ]
            mismatch = len({q for _, q in debt if q}) > 1
            for i, quote in debt:
                o = observations[i]
                errors = []
                if quote is None:
                    errors.append(
                        NormalizationIssue("debt.definition_required", "original_name")
                    )
                if mismatch:
                    errors.append(
                        NormalizationIssue("debt.definition_mismatch", "original_name")
                    )
                if o.value is not None and o.value < 0:
                    errors.append(
                        NormalizationIssue("debt.negative_principal", "value")
                    )
                if errors:
                    observations[i] = replace(
                        o,
                        normalization_status="needs_confirmation",
                        issues=o.issues + tuple(errors),
                    )
        if source.terminology_version in {"8", "9", "10"}:
            has_cash = any(o.concept_id in CASH_CAPEX_CONCEPTS for o in observations)
            required = set(CAPEX_LABELS) | ({"capex"} if has_cash else set())
            groups = {}
            for i, (r, o) in enumerate(zip(rows, observations)):
                if o.concept_id in required:
                    groups.setdefault(
                        "commitment" if o.concept_id == COMMITMENT else "cash", []
                    ).append((i, r.definition_quote))
            for values in groups.values():
                mismatch = len({q for _, q in values if q}) > 1
                for i, quote in values:
                    o = observations[i]
                    errors = []
                    if quote is None:
                        errors.append(
                            NormalizationIssue(
                                "capex.definition_required", "original_name"
                            )
                        )
                    if mismatch:
                        errors.append(
                            NormalizationIssue(
                                "capex.definition_mismatch", "original_name"
                            )
                        )
                    if (
                        o.concept_id in CAPEX_LABELS
                        and o.value is not None
                        and o.value < 0
                    ):
                        errors.append(
                            NormalizationIssue("capex.negative_magnitude", "value")
                        )
                    if errors:
                        observations[i] = replace(
                            o,
                            normalization_status="needs_confirmation",
                            issues=o.issues + tuple(errors),
                        )
        candidates: dict[tuple[object, ...], list[int]] = {}
        for index, observation in enumerate(observations):
            identity = _candidate_identity(observation)
            if identity is not None:
                candidates.setdefault(identity, []).append(index)
        for identity, indexes in candidates.items():
            if len(indexes) < 2:
                continue
            identity_hash = sha256(
                json.dumps(identity, separators=(",", ":")).encode()
            ).hexdigest()
            issue = NormalizationIssue(
                "observation.duplicate_candidate",
                "observation",
                (("candidate_count", str(len(indexes))), ("identity", identity_hash)),
            )
            for index in indexes:
                candidate = observations[index]
                observations[index] = replace(
                    candidate,
                    confidence=min(candidate.confidence, Decimal("0.5")),
                    normalization_status="needs_confirmation",
                    issues=candidate.issues + (issue,),
                )
        if source.terminology_version in {"9", "10"}:
            from .roic import validate_roic_observations

            observations = validate_roic_observations(rows, source, observations)
        if source.terminology_version == "10":
            from .working_capital import validate_working_capital

            observations = validate_working_capital(rows, observations)
        return observations

    def _normalize_row(
        self, row: FinancialRow, source: SourceMetadata
    ) -> NormalizedObservation:
        if (
            row.statement_type not in _VALID_STATEMENTS
            and not (
                source.terminology_version in {"9", "10"}
                and row.statement_type == "roic_schedule"
            )
            and not (
                source.terminology_version in {"5", "6", "7", "8", "9", "10"}
                and row.statement_type
                in {"debt_schedule", "lease_schedule", "working_capital_detail"}
            )
            and not (
                source.terminology_version in {"8", "9", "10"}
                and row.statement_type == "capex_schedule"
            )
        ):
            raise ValueError("unsupported statement_type")
        if (
            row.statement_type == "roic_schedule"
            and source.terminology_version not in {"9", "10"}
        ):
            raise ValueError("unsupported statement_type")
        registry, mapper = {
            "1": (self.registry, self.mapper),
            "2": (self.registry_v2, self.mapper_v2),
            "3": (self.registry_v3, self.mapper_v3),
            "4": (self.registry_v4, self.mapper_v4),
            "5": (self.registry_v5, self.mapper_v5),
            "6": (self.registry_v6, self.mapper_v6),
            "7": (self.registry_v7, self.mapper_v7),
            "8": (self.registry_v8, self.mapper_v8),
            "9": (self.registry_v9, self.mapper_v9),
            "10": (self.registry_v10, self.mapper_v10),
        }[source.terminology_version]
        mapping = mapper.map(
            row.name, language=source.source_language, statement_type=row.statement_type
        )
        entry = mapping.entry
        declared_currency_values = [
            item for item in (row.currency, source.currency) if item
        ]
        invalid_declared_currency = any(
            item not in ISO_4217_CODES for item in declared_currency_values
        )
        declared_currencies = (
            set(declared_currency_values) if not invalid_declared_currency else set()
        )
        currency_hint = (
            next(iter(declared_currencies)) if len(declared_currencies) == 1 else None
        )
        number_parser = (
            self.number_parser
            if source.normalization_version == "1"
            else self.exact_number_parser
        )
        number = number_parser.parse(
            row.text,
            locale=source.format_locale,
            accounting_parentheses=source.accounting_parentheses,
            currency_hint=currency_hint,
        )
        period = self.period_normalizer.normalize(
            row.period_label,
            fiscal_year_end_month=row.fiscal_year_end_month,
            comparison_basis=row.comparison_basis,
        )
        issues = list(mapping.issues + number.issues + period.issues)
        if source.terminology_version in {"6", "7", "8", "9", "10"}:
            from saleslab_v2.i18n.financial_v6 import GROWTH_LABELS

            if (
                entry is not None
                and entry.concept_id in GROWTH_LABELS
                and row.definition_quote is None
            ):
                issues.append(
                    NormalizationIssue("growth.definition_required", "original_name")
                )
        numerator = row.numerator if number.is_percentage else None
        denominator = row.denominator if number.is_percentage else None
        ratio_complete = False
        if number.is_percentage:
            if numerator is None:
                issues.append(NormalizationIssue("ratio.operands_required", "value"))
            elif denominator == 0:
                issues.append(
                    NormalizationIssue("ratio.denominator_zero", "denominator")
                )
            elif number.value is not None and not exact_ratio_matches(
                number.value, numerator, denominator
            ):
                issues.append(NormalizationIssue("ratio.value_conflict", "value"))
            else:
                ratio_complete = True
        elif row.numerator is not None:
            issues.append(NormalizationIssue("ratio.unit_conflict", "unit"))
        if invalid_declared_currency:
            issues.append(NormalizationIssue("currency.invalid", "currency"))
        currency = None
        scale = None
        if number.value is not None and not number.is_percentage:
            text_currency_unresolved = any(
                issue.code
                in {
                    "currency.conflict",
                    "currency.ambiguous_symbol",
                    "currency.unknown",
                }
                for issue in number.issues
            )
            currencies = {
                item
                for item in (number.currency, row.currency, source.currency)
                if item
                and len(item) == 3
                and item.isascii()
                and item.isalpha()
                and item.isupper()
                and item in ISO_4217_CODES
            }
            if invalid_declared_currency or text_currency_unresolved:
                currencies = set()
            if text_currency_unresolved:
                pass
            elif len(currencies) > 1:
                issues.append(NormalizationIssue("currency.conflict", "currency"))
            elif currencies:
                currency = next(iter(currencies))
            else:
                issues.append(NormalizationIssue("currency.missing", "currency"))
            scales = {item for item in (number.scale, row.scale, source.scale) if item}
            if len(scales) > 1:
                issues.append(NormalizationIssue("scale.conflict", "scale"))
            elif scales:
                scale = next(iter(scales))
            else:
                issues.append(NormalizationIssue("scale.missing", "scale"))
        elif number.is_percentage and number.currency is not None:
            issues.append(
                NormalizationIssue("currency.percentage_conflict", "currency")
            )
        if (
            number.is_percentage
            and entry is not None
            and entry.semantic_type
            in {
                "monetary_flow",
                "monetary_stock",
                "reported_operating_profit_unconfirmed",
                "reported_unverified",
            }
        ):
            issues.append(
                NormalizationIssue("unit.percentage_monetary_conflict", "unit")
            )
        if scale is not None and scale not in _SCALE_EXPONENT:
            issues.append(NormalizationIssue("scale.unknown", "scale"))
            scale = None
        scope = row.scope
        if scope not in _VALID_SCOPES:
            issues.append(NormalizationIssue("scope.unknown", "scope"))
            scope = "unresolved"
        if scope == "segment" and not row.dimensions:
            issues.append(
                NormalizationIssue("scope.segment_dimension_required", "scope")
            )
        value = number.value
        if value is not None and scale:
            value = exact_power_of_ten(value, _SCALE_EXPONENT[scale])
        confidence = mapping.confidence
        if issues:
            confidence = min(confidence, Decimal("0.5"))
        expected_sign = entry.expected_sign if entry else None
        if expected_sign == "outflow" and number.sign == "positive":
            issues.append(
                NormalizationIssue("sign.convention_confirmation_required", "sign")
            )
            confidence = min(confidence, Decimal("0.5"))
        required_unresolved = any(
            (
                entry is None,
                period.period is None,
                number.value is not None
                and not number.is_percentage
                and currency is None,
                number.value is not None and not number.is_percentage and scale is None,
                scope == "unresolved",
                scope == "segment" and not row.dimensions,
                number.is_percentage
                and entry is not None
                and entry.semantic_type.startswith(
                    (
                        "monetary_",
                        "reported_operating_profit",
                        "reported_unverified",
                    )
                ),
                number.is_percentage and not ratio_complete,
            )
        )
        return NormalizedObservation(
            concept_id=entry.concept_id if entry else None,
            semantic_type=entry.semantic_type if entry else None,
            value=value,
            null_reason=number.null_reason,
            currency=currency,
            scale=scale,
            unit=(
                "percentage"
                if number.value is not None and number.is_percentage
                else (
                    "monetary"
                    if number.value is not None
                    and entry is not None
                    and entry.semantic_type.startswith(
                        (
                            "monetary_",
                            "reported_operating_profit",
                            "reported_unverified",
                        )
                    )
                    else None
                )
            ),
            numerator=numerator,
            denominator=denominator,
            sign=number.sign,
            expected_sign=expected_sign,
            period=period.period,
            statement_type=row.statement_type,
            scope=scope,
            original_name=row.name,
            original_text=row.text,
            source_document_id=source.source_document_id,
            source_content_hash=source.content_hash,
            source_reference=row.source_reference,
            source_language=source.source_language.value,
            format_locale=source.format_locale.value,
            confidence=confidence,
            normalization_status=(
                "needs_confirmation" if required_unresolved or issues else "normalized"
            ),
            issues=tuple(issues),
            parser_version=(
                self.version
                if source.normalization_version == "1"
                else "financial-normalizer-v2"
            ),
            terminology_version=registry.version,
            dimensions=row.dimensions,
        )


def validate_aggregation_scope(observations: list[NormalizedObservation]) -> None:
    """Reject unsafe aggregation; F-02 deliberately performs no aggregation."""
    scopes = {observation.scope for observation in observations}
    if "consolidated" in scopes and ("segment" in scopes or "eliminations" in scopes):
        raise ValueError("scope.consolidated_segment_aggregation_forbidden")
    segments = [item for item in observations if item.scope == "segment"]
    eliminations = [item for item in observations if item.scope == "eliminations"]
    for segment in segments:
        if not any(
            _aggregation_identity(elimination) == _aggregation_identity(segment)
            for elimination in eliminations
        ):
            raise ValueError("scope.segment_aggregation_requires_matching_eliminations")
    if any(item.normalization_status != "normalized" for item in observations):
        raise ValueError("scope.aggregation_requires_normalized_observations")


def _period_identity(observation: NormalizedObservation) -> tuple[object, ...] | None:
    period = observation.period
    if period is None:
        return None
    return (
        period.start_date.isoformat(),
        period.end_date.isoformat(),
        period.granularity,
        period.fiscal_year,
        period.comparison_basis,
        period.fiscal_year_end_month,
    )


def _candidate_identity(
    observation: NormalizedObservation,
) -> tuple[object, ...] | None:
    """Identify competing canonical candidates, excluding provenance and value."""
    period = _period_identity(observation)
    if observation.concept_id is None or period is None:
        return None
    return (
        observation.concept_id,
        observation.semantic_type,
        period,
        observation.statement_type,
        observation.scope,
        observation.currency,
        observation.unit,
        observation.dimensions,
    )


def _aggregation_identity(observation: NormalizedObservation) -> tuple[object, ...]:
    """Return the exact identity an elimination must match; this performs no sum."""
    return (
        observation.concept_id,
        observation.semantic_type,
        _period_identity(observation),
        observation.statement_type,
        observation.currency,
        observation.unit,
        observation.scale,
        observation.dimensions,
    )
