"""Deterministic monthly preparation, independently scoped comparisons and missing data."""

from dataclasses import asdict
from datetime import date

from saleslab_v2.analyst.context import _normalized, unpack
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.domain.normalization import canonical_decimal
from saleslab_v2.metrics.engine import (
    MetricCalculationError,
    MetricEngine,
    ZeroDenominatorError,
)
from saleslab_v2.pipeline.adapters import metric_input, normalized_id, scope_metric

from .contracts import MONTH_END_VERSION
from .i18n import label
from .metrics import MONTH_END_METRICS, metric_id, percentage_meets_threshold


class MissingEvidence(ValueError):
    pass


def evaluate(request, normalization, checkpoint, *, run_id, language):
    policy, target = request.policy, request.period
    observations = unpack(normalization["observations"])
    quality = unpack(checkpoint["quality"])
    rows = {(r["source_document_id"], r["source_reference"]): r for r in observations}
    calculate_allowed = any(
        d["capability"] == "calculate" and d["decision_status"] == "allowed"
        for d in quality["decisions"]
    )
    checklist, comparisons, calculations = [], [], []

    def item(category, key, status, reason, evidence=()):
        checklist.append(
            {
                "item_id": category + "/" + key,
                "category": category,
                "title": label(category, language) + ": " + key,
                "status": status,
                "reason_code": reason,
                "evidence": list(evidence),
            }
        )
        return checklist[-1]

    def in_scope(row):
        return (
            row["scope"] == policy.scope
            and row["dimensions"] == [list(d) for d in policy.dimensions]
            and row["currency"] == policy.currency
        )

    def monthly(row):
        period = row.get("period") or {}
        return (
            period.get("start_date") == target.start_date.isoformat()
            and period.get("end_date") == target.end_date.isoformat()
            and period.get("granularity") == "month"
            and period.get("comparison_basis") == "current"
        )

    def valid(row):
        return (
            row["normalization_status"] == "normalized"
            and row["value"] is not None
            and not row["issues"]
        )

    def reference(row):
        return {
            "source_id": row["source_document_id"],
            "location": row["source_reference"],
            "content_hash": row["source_content_hash"],
        }

    def resolve(ref, *, current_month=False):
        raw = rows.get((str(ref.source_id), ref.key))
        if raw is None:
            raise MissingEvidence("month_end.source_missing")
        if not valid(raw) or not in_scope(raw) or raw["unit"] != "monetary":
            raise MissingEvidence("month_end.source_incompatible")
        if current_month and not monthly(raw):
            raise MissingEvidence("month_end.target_month_mismatch")
        duplicates = [
            r
            for r in observations
            if valid(r)
            and (
                r["concept_id"],
                r["period"],
                r["scope"],
                r["dimensions"],
                r["currency"],
                r["statement_type"],
            )
            == (
                raw["concept_id"],
                raw["period"],
                raw["scope"],
                raw["dimensions"],
                raw["currency"],
                raw["statement_type"],
            )
        ]
        if len(duplicates) != 1:
            raise MissingEvidence("month_end.source_ambiguous")
        return _normalized(raw), reference(raw)

    for statement in sorted(policy.required_statements):
        present = [
            r
            for r in observations
            if valid(r)
            and in_scope(r)
            and monthly(r)
            and r["statement_type"] == statement
        ]
        item(
            "statement",
            statement,
            "satisfied" if present else "pending",
            "month_end.statement_present" if present else "month_end.statement_missing",
            [reference(r) for r in present],
        )

    for issue in sorted(quality["issues"], key=lambda i: i["issue_id"]):
        status = (
            "pending"
            if issue["severity"] in {"critical", "blocking"}
            else "review" if issue["severity"] == "warning" else "satisfied"
        )
        item(
            "quality", issue["issue_id"], status, issue["code"], issue["affected_refs"]
        )

    for rule in sorted(policy.required_reconciliations):
        matching = [
            r
            for r in unpack(checkpoint["reconciliations"])
            if r["rule_id"] == rule
            and r["period"][:3]
            == [target.start_date.isoformat(), target.end_date.isoformat(), "month"]
            and r["period"][4] == "current"
            and r["currency"] == policy.currency
            and r["scope"] == policy.scope
            and r["dimensions"] == [list(d) for d in policy.dimensions]
        ]
        status = "pending"
        if len(matching) == 1:
            status = {
                "pass": "satisfied",
                "rounding_pass": "satisfied",
                "warning": "review",
            }.get(matching[0]["status"], "pending")
        item(
            "reconciliation",
            rule,
            status,
            "month_end.reconciliation_"
            + (matching[0]["status"] if len(matching) == 1 else "missing_or_ambiguous"),
            [r["result_hash"] for r in matching],
        )

    def calculate(current, prior, rule, operation):
        if not calculate_allowed:
            raise MissingEvidence("month_end.calculation_blocked")
        if (
            current.concept_id != prior.concept_id
            or current.semantic_type != prior.semantic_type
            or current.statement_type != prior.statement_type
        ):
            raise MissingEvidence("month_end.concept_mismatch")
        key = metric_id(current.concept_id, rule, operation)
        try:
            definition = MONTH_END_METRICS.get(key)
        except KeyError as exc:
            raise MissingEvidence("month_end.metric_unsupported") from exc
        inputs = {
            c.role: metric_input(o, c)
            for c, o in zip(definition.operand_contracts, (current, prior))
        }
        result, trace = MetricEngine(MONTH_END_METRICS).calculate(key, inputs)
        result = scope_metric(request.workspace_id, run_id, result, trace)
        calculations.append((result, trace))
        return result, trace

    def comparison(plan):
        ytd = plan.relation == "ytd_year_on_year"
        current, cref = resolve(plan.current, current_month=not ytd)
        prior, pref = resolve(plan.prior)
        if ytd:
            start_month = policy.fiscal_year_start_month
            start_year = target.end_date.year - (target.end_date.month < start_month)
            if (
                current.period.granularity != "ytd"
                or current.period.start_date != date(start_year, start_month, 1)
                or current.period.end_date != target.end_date
            ):
                raise MissingEvidence("month_end.ytd_cutoff_mismatch")
        rule = "previous_month" if plan.relation == "month_on_month" else "prior_year"
        amount, atrace = calculate(current, prior, rule, "subtract")
        percent, ptrace = None, None
        try:
            percent, ptrace = calculate(current, prior, rule, "growth")
        except ZeroDenominatorError:
            pass
        material = (
            amount.value.copy_abs() >= policy.absolute_variation_threshold
            or percent is not None
            and percentage_meets_threshold(
                amount.value, prior.value, policy.percentage_variation_threshold
            )
        )
        data = {
            "key": plan.key,
            "relation": plan.relation,
            "current_value": canonical_decimal(current.value),
            "prior_value": canonical_decimal(prior.value),
            "currency": current.currency,
            "unit": "monetary",
            "scale": "raw",
            "current_period": canonicalize(asdict(current.period)),
            "prior_period": canonicalize(asdict(prior.period)),
            "current_observation_id": str(normalized_id(current)),
            "prior_observation_id": str(normalized_id(prior)),
            "delta": canonical_decimal(amount.value),
            "delta_observation_id": str(amount.observation_id),
            "delta_trace_hash": atrace.trace_hash,
            "percentage": canonical_decimal(percent.value) if percent else None,
            "percentage_observation_id": (
                str(percent.observation_id) if percent else None
            ),
            "percentage_trace_hash": ptrace.trace_hash if ptrace else None,
            "percentage_reason": None if percent else "month_end.zero_comparison_base",
            "material": material,
            "materiality_comparison_policy": "exact_rational_cross_product_v1",
            "direction": (
                "increase"
                if amount.value > 0
                else "decrease" if amount.value < 0 else "unchanged"
            ),
            "evidence": [cref, pref],
        }
        comparisons.append(data)
        item(
            "comparison",
            plan.key,
            "review" if material or percent is None else "satisfied",
            (
                "month_end.material_variation"
                if material
                else (
                    "month_end.zero_comparison_base"
                    if percent is None
                    else "month_end.comparable"
                )
            ),
            [cref, pref],
        )

    for plan in sorted(request.comparisons, key=lambda p: p.key):
        try:
            comparison(plan)
        except MissingEvidence as exc:
            item("comparison", plan.key, "pending", str(exc))
        except MetricCalculationError:
            item(
                "comparison",
                plan.key,
                "pending",
                "month_end.periods_or_values_incompatible",
            )
    for relation in sorted(
        set(policy.required_comparisons) - {p.relation for p in request.comparisons}
    ):
        item(
            "comparison",
            "required:" + relation,
            "pending",
            "month_end.comparison_not_configured",
        )

    for expected in sorted(request.movements, key=lambda m: m.key):
        try:
            current, ref = resolve(expected.current, current_month=True)
            evidence = [ref]
            value = current.value
            if expected.prior is not None:
                prior, pref = resolve(expected.prior)
                observation, _ = calculate(current, prior, "previous_month", "subtract")
                value = observation.value
                evidence.append(pref)
            item(
                "movement",
                expected.key,
                "satisfied" if value != 0 else "pending",
                (
                    "month_end.expected_movement_present"
                    if value != 0
                    else "month_end.expected_movement_absent"
                ),
                evidence,
            )
        except (MissingEvidence, MetricCalculationError):
            item(
                "movement",
                expected.key,
                "pending",
                "month_end.movement_evidence_missing_or_incompatible",
            )

    confirmations = {c.task_id: c for c in request.confirmations}
    for task in sorted(policy.required_tasks):
        confirmation = confirmations.get(task)
        evidence = []
        if confirmation is not None and confirmation.status == "complete":
            try:
                evidence = [
                    resolve(ref, current_month=True)[1] for ref in confirmation.evidence
                ]
            except MissingEvidence:
                evidence = []
        task_item = item(
            "task",
            task,
            "confirmed_by_user" if evidence else "pending",
            (
                "month_end.task_user_confirmed"
                if evidence
                else "month_end.task_unconfirmed"
            ),
            evidence,
        )
        if evidence:
            task_item["confirmed_by_user_id"] = str(request.user_id)

    missing = [
        {
            "item_id": i["item_id"],
            "reason_code": i["reason_code"],
            "target_period": canonicalize(asdict(target)),
            "action": "resolve_" + i["category"],
        }
        for i in checklist
        if i["status"] == "pending"
    ]
    calculations = sorted(
        {o.observation_id: (o, t) for o, t in calculations}.values(),
        key=lambda pair: str(pair[0].observation_id),
    )
    readiness = (
        "incomplete"
        if missing
        else (
            "review_required"
            if any(i["status"] == "review" for i in checklist)
            else "ready_for_review"
        )
    )
    payload = {
        "schema_version": "1",
        "month_end_version": MONTH_END_VERSION,
        "run_id": str(run_id),
        "pipeline_run_id": str(request.pipeline_run_id),
        "target_period": canonicalize(asdict(target)),
        "policy": canonicalize(asdict(policy)),
        "language": language,
        "preparation_readiness": readiness,
        "checklist": checklist,
        "comparisons": comparisons,
        "missing_data_requests": missing,
        "calculations": [
            {"observation": canonicalize(asdict(o)), "trace": t.payload}
            for o, t in calculations
        ],
        "requested_by_user_id": str(request.user_id),
        "source_capability_decisions": quality["decisions"],
        "comparison_authorization": "source_calculate_gate_and_versioned_period_rule",
        "movement_method": "reported_value_or_net_balance_change",
        "movement_coverage": (
            "explicit_expectations" if request.movements else "no_expectations_declared"
        ),
        "performance_assessment": "not_assessed",
        "accounting_close_attested": False,
        "accounting_entries_created": False,
        "customer_publication_authorized": False,
    }
    return payload, calculations
