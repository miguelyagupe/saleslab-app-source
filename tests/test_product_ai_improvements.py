import os
import unittest
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/saleslab_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-with-at-least-32-characters")

import main


class ProductAiInputTests(unittest.TestCase):
    def test_manual_efficiency_input_includes_module_specific_fields(self):
        parts = main.build_manual_input_parts(
            report_type="efficiency",
            currency="EUR",
            period_current="Março 2026",
            team_data="Equipa A: 160h, 48 unidades, custo €2.400",
            output_metric="unidades",
            total_labor_cost="€18.500",
            extra_notes="Pico sazonal",
        )

        joined = "\n".join(parts)
        self.assertIn("Dados por equipa ou colaborador", joined)
        self.assertIn("Equipa A: 160h", joined)
        self.assertIn("Métrica de output: unidades", joined)
        self.assertIn("Custo total de pessoal (EUR): €18.500", joined)

    def test_manual_financial_input_includes_pnl_fields(self):
        parts = main.build_manual_input_parts(
            report_type="financial",
            currency="EUR",
            revenue_current="€100.000",
            revenue_previous="€90.000",
            cogs="€60.000",
            opex="€25.000",
            net_result="€10.000",
        )

        joined = "\n".join(parts)
        self.assertIn("COGS / custo dos bens ou serviços (EUR): €60.000", joined)
        self.assertIn("Despesas operacionais (EUR): €25.000", joined)
        self.assertIn("Resultado líquido (EUR): €10.000", joined)

    def test_frontend_formdata_sends_module_specific_fields(self):
        html = Path("index.html").read_text()
        for field_name in [
            "team_data",
            "output_metric",
            "total_labor_cost",
            "total_costs",
            "cogs",
            "opex",
            "net_result",
        ]:
            self.assertIn(f"{field_name}:", html)

    def test_deterministic_financial_calculates_margin_metrics(self):
        analysis = main.build_deterministic_analysis(
            "financial",
            "EUR",
            {
                "revenue_current": "€100.000",
                "revenue_previous": "€90.000",
                "cogs": "€60.000",
                "opex": "€25.000",
                "net_result": "€10.000",
            },
        )

        metrics = {m["metric_id"]: m for m in analysis["metrics"]}
        self.assertEqual(metrics["gross_profit"]["value"], "€40.000")
        self.assertEqual(metrics["gross_margin_pct"]["value"], "40,0%")
        self.assertEqual(metrics["net_margin_pct"]["value"], "10,0%")
        self.assertTrue(analysis["forecast_allowed"])

    def test_forecast_is_suppressed_when_deterministic_basis_is_insufficient(self):
        analysis = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {"revenue_current": "€100.000", "revenue_previous": ""},
        )
        result = {
            "report_type": "sales",
            "kpis": [],
            "alerts": [],
            "forecast": {"next_period": "Maio", "revenue_estimate": "€120.000", "confidence": "medium"},
        }

        enhanced = main.validate_and_enhance_report_result(result, "sales", analysis, "EUR")

        self.assertTrue(enhanced["forecast"]["suppressed"])
        self.assertIn("forecast_basis", enhanced["missing_data"])

    def test_efficiency_deterministic_analysis_identifies_lowest_output_ratio(self):
        analysis = main.build_deterministic_analysis(
            "efficiency",
            "EUR",
            {
                "team_data": "Equipa A: 160h, 80 unidades, custo €3.200\nEquipa B: 160h, 40 unidades, custo €3.200",
                "output_metric": "unidades",
            },
        )

        self.assertIn("Equipa B", analysis["recommendations"][0]["title"])
        self.assertEqual(analysis["recommendations"][0]["confidence"], "high")

    def test_frontend_renders_decision_first_sections_and_stable_metric_ids(self):
        html = Path("index.html").read_text()
        self.assertIn("Top decisões recomendadas", html)
        self.assertIn("priority_score", html)
        self.assertIn("preferredMetricByModule", html)
        self.assertIn("gross_margin_pct", html)

    def test_priority_score_uses_relative_impact_for_small_company(self):
        rec = main.build_recommendation(
            title="Pequena empresa",
            why="Impacto relativo alto",
            evidence=[],
            action="Agir",
            impact_amount=5_000,
            impact_base_amount=20_000,
            urgency="medium",
            confidence="medium",
            effort="medium",
        )

        self.assertEqual(main.calculate_impact_score(5_000, 20_000), 100)
        self.assertEqual(rec["priority_score"], 74)

    def test_priority_score_downranks_large_company_relative_small_impact(self):
        small = main.build_recommendation(
            title="Pequena empresa", why="", evidence=[], action="",
            impact_amount=5_000, impact_base_amount=20_000,
            urgency="medium", confidence="medium", effort="medium",
        )
        large = main.build_recommendation(
            title="Grande empresa", why="", evidence=[], action="",
            impact_amount=50_000, impact_base_amount=10_000_000,
            urgency="medium", confidence="medium", effort="medium",
        )

        self.assertGreater(small["priority_score"], large["priority_score"])
        self.assertEqual(main.calculate_impact_score(50_000, 10_000_000), 2)

    def test_priority_score_uses_conservative_absolute_fallback_without_denominator(self):
        self.assertEqual(main.calculate_impact_score(5_000, None), 50)
        rec = main.build_recommendation(
            title="Sem denominador", why="", evidence=[], action="",
            impact_amount=5_000,
            urgency="medium", confidence="medium", effort="medium",
        )
        self.assertEqual(rec["priority_score"], 56)

    def test_priority_score_zero_impact_has_zero_impact_component(self):
        self.assertEqual(main.calculate_impact_score(0, 100_000), 0)
        rec = main.build_recommendation(
            title="Sem impacto", why="", evidence=[], action="",
            impact_amount=0, impact_base_amount=100_000,
            urgency="medium", confidence="medium", effort="medium",
        )
        self.assertEqual(rec["priority_score"], 39)

    def test_priority_score_caps_extreme_relative_and_absolute_values(self):
        self.assertEqual(main.calculate_impact_score(1_000_000, 1_000), 100)
        self.assertEqual(main.calculate_impact_score(1_000_000, None), 100)

    def test_consistency_no_alert_when_revenue_equals_client_sum(self):
        analysis = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {
                "revenue_current": "€100.000",
                "top_clients": "Cliente A €60.000; Cliente B €40.000",
            },
        )

        self.assertEqual(analysis["data_quality_issues"], [])

    def test_consistency_allows_small_rounding_difference(self):
        analysis = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {
                "revenue_current": "€100.000",
                "top_clients": "Cliente A €50.500; Cliente B €50.400",
            },
        )

        self.assertEqual(analysis["data_quality_issues"], [])

    def test_consistency_alerts_when_client_sum_exceeds_revenue(self):
        analysis = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {
                "revenue_current": "€100.000",
                "top_clients": "Cliente A €80.000; Cliente B €50.000; Cliente C €20.000",
            },
        )

        self.assertEqual(len(analysis["data_quality_issues"]), 1)
        issue = analysis["data_quality_issues"][0]
        self.assertEqual(issue["type"], "component_sum_exceeds_total")
        self.assertEqual(issue["severity"], "critical")
        self.assertEqual(issue["observed"], "€150.000")
        self.assertEqual(issue["difference"], "€50.000")
        self.assertIn("acima de receita total €100.000", issue["evidence"])

    def test_consistency_alerts_when_cost_categories_exceed_explicit_total(self):
        analysis = main.build_deterministic_analysis(
            "cost",
            "EUR",
            {
                "total_costs": "€4.000",
                "op_costs": "Renda €3.000; Eletricidade €1.500",
            },
        )

        self.assertEqual(len(analysis["data_quality_issues"]), 1)
        issue = analysis["data_quality_issues"][0]
        self.assertEqual(issue["type"], "component_sum_exceeds_total")
        self.assertEqual(issue["severity"], "warning")
        self.assertEqual(issue["observed"], "€4.500")
        self.assertEqual(issue["difference"], "€500")

    def test_consistency_partial_data_does_not_assume_issue(self):
        no_components = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {"revenue_current": "€100.000"},
        )
        no_total = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {"top_clients": "Cliente A €150.000"},
        )

        self.assertEqual(no_components["data_quality_issues"], [])
        self.assertEqual(no_total["data_quality_issues"], [])

    def test_consistency_partial_top_components_below_total_are_not_issue(self):
        analysis = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {
                "revenue_current": "€100.000",
                "top_clients": "Cliente A €35.000; Cliente B €25.000",
            },
        )

        self.assertEqual(analysis["data_quality_issues"], [])

    def test_consistency_issue_does_not_create_causal_explanation(self):
        analysis = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {
                "revenue_current": "€100.000",
                "top_clients": "Cliente A €150.000",
            },
        )

        self.assertEqual(analysis["recommendations"], [])
        self.assertTrue(analysis["data_quality_issues"])
        self.assertFalse(analysis["forecast_allowed"])
        self.assertNotIn("causa", " ".join(analysis["evidence"]).lower())

    def test_contradictory_data_suppresses_forecast_and_downgrades_confidence(self):
        analysis = main.build_deterministic_analysis(
            "sales",
            "EUR",
            {
                "revenue_current": "€100.000",
                "revenue_previous": "€90.000",
                "top_clients": "Cliente A €150.000",
            },
        )
        result = {
            "report_type": "sales",
            "kpis": [],
            "alerts": [],
            "data_quality": "good",
            "forecast": {"next_period": "Maio", "revenue_estimate": "€120.000", "confidence": "high"},
            "recommendations": [
                {
                    "title": "Acelerar crescimento",
                    "recommended_action": "Investir mais",
                    "confidence": "high",
                    "priority_score": 80,
                }
            ],
        }

        enhanced = main.validate_and_enhance_report_result(result, "sales", analysis, "EUR")

        self.assertEqual(enhanced["data_quality"], "partial")
        self.assertTrue(enhanced["forecast"]["suppressed"])
        self.assertIn("forecast_basis", enhanced["missing_data"])
        self.assertTrue(enhanced["data_quality_issues"])
        self.assertTrue(enhanced["alerts"][0]["message"].startswith("Inconsistência nos dados:"))
        self.assertTrue(all(rec.get("confidence") != "high" for rec in enhanced["recommendations"]))


    def test_anthropic_analysis_has_sufficient_output_budget(self):
        source = Path("main.py").read_text()
        self.assertIn("max_tokens=6000", source)

    def test_anthropic_truncation_is_handled_explicitly(self):
        source = Path("main.py").read_text()
        self.assertIn('getattr(response, "stop_reason", None) == "max_tokens"', source)
        self.assertIn("A análise excedeu o limite de resposta do modelo", source)



if __name__ == "__main__":
    unittest.main()
