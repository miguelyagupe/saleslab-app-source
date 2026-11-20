import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
from uuid import UUID

from saleslab_v2.domain.value_objects import Period
from saleslab_v2.metrics.definitions import MetricDefinition, MetricRegistry
from saleslab_v2.metrics.engine import (
    IncompatibleInputError,
    MetricEngine,
    MetricInput,
    MissingInputError,
    ZeroDenominatorError,
)
from saleslab_v2.metrics.financial_v1 import FINANCIAL_V1

PERIOD = Period(
    date(2026, 1, 1), date(2026, 12, 31), granularity="year", comparison_basis="current"
)


def item(
    role,
    value,
    *,
    unit="monetary",
    currency="EUR",
    period=PERIOD,
    dimensions=(),
    number=1,
    metric_id=None,
    status="confirmed",
    variant=None,
):
    return MetricInput(
        UUID(f"00000000-0000-4000-8000-{number:012d}"),
        metric_id or role,
        Decimal(value),
        unit,
        currency if unit == "monetary" else None,
        period,
        "consolidated",
        dimensions,
        (f"sheet:1!B{number}",),
        (("source_hash", "a" * 64),),
        status,
        variant,
    )


class MetricEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = MetricEngine(FINANCIAL_V1)

    def calculate(self, metric, values):
        definition = FINANCIAL_V1.get(metric)
        return self.engine.calculate(
            metric,
            {
                contract.role: item(
                    contract.role,
                    value,
                    number=index + 1,
                    unit=contract.unit_family,
                    metric_id=contract.metric_ids[0],
                    variant=contract.variants[0],
                    dimensions=contract.required_dimensions,
                )
                for index, (contract, value) in enumerate(
                    zip(definition.operand_contracts, values)
                )
            },
        )

    def test_statutory_adjusted_golden_bridge_remains_explicit(self):
        statutory, st = self.calculate("ebitda_statutory", ["2000", "113"])
        adjusted, at = self.calculate("ebitda_adjusted", ["2113", "119"])
        self.assertEqual(statutory.value, Decimal(2113))
        self.assertEqual(adjusted.value, Decimal(2232))
        self.assertNotEqual(st.metric_id, at.metric_id)
        with self.assertRaises(MissingInputError):
            self.engine.calculate(
                "ebitda_statutory",
                {
                    "reported_unverified_ebitda": item(
                        "reported_unverified_ebitda", "2113"
                    )
                },
            )

    def test_golden_percentages_have_complete_operand_ids_and_values(self):
        margin, trace = self.calculate(
            "adjustments_to_statutory_ebitda_pct", ["93.12", "2113"]
        )
        self.assertEqual(margin.rounded_value, Decimal("4.41"))
        self.assertEqual(
            [x.role for x in trace.inputs],
            ["explicit_ebitda_adjustments", "ebitda_statutory"],
        )
        self.assertTrue(
            all(x.observation_id and isinstance(x.value, Decimal) for x in trace.inputs)
        )
        concentration, ctrace = self.calculate(
            "adjustments_to_reference_amount_pct", ["93.12", "24"]
        )
        self.assertEqual(concentration.value, Decimal("388.00"))
        self.assertEqual(len(ctrace.inputs), 2)
        self.assertEqual(ctrace.metric_id, "adjustments_to_reference_amount_pct")

    def test_fcf_sign_is_cfo_minus_positive_outflow(self):
        result, trace = self.calculate("free_cash_flow", ["100", "30"])
        self.assertEqual(result.value, Decimal(70))
        self.assertEqual(trace.canonical_expression, "cfo-capex_outflow_positive")

    def test_fcf_version_two_preserves_signed_cash_outflows_and_exact_arithmetic(self):
        for cfo, purchase, expected in (
            ("100", "-30", "70"),
            ("-100", "-30", "-130"),
            ("100", "0", "100"),
            ("1E+100", "-1", "9" * 100),
        ):
            inputs = {
                "cfo": item("cfo", cfo),
                "reported_capex_cash_outflow_negative": item(
                    "capex", purchase, number=2
                ),
            }
            with self.subTest(cfo=cfo, purchase=purchase), localcontext() as context:
                context.prec = 6
                result, trace = self.engine.calculate("free_cash_flow", inputs, "2")
                self.assertEqual(result.value, Decimal(expected))
                self.assertEqual(trace.metric_definition_version, "2")
                self.assertEqual(trace.inputs[1].value, Decimal(purchase))
                self.assertEqual(
                    trace.canonical_expression,
                    "cfo+reported_capex_cash_outflow_negative",
                )
        with self.assertRaises(IncompatibleInputError):
            self.engine.calculate(
                "free_cash_flow",
                {
                    "cfo": item("cfo", "100"),
                    "reported_capex_cash_outflow_negative": item(
                        "capex", "30", number=2
                    ),
                },
                "2",
            )
        contract = FINANCIAL_V1.get("free_cash_flow", "2").operand_contracts[1]
        with self.assertRaises(ValueError):
            replace(contract, value_constraint="guess_sign")

    def test_zero_missing_and_incompatible_inputs_fail_typed(self):
        with self.assertRaises(ZeroDenominatorError):
            self.calculate("current_ratio", ["1", "0"])
        with self.assertRaises(MissingInputError):
            self.engine.calculate(
                "current_ratio", {"current_assets": item("current_assets", "1")}
            )
        definition = FINANCIAL_V1.get("current_ratio")
        inputs = {
            role: item(role, value, number=i + 1)
            for i, (role, value) in enumerate(zip(definition.inputs, ["1", "2"]))
        }
        inputs["current_liabilities"] = replace(
            inputs["current_liabilities"], currency="USD"
        )
        with self.assertRaises(IncompatibleInputError):
            self.engine.calculate("current_ratio", inputs)
        inputs["current_liabilities"] = replace(
            inputs["current_liabilities"],
            currency="EUR",
            dimensions=(("segment", "north"),),
        )
        with self.assertRaises(IncompatibleInputError):
            self.engine.calculate("current_ratio", inputs)

    def test_growth_requires_comparable_structured_periods(self):
        prior = replace(
            PERIOD,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            comparison_basis="comparative",
        )
        inputs = {
            "revenue_current": item("revenue_current", "110"),
            "revenue_prior": item("revenue_prior", "100", period=prior, number=2),
        }
        result, _ = self.engine.calculate("revenue_growth_reported_pct", inputs)
        self.assertEqual(result.value, Decimal("10.0"))
        with self.assertRaises(IncompatibleInputError):
            self.engine.calculate(
                "revenue_growth_reported_pct",
                {
                    **inputs,
                    "revenue_prior": replace(
                        inputs["revenue_prior"],
                        period=replace(prior, comparison_basis="budget"),
                    ),
                },
            )
        invalid_priors = (
            replace(prior, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)),
            Period(
                date(2024, 7, 1),
                date(2025, 6, 30),
                granularity="year",
                fiscal_year=2025,
                comparison_basis="comparative",
                fiscal_year_end_month=6,
            ),
            replace(
                PERIOD,
                start_date=date(2027, 1, 1),
                end_date=date(2027, 12, 31),
                comparison_basis="comparative",
            ),
        )
        for invalid in invalid_priors:
            with self.subTest(invalid=invalid):
                with self.assertRaises(IncompatibleInputError):
                    self.engine.calculate(
                        "revenue_growth_reported_pct",
                        {
                            **inputs,
                            "revenue_prior": replace(
                                inputs["revenue_prior"], period=invalid
                            ),
                        },
                    )
        ytd_current = Period(
            date(2026, 1, 1),
            date(2026, 9, 30),
            granularity="ytd",
            comparison_basis="current",
        )
        ytd_prior = Period(
            date(2025, 1, 1),
            date(2025, 3, 31),
            granularity="ytd",
            comparison_basis="comparative",
        )
        with self.assertRaises(IncompatibleInputError):
            self.engine.calculate(
                "revenue_growth_reported_pct",
                {
                    "revenue_current": item("revenue_current", "9", period=ytd_current),
                    "revenue_prior": item(
                        "revenue_prior", "3", period=ytd_prior, number=2
                    ),
                },
            )

    def test_hash_order_and_decimal_context_are_irrelevant(self):
        hashes = set()
        for precision in (6, 28, 100):
            with localcontext() as context:
                context.prec = precision
                left = item("gross_profit", "1", dimensions=(("b", "2"), ("a", "1")))
                right = item(
                    "revenue", "3", dimensions=(("a", "1"), ("b", "2")), number=2
                )
                hashes.add(
                    self.engine.calculate(
                        "gross_margin_pct", {"revenue": right, "gross_profit": left}
                    )[1].trace_hash
                )
        self.assertEqual(len(hashes), 1)

    def test_operand_contracts_reject_identity_unit_status_and_variant(self):
        definition = FINANCIAL_V1.get("ebitda_margin_pct")
        valid = {
            "ebitda_for_margin": item(
                "ebitda_for_margin",
                "100",
                metric_id="ebitda_statutory",
                variant="statutory",
            ),
            "revenue": item("revenue", "1000", number=2),
        }
        for bad in (
            replace(valid["ebitda_for_margin"], metric_id="ebitda_reported_unverified"),
            replace(
                valid["ebitda_for_margin"], normalization_status="needs_confirmation"
            ),
            replace(valid["ebitda_for_margin"], variant=None),
            replace(valid["ebitda_for_margin"], unit="days", currency=None),
        ):
            with self.assertRaises(IncompatibleInputError):
                self.engine.calculate(
                    "ebitda_margin_pct", {**valid, "ebitda_for_margin": bad}
                )
        self.assertEqual(definition.inputs, tuple(valid))

    def test_decimal_exponents_and_division_policy_are_deterministic(self):
        result, _ = self.calculate("gross_profit", ["1E+1000", "1"])
        self.assertEqual(result.value, Decimal("9" * 1000))
        ratio, trace = self.calculate("current_ratio", ["1", "3"])
        self.assertTrue(str(ratio.value).startswith("0.333333"))
        self.assertEqual(trace.division_policy, "decimal-significant-digits-v1:100")

    def test_registry_is_immutable_and_cycles_fail(self):
        with self.assertRaises(AttributeError):
            FINANCIAL_V1.definitions.append(None)
        a = MetricDefinition("a", "1", "a", "b", ("b",), "identity", "ratio")
        b = MetricDefinition("b", "1", "b", "a", ("a",), "identity", "ratio")
        with self.assertRaisesRegex(ValueError, "cycle"):
            MetricRegistry((a, b))
        with self.assertRaisesRegex(KeyError, "unknown"):
            FINANCIAL_V1.get("not_there")

    def test_catalog_contains_only_explicit_required_variants(self):
        required = {
            "revenue_growth_cc_pct",
            "revenue_growth_lfl_pct",
            "revenue_change_reported",
            "growth_lfl_contribution",
            "growth_expansion_acquisition_contribution",
            "growth_fx_contribution",
            "growth_other_contribution",
            "cash_and_cash_equivalents",
            "current_operating_assets",
            "current_operating_liabilities",
            "average_trade_receivables",
            "average_inventory",
            "average_trade_payables",
            "purchases",
            "interest_expense",
            "lease_liabilities",
            "debt_drawdowns",
            "debt_repayments",
            "debt_fx_other",
            "lease_additions",
            "lease_modifications",
            "lease_interest",
            "lease_payments",
            "lease_fx_other",
            "quick_assets",
            "days_in_period",
            "cost_of_sales",
            "operating_costs",
            "ebit_confirmed",
            "depreciation_and_amortization",
            "ebitda_adjustments",
            "other_operating_income_expense",
            "net_finance_cost",
            "income_tax",
            "revenue",
            "revenue_growth_reported_pct",
            "gross_profit",
            "gross_margin_pct",
            "ebitda_statutory",
            "ebitda_adjusted",
            "ebitda_margin_pct",
            "ebit_margin_pct",
            "net_margin_pct",
            "cfo",
            "cfi",
            "cff",
            "cash_fx_other",
            "capex",
            "free_cash_flow",
            "cash_conversion_pct",
            "net_working_capital",
            "dso",
            "dio",
            "dpo",
            "cash_conversion_cycle",
            "roic",
            "current_ratio",
            "quick_ratio",
            "gross_debt",
            "net_debt",
            "net_debt_incl_leases",
            "net_debt_to_ebitda",
            "interest_coverage",
            "capex_cash_total",
            "capex_cash_maintenance",
            "capex_cash_growth",
            "capex_commitments_outstanding",
            "capex_maintenance_share_pct",
            "capex_growth_share_pct",
            "variable_rate_share_pct",
            "fixed_variable_mix",
            "maturity_concentration",
            "adjustments_to_statutory_ebitda_pct",
            "adjustments_to_reference_amount_pct",
        }
        required.update(
            {
                "reported_operating_nwc",
                "reported_operating_nwc_change",
                "operating_nwc_change",
            }
        )
        self.assertEqual({x.metric_id for x in FINANCIAL_V1.definitions}, required)
