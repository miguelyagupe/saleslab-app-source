"""Deterministic reconciliation engine with no implicit materiality choices."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256

from saleslab_v2.domain.value_objects import Period
from saleslab_v2.metrics.trace import canonical_json

from .contracts import (
    CapabilityEffect,
    CapabilityEvidence,
    InputContractError,
    PolicyRequiredError,
    ReconciliationEquationResult,
    ReconciliationInput,
    ReconciliationResult,
    ReconciliationRule,
    RelativeDeltaKind,
    TemporalRole,
    TolerancePolicy,
)

ENGINE_VERSION = "reconciliation-engine-v2"
_RANK = {"pass": 0, "rounding_pass": 1, "warning": 2, "critical": 3, "blocking": 4}
_REASON = {
    "pass": "EXACT_MATCH",
    "rounding_pass": "WITHIN_ROUNDING",
    "warning": "OUTSIDE_ROUNDING",
    "critical": "MATERIAL_VARIANCE",
    "blocking": "UNACCEPTABLE_VARIANCE",
}


class ReconciliationEngine:
    def evaluate(
        self,
        rule: ReconciliationRule,
        inputs: tuple[ReconciliationInput, ...],
        policy: TolerancePolicy | None,
        *,
        timestamp: datetime,
        capability_evidence: tuple[CapabilityEvidence, ...] = (),
        workspace_id=None,
        run_id=None,
    ) -> ReconciliationResult:
        if policy is None:
            raise PolicyRequiredError("an explicit tolerance policy is required")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        by_role: dict[str, ReconciliationInput] = {}
        requirements = {operand.role: operand for operand in rule.operands}
        for item in inputs:
            if item.role not in requirements:
                raise InputContractError(f"unknown input role: {item.role}")
            if item.role in by_role:
                raise InputContractError(f"duplicate input role: {item.role}")
            by_role[item.role] = item
        missing = [
            o.role for o in rule.operands if o.required and o.role not in by_role
        ]
        if missing:
            raise InputContractError(f"missing required inputs: {', '.join(missing)}")
        ordered = tuple(by_role[o.role] for o in rule.operands if o.role in by_role)
        for operand in rule.operands:
            item = by_role.get(operand.role)
            if item is None:
                continue
            if item.identity not in operand.identities:
                raise InputContractError(f"identity mismatch for {operand.role}")
            if item.variant not in operand.variants:
                raise InputContractError(f"variant mismatch for {operand.role}")
            if item.status not in operand.statuses:
                raise InputContractError(f"status mismatch for {operand.role}")
            if item.unit not in operand.units:
                raise InputContractError(f"unit mismatch for {operand.role}")
            if item.scope not in operand.scopes:
                raise InputContractError(f"scope mismatch for {operand.role}")
            if operand.currency_applicable != (item.currency is not None):
                raise InputContractError(
                    f"currency applicability mismatch for {operand.role}"
                )
            if not operand.scale_applicable and item.scale not in {"unit", "none"}:
                raise InputContractError(
                    f"scale applicability mismatch for {operand.role}"
                )
        first = ordered[0]
        result_period = next(
            (
                by_role[operand.role].period
                for operand in rule.operands
                if operand.temporal_role is TemporalRole.FLOW_INTERVAL
                and operand.role in by_role
            ),
            first.period,
        )
        if any(
            (i.unit, i.currency, i.scale, i.scope, i.dimensions)
            != (first.unit, first.currency, first.scale, first.scope, first.dimensions)
            for i in ordered[1:]
        ):
            raise InputContractError(
                "unit/currency/scale/scope/dimensions are incompatible"
            )
        self._validate_periods(rule, by_role)
        if (policy.unit, policy.currency, policy.scale) != (
            first.unit,
            first.currency,
            first.scale,
        ):
            raise PolicyRequiredError(
                "tolerance policy is incompatible with input unit/currency/scale"
            )

        equations = []
        with localcontext() as context:
            context.prec, context.rounding = 100, ROUND_HALF_EVEN
            for equation in rule.equations:
                expected = sum(
                    (by_role[r].value * c for r, c in equation.expected_terms),
                    Decimal(0),
                )
                observed = by_role[equation.observed_role].value
                signed, absolute = observed - expected, abs(observed - expected)
                relative, kind = (
                    (None, RelativeDeltaKind.ZERO_EXPECTED)
                    if expected == 0
                    else (abs(signed / expected), RelativeDeltaKind.DEFINED)
                )
                status = self._status(absolute, relative, policy)
                equations.append(
                    ReconciliationEquationResult(
                        equation.equation_id,
                        expected,
                        observed,
                        signed,
                        absolute,
                        relative,
                        kind,
                        status,
                        status,
                        (_REASON[status],),
                    )
                )
        equation_results = tuple(equations)
        worst = max(
            equation_results,
            key=lambda e: (_RANK[e.status], e.absolute_delta, e.equation_id),
        )
        refs = tuple(item.payload() for item in ordered)
        fingerprint = sha256(canonical_json(refs).encode()).hexdigest()
        applicable_evidence = tuple(
            evidence
            for evidence in capability_evidence
            if evidence.capability_id == rule.capability_id
        )
        if capability_evidence and not rule.capability_id:
            raise InputContractError("rule does not accept capability evidence")
        if (
            len(applicable_evidence) != len(capability_evidence)
            or len(applicable_evidence) > 1
        ):
            raise InputContractError("capability evidence does not match the rule")
        for evidence in applicable_evidence:
            if workspace_id is None or run_id is None:
                raise InputContractError(
                    "evidence requires explicit workspace/run scope"
                )
            if (evidence.workspace_id, evidence.run_id) != (workspace_id, run_id):
                raise InputContractError("capability evidence is outside workspace/run")
            if (
                evidence.period != result_period
                or evidence.dimensions != first.dimensions
            ):
                raise InputContractError("capability evidence has incompatible scope")
        bridge_ok = worst.status in {"pass", "rounding_pass"}
        causal_proof = bool(
            applicable_evidence and applicable_evidence[0].status == "confirmed"
        )
        blocked = bool(rule.capability_id and not (bridge_ok and causal_proof))
        effects = (
            ()
            if not rule.capability_id
            else (
                CapabilityEffect(
                    rule.capability_id,
                    rule.capability_effect_version or "1",
                    blocked,
                    (
                        ("EXPLICIT_CAUSAL_EVIDENCE_CONFIRMED",)
                        if not blocked
                        else (
                            ("BRIDGE_NOT_RECONCILED",)
                            if not bridge_ok
                            else ("EXPLICIT_CAUSAL_EVIDENCE_REQUIRED",)
                        )
                    ),
                ),
            )
        )
        result = ReconciliationResult(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            policy_payload=policy.payload(),
            policy_fingerprint=policy.fingerprint,
            input_references=refs,
            input_fingerprint=fingerprint,
            equations=equation_results,
            expected=worst.expected,
            observed=worst.observed,
            signed_delta=worst.signed_delta,
            absolute_delta=worst.absolute_delta,
            relative_delta=worst.relative_delta,
            relative_delta_kind=worst.relative_delta_kind,
            unit=first.unit,
            currency=first.currency,
            period=result_period,
            scope=first.scope,
            dimensions=first.dimensions,
            variant=(
                first.variant if len({i.variant for i in ordered}) == 1 else "bridge"
            ),
            status=worst.status,
            severity=worst.severity,
            reason_codes=tuple(
                code for e in equation_results for code in e.reason_codes
            ),
            capability_effects=effects,
            capability_evidence=applicable_evidence,
            source_references=tuple(
                sorted({r for i in ordered for r in i.source_references})
            ),
            metric_references=tuple(i.identity for i in ordered),
            calculation_trace_references=tuple(
                sorted(
                    {
                        str(i.calculation_trace_id)
                        for i in ordered
                        if i.calculation_trace_id
                    }
                )
            ),
            engine_version=ENGINE_VERSION,
            result_hash="",
            created_at=timestamp.astimezone(timezone.utc),
        )
        return replace(result, result_hash=self.semantic_hash(result))

    @staticmethod
    def semantic_hash(result: ReconciliationResult) -> str:
        return sha256(
            canonical_json(result.payload(include_hash=False)).encode()
        ).hexdigest()

    @staticmethod
    def input_fingerprint(result: ReconciliationResult) -> str:
        return sha256(canonical_json(result.input_references).encode()).hexdigest()

    @staticmethod
    def _validate_periods(rule, by_role):
        periods = [
            (o, by_role[o.role].period) for o in rule.operands if o.role in by_role
        ]
        flows = [p for o, p in periods if o.temporal_role is TemporalRole.FLOW_INTERVAL]
        if flows:
            interval = flows[0]
            if any(p != interval for p in flows[1:]):
                raise InputContractError(
                    "roll-forward movements must share one fiscal interval"
                )
            ReconciliationEngine._validate_period_identity(interval)
            start, end = date.fromisoformat(str(interval[0])), date.fromisoformat(
                str(interval[1])
            )
            adjacent_stocks = (
                rule.rule_id in {"REC-DEBT-001", "REC-LEASE-001"}
                and rule.version == "2"
            ) or (rule.rule_id == "REC-WC-CHANGE-001" and rule.version == "1")
            for operand, period in periods:
                ReconciliationEngine._validate_period_identity(period)
                if (
                    (
                        period[2] != interval[2]
                        and not (
                            adjacent_stocks
                            and operand.temporal_role
                            in {TemporalRole.STOCK_OPENING, TemporalRole.STOCK_CLOSING}
                        )
                    )
                    or period[4] != interval[4]
                    or period[5] != interval[5]
                ):
                    raise InputContractError(
                        "roll-forward period identity is incompatible"
                    )
                if operand.temporal_role is TemporalRole.STOCK_OPENING and (
                    date.fromisoformat(str(period[1])).toordinal() + 1
                    != start.toordinal()
                    if adjacent_stocks
                    else date.fromisoformat(str(period[1])) + timedelta(days=1) != start
                ):
                    raise InputContractError(
                        "opening stock must precede interval start"
                    )
                if (
                    operand.temporal_role is TemporalRole.STOCK_CLOSING
                    and date.fromisoformat(str(period[1])) != end
                ):
                    raise InputContractError("closing stock must match interval end")
                fiscal_year = period[3]
                if interval[3] is not None and not (
                    adjacent_stocks
                    and operand.temporal_role is TemporalRole.STOCK_OPENING
                ):
                    expected_year = (
                        interval[3] - 1
                        if operand.temporal_role is TemporalRole.STOCK_OPENING
                        else interval[3]
                    )
                    if fiscal_year != expected_year:
                        raise InputContractError(
                            "roll-forward fiscal year is incompatible"
                        )
        else:
            for _, period in periods:
                ReconciliationEngine._validate_period_identity(period)
            if any(p != periods[0][1] for _, p in periods[1:]):
                raise InputContractError(
                    "same-period operands have incompatible periods"
                )

    @staticmethod
    def _validate_period_identity(period):
        if not isinstance(period, tuple) or len(period) != 6:
            raise InputContractError("period must use the canonical F-02 identity")
        start, end, granularity, fiscal_year, basis, fiscal_end = period
        try:
            start_date, end_date = date.fromisoformat(str(start)), date.fromisoformat(
                str(end)
            )
        except ValueError as exc:
            raise InputContractError("period dates are invalid") from exc
        try:
            Period(
                start_date,
                end_date,
                granularity=granularity,
                fiscal_year=fiscal_year,
                comparison_basis=basis,
                fiscal_year_end_month=fiscal_end,
            )
        except (TypeError, ValueError) as exc:
            raise InputContractError("period structured identity is invalid") from exc

    @staticmethod
    def _status(absolute, relative, policy):
        if absolute == 0:
            return "pass"
        if absolute <= policy.rounding_absolute:
            return "rounding_pass"
        if absolute <= policy.warning_absolute or (
            relative is not None and relative <= policy.warning_relative
        ):
            return "warning"
        if absolute <= policy.critical_absolute or (
            relative is not None and relative <= policy.critical_relative
        ):
            return "critical"
        return "blocking"
