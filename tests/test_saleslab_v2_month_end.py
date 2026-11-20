"""F-10 truth-based monthly preparation and temporal arithmetic regressions."""

import calendar
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from uuid import uuid4

from month_end_fixtures import close_request, monthly
from pipeline_fixtures import change_row

from saleslab_v2.config import V2Settings
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.domain.value_objects import Period
from saleslab_v2.metrics.engine import IncompatibleInputError, MetricEngine, MetricInput
from saleslab_v2.month_end.contracts import MovementExpectation
from saleslab_v2.month_end.evaluator import evaluate
from saleslab_v2.month_end.metrics import (
    MONTH_END_METRICS,
    metric_id,
    percentage_meets_threshold,
)
from saleslab_v2.pipeline.evaluator import evaluate as pipeline_evaluate


def evaluated(value, request=None, language="pt"):
    request = request or close_request(value)
    pipeline = pipeline_evaluate(value, request.pipeline_run_id, datetime.now(UTC))
    checkpoint = {
        "quality": pipeline.payloads[3],
        "reconciliations": [r.payload() for r in pipeline.reconciliations],
    }
    result, calculations = evaluate(
        request,
        pipeline.payloads[0],
        canonicalize(checkpoint),
        run_id=uuid4(),
        language=language,
    )
    return result, calculations


class MonthEndTests(unittest.TestCase):
    def test_reconciliation_tolerance_states_preserve_the_pipeline_decision(self):
        for amount, outcome, item_status, readiness in (
            ("100.005", "rounding_pass", "satisfied", "ready_for_review"),
            ("100.5", "warning", "review", "review_required"),
            ("102", "critical", "pending", "incomplete"),
            ("200", "blocking", "pending", "incomplete"),
        ):
            with self.subTest(outcome=outcome):
                result, _ = evaluated(change_row(monthly(), "assets", text=amount))
                reconciliation = next(
                    i for i in result["checklist"] if i["category"] == "reconciliation"
                )
                self.assertEqual(
                    reconciliation["reason_code"], "month_end.reconciliation_" + outcome
                )
                self.assertEqual(reconciliation["status"], item_status)
                self.assertEqual(result["preparation_readiness"], readiness)

    def test_generated_comparison_gaps_cannot_collide_with_configured_plan_ids(self):
        value = monthly()
        request = close_request(value)
        plan = request.comparisons[0]
        for relation in request.policy.required_comparisons:
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                replace(plan, key="required:" + relation)
        # A valid similar key must coexist with every automatically generated gap.
        request = replace(request, comparisons=(replace(plan, key="required"),))
        result, _ = evaluated(value, request)
        item_ids = [item["item_id"] for item in result["checklist"]]
        gap_ids = [gap["item_id"] for gap in result["missing_data_requests"]]
        self.assertEqual(len(item_ids), len(set(item_ids)))
        self.assertEqual(len(gap_ids), len(set(gap_ids)))
        self.assertIn("comparison/required", item_ids)
        self.assertIn("comparison/required:month_year_on_year", gap_ids)

    def test_complete_declining_month_is_ready_for_review_and_every_change_is_traced(
        self,
    ):
        result, calculations = evaluated(monthly())
        self.assertEqual(result["preparation_readiness"], "ready_for_review")
        self.assertEqual(len(result["comparisons"]), 3)
        mom = next(
            c for c in result["comparisons"] if c["relation"] == "month_on_month"
        )
        self.assertEqual(mom["delta"], "-100")
        self.assertEqual(mom["direction"], "decrease")
        self.assertTrue(Decimal(mom["percentage"]) < 0)
        ytd = next(
            c for c in result["comparisons"] if c["relation"] == "ytd_year_on_year"
        )
        self.assertEqual((ytd["delta"], ytd["percentage"]), ("2000", "25"))
        self.assertEqual(len(calculations), 6)
        self.assertEqual(len({o.calculation_trace_hash for o, _ in calculations}), 6)
        for observation, trace in calculations:
            self.assertEqual(trace.exact_output, observation.value)
            self.assertTrue(trace.inputs)
        self.assertFalse(result["accounting_close_attested"])
        self.assertFalse(result["accounting_entries_created"])
        self.assertEqual(result["performance_assessment"], "not_assessed")

    def test_material_variation_requires_review_without_making_data_incomplete(self):
        value = monthly()
        request = close_request(value)
        request = replace(
            request,
            policy=replace(request.policy, absolute_variation_threshold=Decimal(100)),
        )
        result, _ = evaluated(value, request)
        self.assertEqual(result["preparation_readiness"], "review_required")
        self.assertEqual(result["missing_data_requests"], [])
        self.assertTrue(any(c["material"] for c in result["comparisons"]))

    def test_missing_statement_confirmation_and_comparison_are_explicit(self):
        value = monthly()
        request = close_request(value)
        request = replace(
            request,
            comparisons=request.comparisons[:1],
            confirmations=(),
            policy=replace(
                request.policy,
                required_statements=(
                    *request.policy.required_statements,
                    "cash_flow_statement",
                ),
            ),
        )
        result, _ = evaluated(value, request)
        reasons = {r["reason_code"] for r in result["missing_data_requests"]}
        self.assertEqual(result["preparation_readiness"], "incomplete")
        self.assertTrue(
            {
                "month_end.statement_missing",
                "month_end.task_unconfirmed",
                "month_end.comparison_not_configured",
            }
            <= reasons
        )

    def test_zero_prior_preserves_exact_delta_and_never_manufactures_a_percentage(self):
        value = change_row(monthly(), "revenue_prior_month", text="0")
        result, _ = evaluated(value)
        mom = next(
            c for c in result["comparisons"] if c["relation"] == "month_on_month"
        )
        self.assertEqual(mom["delta"], "1000")
        self.assertIsNone(mom["percentage"])
        self.assertEqual(mom["percentage_reason"], "month_end.zero_comparison_base")
        self.assertEqual(result["preparation_readiness"], "review_required")

    def test_missing_movement_is_a_close_gap_only_when_explicitly_expected(self):
        value = change_row(monthly(), "net_income", text="0")
        result, _ = evaluated(value)
        self.assertIn(
            "month_end.expected_movement_absent",
            {m["reason_code"] for m in result["missing_data_requests"]},
        )
        request = replace(close_request(value), movements=())
        without, _ = evaluated(value, request)
        self.assertEqual(without["preparation_readiness"], "ready_for_review")
        self.assertEqual(without["movement_coverage"], "no_expectations_declared")

    def test_changed_balance_expectation_has_its_own_trace_and_cannot_choose_other_concept(
        self,
    ):
        value = monthly()
        request = close_request(value)
        plan = request.comparisons[0]
        request = replace(
            request,
            movements=(
                MovementExpectation(
                    "change", plan.current, "changed_from_prior_month", plan.prior
                ),
            ),
        )
        result, calculations = evaluated(value, request)
        self.assertEqual(result["preparation_readiness"], "ready_for_review")
        self.assertEqual(len(calculations), 6)  # the same computation is stored once
        request = replace(
            request,
            movements=(
                replace(
                    request.movements[0], prior=request.confirmations[0].evidence[0]
                ),
            ),
        )
        result, _ = evaluated(value, request)
        self.assertEqual(result["preparation_readiness"], "incomplete")

    def test_wrong_scope_currency_period_or_ytd_cutoff_cannot_produce_comparison(self):
        for changes in (
            {"currency": "USD"},
            {"scope": "segment", "dimensions": (("segment", "x"),)},
            {"period_label": "August 2026"},
        ):
            with self.subTest(changes=changes):
                value = change_row(monthly(), "revenue_prior_month", **changes)
                result, _ = evaluated(value)
                self.assertNotIn(
                    "revenue-mom", {c["key"] for c in result["comparisons"]}
                )
                self.assertEqual(result["preparation_readiness"], "incomplete")
        value = change_row(monthly(), "revenue_ytd", period_label="YTD September 2026")
        result, _ = evaluated(value)
        self.assertIn(
            "month_end.ytd_cutoff_mismatch",
            {m["reason_code"] for m in result["missing_data_requests"]},
        )

    def test_blocked_reconciliation_does_not_bypass_calculation_gate(self):
        result, calculations = evaluated(change_row(monthly(), "assets", text="200"))
        self.assertEqual(result["preparation_readiness"], "incomplete")
        self.assertEqual(result["comparisons"], [])
        self.assertEqual(calculations, [])
        self.assertIn(
            "month_end.calculation_blocked",
            {m["reason_code"] for m in result["missing_data_requests"]},
        )

    def test_labels_exist_in_all_four_languages(self):
        titles = []
        for language in ("pt", "en", "es", "fr"):
            with self.subTest(language=language):
                result, _ = evaluated(monthly(language=language), language=language)
                self.assertEqual(result["preparation_readiness"], "ready_for_review")
                self.assertEqual(result["language"], language)
                titles.append(result["checklist"][0]["title"])
        self.assertEqual(len(set(titles)), 4)

    def test_strict_flag_and_explicit_finite_thresholds(self):
        self.assertFalse(V2Settings.from_mapping({}).financial_month_end_enabled)
        self.assertTrue(
            V2Settings.from_mapping(
                {"SALESLAB_V2_FINANCIAL_MONTH_END_ENABLED": "true"}
            ).financial_month_end_enabled
        )
        with self.assertRaises(ValueError):
            V2Settings.from_mapping({"SALESLAB_V2_FINANCIAL_MONTH_END_ENABLED": "1"})
        policy = close_request(monthly()).policy
        for value in (Decimal(0), Decimal(-1), 1.0, Decimal("NaN")):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                replace(policy, absolute_variation_threshold=value)

    def test_calendar_comparisons_accept_year_boundary_and_leap_february(self):
        engine = MetricEngine(MONTH_END_METRICS)
        for relation, current_ym, prior_ym in (
            ("previous_month", (2026, 1), (2025, 12)),
            ("prior_year", (2024, 2), (2023, 2)),
        ):

            def operand(ym, value):
                year, month = ym
                period = Period(
                    date(year, month, 1),
                    date(year, month, calendar.monthrange(year, month)[1]),
                    granularity="month",
                    comparison_basis="current",
                )
                return MetricInput(
                    uuid4(),
                    "revenue",
                    Decimal(value),
                    "monetary",
                    "EUR",
                    period,
                    "consolidated",
                )

            current, prior = operand(current_ym, "11"), operand(prior_ym, "10")
            with self.subTest(relation=relation):
                observation, _ = engine.calculate(
                    metric_id("revenue", relation, "growth"),
                    {"current": current, "prior": prior},
                )
                self.assertEqual(observation.value, 10)
                with self.assertRaises(IncompatibleInputError):
                    engine.calculate(
                        metric_id("revenue", relation, "growth"),
                        {"current": prior, "prior": current},
                    )

    def test_materiality_comparison_uses_unrounded_decimal_even_in_small_context(self):
        value = change_row(
            monthly(), "revenue_prior_month", text="999.000000000000000000000000000001"
        )
        request = close_request(value)
        request = replace(
            request,
            policy=replace(request.policy, absolute_variation_threshold=Decimal(1)),
        )
        with localcontext() as ctx:
            ctx.prec = 5
            result, _ = evaluated(value, request)
        mom = next(
            c for c in result["comparisons"] if c["relation"] == "month_on_month"
        )
        self.assertFalse(mom["material"])

    def test_fiscal_ytd_uses_explicit_year_boundary_and_same_cutoff(self):
        value = change_row(
            monthly(),
            "revenue_ytd",
            period_label="FY 2027 YTD Q1",
            fiscal_year_end_month=7,
        )
        value = change_row(
            value,
            "revenue_ytd_prior",
            period_label="FY 2026 YTD Q1",
            fiscal_year_end_month=7,
        )
        request = close_request(value)
        request = replace(
            request, policy=replace(request.policy, fiscal_year_start_month=8)
        )
        result, _ = evaluated(value, request)
        self.assertEqual(result["preparation_readiness"], "ready_for_review")
        ytd = next(
            c for c in result["comparisons"] if c["relation"] == "ytd_year_on_year"
        )
        self.assertEqual(ytd["delta"], "2000")
        wrong_calendar, _ = evaluated(
            value,
            replace(request, policy=replace(request.policy, fiscal_year_start_month=1)),
        )
        self.assertEqual(wrong_calendar["preparation_readiness"], "incomplete")

    def test_confirming_a_task_with_prior_period_evidence_does_not_complete_it(self):
        value = monthly()
        request = close_request(value)
        confirmation = replace(
            request.confirmations[0], evidence=(request.comparisons[0].prior,)
        )
        result, _ = evaluated(value, replace(request, confirmations=(confirmation,)))
        task = next(i for i in result["checklist"] if i["category"] == "task")
        self.assertEqual(task["status"], "pending")
        self.assertNotIn("confirmed_by_user_id", task)

    def test_repeating_percentage_rounded_to_threshold_is_not_material(self):
        value = change_row(monthly(), "revenue", text="5")
        value = change_row(value, "revenue_prior_month", text="3")
        request = close_request(value)
        threshold = Decimal("66." + "6" * 97 + "7")
        request = replace(
            request,
            comparisons=request.comparisons[:1],
            policy=replace(
                request.policy,
                required_comparisons=("month_on_month",),
                percentage_variation_threshold=threshold,
            ),
        )
        result, _ = evaluated(value, request)
        comparison = result["comparisons"][0]
        self.assertEqual(Decimal(comparison["percentage"]), threshold)
        self.assertFalse(comparison["material"])
        self.assertEqual(result["preparation_readiness"], "ready_for_review")
        for precision in (5, 28, 100):
            with self.subTest(precision=precision), localcontext() as context:
                context.prec = precision
                self.assertFalse(
                    percentage_meets_threshold(Decimal(2), Decimal(3), threshold)
                )
                self.assertFalse(
                    percentage_meets_threshold(Decimal(-2), Decimal(-3), threshold)
                )
                self.assertTrue(
                    percentage_meets_threshold(Decimal(2), Decimal(3), Decimal("66.6"))
                )
                self.assertTrue(
                    percentage_meets_threshold(Decimal(1), Decimal(2), Decimal(50))
                )
