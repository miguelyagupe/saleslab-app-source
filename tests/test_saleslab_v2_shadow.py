import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from pipeline_fixtures import change_row, request
from shadow_fixtures import RecordingLegacy, main, shadow_case

from saleslab_v2.analyst.context import unpack
from saleslab_v2.config import V2Settings
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.pipeline.evaluator import evaluate
from saleslab_v2.shadow.campaign import FinancialShadowCampaign
from saleslab_v2.shadow.comparison import compare_case, legacy_number
from saleslab_v2.shadow.contracts import (
    InvalidShadow,
    LegacyUnavailable,
    MetricExpectation,
)
from saleslab_v2.shadow.legacy import LegacyFinancialModelRunner, snapshot_legacy
from saleslab_v2.shadow.projection import shared_projection


def evaluated(case):
    result = evaluate(case.request, uuid4(), datetime.now(UTC))
    quality = result.payloads[3]
    checkpoint = {"quality": quality, "metrics": result.payloads[1]["metrics"]}
    v2 = {
        "checkpoint": checkpoint,
        "workflow_status": "completed",
        "report": {"audit_result": {"status": "approved"}, "recommendations": []},
        "audit": {"status": "approved"},
    }
    return result, v2


class ShadowTests(unittest.TestCase):
    def test_policy_differences_in_both_directions_require_review(self):
        case = shadow_case()
        _, v2 = evaluated(case)
        v2["checkpoint"]["quality"] = unpack(v2["checkpoint"]["quality"])
        for legacy_allowed, v2_allowed in ((True, False), (False, True)):
            with self.subTest(legacy_allowed=legacy_allowed):
                decisions = v2["checkpoint"]["quality"]["decisions"]
                forecast = next(d for d in decisions if d["capability"] == "forecast")
                forecast["decision_status"] = "allowed" if v2_allowed else "blocked"
                capture = {
                    "status": "completed",
                    "output": {
                        "report": {"forecast_allowed": legacy_allowed},
                        "usage": None,
                    },
                    "duration_ms": 0,
                }
                result = compare_case(
                    case, capture, v2, configurations={"legacy": {}}, v2_duration_ms=0
                )
                self.assertTrue(result["migration_differences_require_review"])

    def test_shared_projection_is_bound_to_normalized_rows_in_all_languages_and_scales(
        self,
    ):
        for lang in ("pt", "en", "es", "fr"):
            for scale in ("raw", "thousands", "millions"):
                with self.subTest(lang=lang, scale=scale):
                    case = shadow_case(request(language=lang, scale=scale))
                    result, _ = evaluated(case)
                    shared = shared_projection(case, result.payloads[0])
                    multiplier = {"raw": 1, "thousands": 1000, "millions": 1000000}[
                        scale
                    ]
                    self.assertEqual(
                        shared["fields"]["revenue_current"], str(1000 * multiplier)
                    )
                    self.assertEqual(
                        shared["fields"]["net_result"], str(100 * multiplier)
                    )
                    self.assertEqual(shared["request_hash"], case.request.fingerprint)
                    self.assertEqual(len(shared["sources"][0]["rows"]), 5)
                    self.assertNotIn("uploaded_at", shared["sources"][0]["document"])

    def test_projection_rejects_wrong_concepts_periods_and_foreign_sources(self):
        case = shadow_case(growth=True)
        result, _ = evaluated(case)
        sid = case.request.sources[0].document.source_id
        from saleslab_v2.pipeline.contracts import InputRef

        for binding in (
            InputRef("net_income", sid),
            InputRef("revenue_prior", sid),
            InputRef("revenue", uuid4()),
            InputRef("margin"),
        ):
            with self.subTest(binding=binding):
                wrong = replace(case, legacy_bindings=(("revenue_current", binding),))
                with self.assertRaises(InvalidShadow):
                    shared_projection(wrong, result.payloads[0])

    def test_canonical_fraction_is_not_misread_as_thousands_by_legacy_parser(self):
        case = shadow_case(change_row(request(), "revenue", text="1234.567"))
        result, _ = evaluated(case)
        shared = shared_projection(case, result.payloads[0])
        self.assertEqual(shared["fields"]["revenue_current"], "1234,567")
        self.assertEqual(
            main.parse_numeric_value(shared["fields"]["revenue_current"]), 1234.567
        )

    def test_actual_legacy_and_v2_compare_against_exact_expected_values(self):
        case = shadow_case(growth=True)
        result, v2 = evaluated(case)
        legacy = RecordingLegacy()
        out = asyncio.run(legacy.generate(shared_projection(case, result.payloads[0])))
        capture = {
            "status": "completed",
            "output": out,
            "error_code": None,
            "duration_ms": 3,
        }
        report = compare_case(
            case,
            capture,
            v2,
            configurations={"legacy": legacy.configuration},
            v2_duration_ms=10,
        )
        self.assertEqual(report["status"], "pass")
        self.assertTrue(
            all(
                d["legacy_matches_expected"]
                for d in report["metric_differences"]
                if d["legacy_metric_id"] is not None
            )
        )
        unsupported = next(
            d for d in report["metric_differences"] if d["step_key"] == "growth"
        )
        self.assertEqual(unsupported["comparison_status"], "unsupported")
        self.assertTrue(report["migration_differences_require_review"])
        self.assertTrue(report["policy_differences"]["legacy_forecast_allowed"])
        self.assertEqual(report["semantic_quality"], "not_certified")
        self.assertFalse(report["rollout_authorized"])
        self.assertIsNone(report["measurements"]["monetary_cost"])

    def test_matching_implementations_cannot_pass_when_both_disagree_with_gold_truth(
        self,
    ):
        case = shadow_case()
        _, v2 = evaluated(case)
        v2["checkpoint"]["metrics"][0]["observation"]["value"] = "99"
        capture = {
            "status": "completed",
            "output": {
                "report": {
                    "metrics": [{"metric_id": "net_margin_pct", "value": "99,0%"}]
                },
                "usage": None,
            },
            "duration_ms": 0,
        }
        report = compare_case(
            case, capture, v2, configurations={"legacy": {}}, v2_duration_ms=0
        )
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["metric_differences"][0]["legacy_matches_expected"])

    def test_unmapped_metric_requires_review_even_when_other_policies_match(self):
        case = shadow_case()
        case = replace(
            case,
            metrics=(
                replace(
                    case.metrics[0],
                    legacy_metric_id=None,
                    legacy_absolute_tolerance=None,
                ),
            ),
        )
        _, v2 = evaluated(case)
        capture = {
            "status": "completed",
            "output": {
                "report": {"forecast_allowed": False, "recommendations": []},
                "usage": None,
            },
            "duration_ms": 0,
        }
        result = compare_case(
            case, capture, v2, configurations={"legacy": {}}, v2_duration_ms=0
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(len(result["metric_differences"]), 1)
        self.assertEqual(
            result["metric_differences"][0]["reason_code"],
            "shadow.legacy_metric_not_mapped",
        )
        self.assertIsNone(result["metric_differences"][0]["legacy_matches_expected"])
        self.assertTrue(result["migration_differences_require_review"])

    def test_known_legacy_zero_revenue_failure_is_captured_without_changing_legacy(
        self,
    ):
        case = shadow_case(change_row(request(), "revenue", text="0"))
        result, _ = evaluated(case)
        with self.assertRaises(LegacyUnavailable):
            asyncio.run(
                RecordingLegacy().generate(shared_projection(case, result.payloads[0]))
            )

    def test_legacy_display_parser_rejects_ambiguous_or_wrong_unit_values(self):
        self.assertEqual(
            legacy_number("€1.234", unit="monetary", currency="EUR"), Decimal(1234)
        )
        self.assertEqual(
            legacy_number("-12,3%", unit="percentage", currency=None), Decimal("-12.3")
        )
        for text in ("12.3%", "n/a", "10 EUR", "10", "NaN%", "€1,2,3"):
            with self.subTest(text=text):
                self.assertIsNone(legacy_number(text, unit="percentage", currency=None))

    def test_monetary_parser_accepts_actual_legacy_output_for_non_eur_currencies(self):
        for currency in ("EUR", "USD", "GBP", "BRL", "JPY", "CHF"):
            for value in (1234.0, -1234.56, 0.0):
                with self.subTest(currency=currency, value=value):
                    rendered = main.format_amount(value, currency)
                    self.assertEqual(
                        legacy_number(rendered, unit="monetary", currency=currency),
                        Decimal(str(value)),
                    )
                    self.assertIsNone(
                        legacy_number(rendered, unit="monetary", currency="CAD")
                    )

    def test_case_has_explicit_thresholds_and_locale_identity(self):
        with self.assertRaises(ValueError):
            MetricExpectation(
                "margin", Decimal(10), "percentage", None, "net_margin_pct"
            )
        with self.assertRaises(TypeError):
            MetricExpectation("margin", Decimal("NaN"), "percentage", None)
        with self.assertRaises(ValueError):
            MetricExpectation(
                "margin", Decimal(10), "percentage", None, "net_margin_pct", Decimal(-1)
            )
        case = shadow_case()
        from saleslab_v2.domain.language import LocaleTag

        other = replace(
            case,
            request=replace(
                case.request,
                language=replace(case.request.language, format_locale=LocaleTag.FR_FR),
            ),
        )
        self.assertEqual(case.request.fingerprint, other.request.fingerprint)
        self.assertNotEqual(
            semantic_hash(case.payload()), semantic_hash(other.payload())
        )

    def test_legacy_float_is_preserved_explicitly_and_nonfinite_values_fail(self):
        self.assertEqual(
            snapshot_legacy({"amount": 0.1}), {"amount": {"legacy_float": "0.1"}}
        )
        with self.assertRaises(LegacyUnavailable):
            snapshot_legacy({"amount": float("nan")})

    def test_campaign_rejects_missing_duplicate_or_mixed_cases_before_calls(self):
        case = shadow_case()
        runner = Mock(execute=AsyncMock())
        campaign = FinancialShadowCampaign(runner)
        for cases, required in (
            ((case,), (case.case_id, "missing")),
            ((case, case), (case.case_id,)),
            ((), (case.case_id,)),
        ):
            with self.subTest(cases=cases), self.assertRaises(InvalidShadow):
                asyncio.run(
                    campaign.execute(
                        cases,
                        required_case_ids=required,
                        dataset_version=case.dataset_version,
                        campaign_key="golden",
                    )
                )
        runner.execute.assert_not_awaited()

    def test_model_baseline_uses_current_legacy_schema_and_injected_transport(self):
        client = Mock()
        client.with_options.return_value = client
        client.messages.create = AsyncMock(
            return_value=SimpleNamespace(
                stop_reason="end_turn",
                content=[
                    SimpleNamespace(
                        type="text",
                        text='{"report_type":"financial","kpis":[],"alerts":[],"recommendations":[]}',
                    )
                ],
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )
        )
        legacy = LegacyFinancialModelRunner(main, client, model="explicit-test-model")
        case = shadow_case(growth=True)
        result, v2 = evaluated(case)
        output = asyncio.run(
            legacy.generate(shared_projection(case, result.payloads[0]))
        )
        self.assertEqual(output["usage"], {"input_tokens": 100, "output_tokens": 20})
        self.assertEqual(output["deterministic_policy"], {"forecast_allowed": True})
        comparison = compare_case(
            case,
            {"status": "completed", "output": output, "duration_ms": 0},
            v2,
            configurations={"legacy": legacy.configuration},
            v2_duration_ms=0,
        )
        self.assertTrue(comparison["policy_differences"]["legacy_forecast_allowed"])
        self.assertTrue(comparison["migration_differences_require_review"])
        self.assertEqual(legacy.configuration["mode"], "legacy_model_shadow")
        self.assertEqual(client.with_options.call_args.kwargs["max_retries"], 0)
        client.messages.create.return_value.stop_reason = "max_tokens"
        with self.assertRaises(LegacyUnavailable):
            asyncio.run(legacy.generate(shared_projection(case, result.payloads[0])))

    def test_shadow_flag_is_strict_and_default_off(self):
        self.assertFalse(V2Settings.from_mapping({}).financial_shadow_enabled)
        self.assertTrue(
            V2Settings.from_mapping(
                {"SALESLAB_V2_FINANCIAL_SHADOW_ENABLED": "true"}
            ).financial_shadow_enabled
        )
        with self.assertRaises(ValueError):
            V2Settings.from_mapping({"SALESLAB_V2_FINANCIAL_SHADOW_ENABLED": "1"})
