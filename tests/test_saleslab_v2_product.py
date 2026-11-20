import asyncio
import io
import json
import unittest
import zipfile
from uuid import uuid4

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from product_fixtures import csv_content, upload_case

from saleslab_v2.config import V2Settings
from saleslab_v2.product.api import choices_from_json
from saleslab_v2.product.contracts import GenerateChoices, InvalidUpload, UploadChoices
from saleslab_v2.product.ingestion import HEADERS, MAX_BYTES, parse_upload
from saleslab_v2.product.limits import FinancialUploadLimit
from saleslab_v2.product.planning import prepare_upload


class ProductUploadTests(unittest.TestCase):
    def test_explicit_sign_convention_rejects_source_digits_the_parser_would_round(
        self,
    ):
        from decimal import localcontext

        choices = UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            accounting_parentheses=True,
        )
        content = csv_content(
            changes={
                "net_income": {"amount": "(123456789012345678901234567890.123456789)"}
            }
        )
        with localcontext() as context:
            context.prec = 28
            with self.assertRaisesRegex(InvalidUpload, "supported precision"):
                upload_case(choices=choices, content=content)
        with localcontext() as context:
            context.prec = 6
            with self.assertRaisesRegex(InvalidUpload, "supported precision"):
                upload_case(
                    choices=choices,
                    content=csv_content(
                        changes={"net_income": {"amount": "(1234567.89)"}}
                    ),
                )
        with localcontext() as context:
            context.prec = 100
            _, preview, _, _, _ = upload_case(choices=choices, content=content)
            value = next(
                r["value"] for r in preview["rows"] if r["concept_id"] == "net_income"
            )
            self.assertEqual(value, "-123456789012345678901234567890.123456789")

    def test_parentheses_require_explicit_convention_and_keep_original_values(self):
        from decimal import Decimal

        hashes, fingerprints, source_hashes = set(), set(), set()
        content = csv_content(changes={"net_income": {"amount": "(100)"}})
        workspace, user = uuid4(), uuid4()
        for convention, expected in ((None, None), (True, "-10.0"), (False, "10.0")):
            choices = UploadChoices(
                source_language="en",
                format_locale="en-GB",
                report_language="pt",
                accounting_parentheses=convention,
            )
            parsed, preview, _, _, _ = upload_case(
                workspace, user, choices=choices, content=content
            )
            request, _ = prepare_upload(parsed, choices, user, "preview")
            hashes.add(preview["preview_hash"])
            fingerprints.add(request.fingerprint)
            source_hashes.add(preview["source_hash"])
            with self.subTest(convention=convention):
                self.assertIs(parsed.batch.metadata.accounting_parentheses, convention)
                self.assertEqual(parsed.original_rows[1]["amount"], "(100)")
                normalized = next(
                    r for r in preview["rows"] if r["concept_id"] == "net_income"
                )
                self.assertEqual(normalized["original_text"], "(100)")
                self.assertEqual(preview["ready"], convention is not None)
                if convention is None:
                    self.assertNotIn("net_margin_pct", preview["planned_metrics"])
                    self.assertIn(
                        "number.parentheses_sign_ambiguous",
                        [i["code"] for i in normalized["issues"]],
                    )
                    self.assertNotIn(
                        "accounting_parentheses", choices.contract_payload()
                    )
                else:
                    margin = next(
                        m
                        for m in preview["calculated_metrics"]
                        if m["metric_id"] == "net_margin_pct"
                    )
                    self.assertEqual(margin["exact_value"], expected)
                    self.assertIs(
                        choices.contract_payload()["accounting_parentheses"], convention
                    )
                    self.assertEqual(
                        Decimal(normalized["value"]),
                        Decimal(-100 if convention else 100),
                    )
        self.assertEqual(len(hashes), 3)
        self.assertEqual(len(fingerprints), 3)
        self.assertEqual(len(source_hashes), 1)

    def test_parenthesized_capex_uses_existing_parser_for_all_number_locales(self):
        for language, locale, amount in (
            ("pt", "pt-PT", "(1.234,56)"),
            ("en", "en-GB", "(1,234.56)"),
            ("es", "es-ES", "(1.234,56)"),
            ("fr", "fr-FR", "(1 234,56)"),
        ):
            choices = UploadChoices(
                source_language="en",
                format_locale=locale,
                report_language=language,
                planning_version="3",
                accounting_parentheses=True,
            )
            rows = [
                (
                    "cash flow from operations",
                    "2000",
                    "2026",
                    "cash_flow_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                ),
                (
                    "purchases of property plant and equipment",
                    amount,
                    "2026",
                    "cash_flow_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                ),
            ]
            _, preview, _, _, _ = upload_case(
                choices=choices, content=csv_content(extra=rows)
            )
            with self.subTest(locale=locale):
                self.assertTrue(preview["ready"])
                fcf = next(
                    m
                    for m in preview["calculated_metrics"]
                    if m["metric_id"] == "free_cash_flow"
                )
                self.assertEqual(fcf["exact_value"], "765.44")
                purchase = fcf["calculation_evidence"]["inputs"][1]
                self.assertEqual(purchase["value"], "-1234.56")
                self.assertEqual(purchase["sources"][0]["amount"], amount)

    def test_parentheses_option_is_strict_and_does_not_flip_written_signs(self):
        base = {
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
        }
        for invalid in (1, 0, "true", "false", [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(InvalidUpload):
                choices_from_json(
                    json.dumps({**base, "accounting_parentheses": invalid}),
                    UploadChoices,
                )
        for amount, expected in (("-100", "-10.0"), ("100", "10.0")):
            _, preview, _, _, _ = upload_case(
                choices=UploadChoices(**base, accounting_parentheses=True),
                content=csv_content(changes={"net_income": {"amount": amount}}),
            )
            margin = next(
                m
                for m in preview["calculated_metrics"]
                if m["metric_id"] == "net_margin_pct"
            )
            self.assertTrue(preview["ready"])
            self.assertEqual(margin["exact_value"], expected)

    def test_metric_availability_is_limited_to_the_selected_plan_and_report_language(
        self,
    ):
        for language in ("pt", "en", "es", "fr"):
            for version, count in (("1", 4), ("2", 5), ("3", 6)):
                choices = UploadChoices(
                    source_language="en",
                    format_locale="en-GB",
                    report_language=language,
                    planning_version=version,
                )
                _, preview, _, _, _ = upload_case(choices=choices)
                rows = {m["metric_id"]: m for m in preview["metric_availability"]}
                with self.subTest(language=language, version=version):
                    self.assertTrue(preview["ready"])
                    self.assertEqual(len(rows), count)
                    self.assertEqual(rows["revenue"]["status"], "calculated")
                    self.assertEqual(rows["revenue"]["requirements"], [])
                    self.assertEqual(
                        rows["cfo"]["requirements"][0]["status"], "missing"
                    )
                    self.assertNotEqual(rows["cfo"]["label"], "cfo")
                    self.assertEqual("free_cash_flow" in rows, version == "3")
                    if version == "3":
                        capex = next(
                            r
                            for r in rows["free_cash_flow"]["requirements"]
                            if r["concept_id"] == "capex"
                        )
                        self.assertNotEqual(capex["label"], "capex")
                        self.assertEqual(capex["source_references"], [])

    def test_metric_gaps_distinguish_confirmation_ambiguity_and_failed_calculation(
        self,
    ):
        choices = UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            planning_version="3",
        )
        cfo = (
            "cash flow from operations",
            "300",
            "2026",
            "cash_flow_statement",
            "consolidated",
            "EUR",
            "raw",
            "current",
        )
        capex = ("purchases of property plant and equipment", "80", *cfo[2:])
        for rows, status, refs, codes in (
            (
                [cfo, capex],
                "needs_confirmation",
                ["csv:8"],
                ["sign.convention_confirmation_required"],
            ),
            (
                [cfo, (capex[0], "-80", *capex[2:]), (capex[0], "-80", *capex[2:])],
                "ambiguous",
                ["csv:8", "csv:9"],
                ["observation.duplicate_candidate"],
            ),
        ):
            _, preview, _, _, _ = upload_case(
                choices=choices, content=csv_content(extra=rows)
            )
            gap = next(
                m
                for m in preview["metric_availability"]
                if m["metric_id"] == "free_cash_flow"
            )
            with self.subTest(status=status):
                self.assertFalse(preview["ready"])
                self.assertEqual(gap["status"], "not_planned")
                self.assertEqual(len(gap["requirements"]), 1)
                requirement = gap["requirements"][0]
                self.assertEqual(requirement["status"], status)
                self.assertEqual(requirement["source_references"], refs)
                self.assertEqual(requirement["reason_codes"], codes)
        _, preview, _, _, _ = upload_case(
            choices=choices,
            content=csv_content(
                extra=[cfo, (capex[0], "-80", *capex[2:5], "USD", *capex[6:])]
            ),
        )
        gap = next(
            m
            for m in preview["metric_availability"]
            if m["metric_id"] == "free_cash_flow"
        )
        self.assertEqual(gap["status"], "blocked")
        self.assertEqual(gap["requirements"], [])
        self.assertEqual(gap["reason_codes"], ["pipeline.metric_invalid"])
        self.assertFalse(preview["ready"])

    def test_metric_gaps_request_a_target_period_before_inventing_missing_inputs(self):
        extra = [
            (
                "cash flow from operations",
                "300",
                "2025",
                "cash_flow_statement",
                "consolidated",
                "EUR",
                "raw",
                "current",
            )
        ]
        _, preview, _, _, _ = upload_case(content=csv_content(extra=extra))
        self.assertEqual(preview["target_period"], "")
        self.assertTrue(
            all(
                m["status"] == "period_required" and m["requirements"] == []
                for m in preview["metric_availability"]
            )
        )

    def test_growth_gaps_use_the_same_prior_selection_and_wait_for_current_revenue(
        self,
    ):
        prior = (
            "revenue",
            "800",
            "2024",
            "income_statement",
            "consolidated",
            "EUR",
            "raw",
            "comparative",
        )
        _, preview, _, _, _ = upload_case(content=csv_content(extra=[prior]))
        growth = next(
            m
            for m in preview["metric_availability"]
            if m["metric_id"] == "revenue_growth_reported_pct"
        )
        self.assertEqual([r["role"] for r in growth["requirements"]], ["revenue_prior"])
        self.assertEqual(growth["requirements"][0]["status"], "missing")
        self.assertEqual(growth["requirements"][0]["source_references"], [])
        _, preview, _, _, _ = upload_case(
            content=csv_content(
                changes={"revenue": {"line": "unmapped revenue"}}, extra=[prior]
            )
        )
        growth = next(
            m
            for m in preview["metric_availability"]
            if m["metric_id"] == "revenue_growth_reported_pct"
        )
        self.assertEqual(
            growth["requirements"][1]["status"], "current_revenue_required"
        )

    def test_growth_with_incompatible_ytd_span_is_blocked_not_reported_as_missing(self):
        prior = (
            "revenue",
            "800",
            "YTD September 2025",
            "income_statement",
            "consolidated",
            "EUR",
            "raw",
            "comparative",
        )
        _, preview, _, _, _ = upload_case(
            content=csv_content(
                changes={"revenue": {"period": "YTD June 2026"}}, extra=[prior]
            ),
            choices=UploadChoices(
                source_language="en",
                format_locale="en-GB",
                report_language="pt",
                target_period="YTD June 2026",
            ),
        )
        growth = next(
            m
            for m in preview["metric_availability"]
            if m["metric_id"] == "revenue_growth_reported_pct"
        )
        self.assertEqual(growth["status"], "blocked")
        self.assertEqual(growth["requirements"], [])
        self.assertFalse(preview["ready"])

    def test_calculated_availability_never_overrides_a_blocked_reconciliation(self):
        _, preview, _, _, _ = upload_case(
            content=csv_content(changes={"equity": {"amount": "41"}})
        )
        self.assertFalse(preview["ready"])
        rows = {m["metric_id"]: m for m in preview["metric_availability"]}
        self.assertEqual(rows["net_margin_pct"]["status"], "calculated")
        self.assertEqual(rows["net_margin_pct"]["requirements"], [])

    def test_plan_two_is_unchanged_and_plan_three_adds_source_bound_fcf(self):
        from uuid import UUID

        content = csv_content(
            extra=[
                (
                    "EBIT",
                    "200",
                    "2026",
                    "income_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                ),
                (
                    "cash flow from operations",
                    "300",
                    "2026",
                    "cash_flow_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                ),
                (
                    "purchases of property plant and equipment",
                    "-80",
                    "2026",
                    "cash_flow_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                ),
            ]
        )
        choices = UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            planning_version="2",
        )
        w = UUID("10000000-0000-4000-8000-000000000001")
        u = UUID("10000000-0000-4000-8000-000000000002")
        parsed, preview, _, _, _ = upload_case(w, u, content=content, choices=choices)
        request, _ = prepare_upload(parsed, choices, u, "preview")
        self.assertEqual(
            preview["preview_hash"],
            "2db8a6d4c393a3b4538b9106d3bfe1599e24ed7ca469ae10d3b536c3ee131fe4",
        )
        self.assertEqual(
            request.fingerprint,
            "9dd5a11e83b8fefc9e6e7fad20d063ab42c4203dc8375342267a161ccfbb8a2a",
        )
        self.assertNotIn("free_cash_flow", preview["planned_metrics"])
        _, current = prepare_upload(
            parsed, choices.model_copy(update={"planning_version": "3"}), u, "preview"
        )
        self.assertTrue(current["ready"])
        self.assertIn("ebit_margin_pct", current["planned_metrics"])
        fcf = next(
            m
            for m in current["calculated_metrics"]
            if m["metric_id"] == "free_cash_flow"
        )
        self.assertEqual(fcf["exact_value"], "220")
        self.assertEqual(fcf["source_references"], ["csv:8", "csv:9"])
        self.assertEqual(fcf["calculation_evidence"]["metric_definition_version"], "2")
        self.assertEqual(
            [i["value"] for i in fcf["calculation_evidence"]["inputs"]], ["300", "-80"]
        )

    def test_fcf_preview_in_four_languages_preserves_scale_and_negative_cfo(self):
        for language in ("pt", "en", "es", "fr"):
            choices = UploadChoices(
                source_language="en",
                format_locale="en-GB",
                report_language=language,
                planning_version="3",
            )
            rows = [
                (
                    "cash flow from operations",
                    "-1.2",
                    "2026",
                    "cash_flow_statement",
                    "consolidated",
                    "EUR",
                    "millions",
                    "current",
                ),
                (
                    "purchases of property plant and equipment",
                    "-0.4",
                    "2026",
                    "cash_flow_statement",
                    "consolidated",
                    "EUR",
                    "millions",
                    "current",
                ),
            ]
            _, preview, _, _, _ = upload_case(
                choices=choices, content=csv_content(extra=rows)
            )
            fcf = next(
                m
                for m in preview["calculated_metrics"]
                if m["metric_id"] == "free_cash_flow"
            )
            with self.subTest(language=language):
                self.assertTrue(preview["ready"])
                self.assertEqual(fcf["exact_value"], "-1600000.0")
                self.assertNotEqual(fcf["label"], "free_cash_flow")
                self.assertEqual(
                    fcf["calculation_evidence"]["inputs"][1]["sources"][0]["amount"],
                    "-0.4",
                )
                self.assertEqual(
                    fcf["calculation_evidence"]["inputs"][1]["sources"][0]["scale"],
                    "millions",
                )

    def test_fcf_never_assumes_missing_capex_or_changes_a_positive_purchase_sign(self):
        choices = UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            planning_version="3",
            target_period="2026",
        )
        cfo = (
            "cash flow from operations",
            "300",
            "2026",
            "cash_flow_statement",
            "consolidated",
            "EUR",
            "raw",
            "current",
        )
        capex = ("purchases of property plant and equipment", "-80", *cfo[2:])
        for rows, planned, ready, amount in (
            ([cfo], False, True, None),
            ([capex], False, True, None),
            ([cfo, (capex[0], "80", *capex[2:])], False, False, None),
            ([cfo, capex, capex], False, False, None),
            ([cfo, (*capex[:5], "USD", *capex[6:])], True, False, None),
            ([cfo, (*capex[:2], "2025", *capex[3:])], False, True, None),
            ([cfo, (capex[0], "0", *capex[2:])], True, True, "300"),
        ):
            _, preview, _, _, _ = upload_case(
                choices=choices, content=csv_content(extra=rows)
            )
            with self.subTest(rows=rows):
                self.assertEqual(
                    "free_cash_flow" in preview["planned_metrics"], planned
                )
                self.assertEqual(preview["ready"], ready)
                if planned:
                    fcf = next(
                        m
                        for m in preview["calculated_metrics"]
                        if m["metric_id"] == "free_cash_flow"
                    )
                    self.assertEqual(fcf["exact_value"], amount)
                    if amount is None:
                        self.assertIsNone(fcf["calculation_evidence"])

    def test_original_planner_hash_and_serialized_choices_are_preserved(self):
        from uuid import UUID

        content = csv_content(
            extra=[
                (
                    "EBIT",
                    "200",
                    "2026",
                    "income_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                )
            ]
        )
        workspace = UUID("10000000-0000-4000-8000-000000000001")
        user = UUID("10000000-0000-4000-8000-000000000002")
        parsed, preview, generate, _, _ = upload_case(workspace, user, content=content)
        request, _ = prepare_upload(parsed, generate, user, "preview")
        self.assertEqual(preview["planning_version"], "1")
        self.assertEqual(
            preview["preview_hash"],
            "d97e30120c52afc38ee76ba45afc6ddef963630acfb460142a8dde62146e66db",
        )
        self.assertEqual(
            request.fingerprint,
            "1ee8912cbc5ff191ddc39a9935469ea803b758c371b99a79f2ca7a02cf98a14f",
        )
        self.assertNotIn("planning_version", generate.contract_payload())
        self.assertEqual(
            GenerateChoices.model_validate(generate.contract_payload()), generate
        )
        self.assertNotIn("ebit_margin_pct", preview["planned_metrics"])
        newer = UploadChoices(
            **{
                **generate.model_dump(exclude={"preview_hash", "attempt_key"}),
                "planning_version": "2",
            }
        )
        _, upgraded = prepare_upload(parsed, newer, user, "preview")
        self.assertNotEqual(upgraded["preview_hash"], preview["preview_hash"])
        self.assertIn("ebit_margin_pct", upgraded["planned_metrics"])

    def test_version_two_ebit_margin_uses_existing_engine_and_source_in_four_languages(
        self,
    ):
        for language, label in (
            ("pt", "Margem EBIT"),
            ("en", "EBIT margin"),
            ("es", "Margen EBIT"),
            ("fr", "Marge EBIT"),
        ):
            choices = UploadChoices(
                source_language="en",
                format_locale="en-GB",
                report_language=language,
                planning_version="2",
            )
            _, preview, generate, _, _ = upload_case(
                choices=choices,
                content=csv_content(
                    extra=[
                        (
                            "EBIT",
                            "-200",
                            "2026",
                            "income_statement",
                            "consolidated",
                            "EUR",
                            "raw",
                            "current",
                        )
                    ]
                ),
            )
            margin = next(
                m
                for m in preview["calculated_metrics"]
                if m["metric_id"] == "ebit_margin_pct"
            )
            with self.subTest(language=language):
                self.assertTrue(preview["ready"])
                self.assertEqual(preview["planning_version"], "2")
                self.assertEqual(generate.contract_payload()["planning_version"], "2")
                self.assertEqual(margin["label"], label)
                self.assertEqual(margin["exact_value"], "-20.0")
                self.assertEqual(margin["source_references"], ["csv:2", "csv:7"])
                self.assertEqual(
                    margin["calculation_evidence"]["canonical_expression"],
                    "confirmed_ebit/revenue*100",
                )

    def test_ebit_planning_never_selects_ambiguous_or_incompatible_inputs(self):
        choices = UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            planning_version="2",
            target_period="2026",
        )
        base = (
            "EBIT",
            "200",
            "2026",
            "income_statement",
            "consolidated",
            "EUR",
            "raw",
            "current",
        )
        for rows, changes, planned, ready in (
            ([base], {"revenue": {"amount": "0"}}, True, False),
            ([(*base[:5], "USD", *base[6:])], {}, True, False),
            ([base, base], {}, False, False),
            ([("operating profit", *base[1:])], {}, False, False),
            ([(*base[:2], "2025", *base[3:])], {}, False, True),
            ([], {}, False, True),
        ):
            _, preview, _, _, _ = upload_case(
                choices=choices, content=csv_content(changes=changes, extra=rows)
            )
            with self.subTest(rows=rows, changes=changes):
                self.assertEqual(
                    "ebit_margin_pct" in preview["planned_metrics"], planned
                )
                self.assertEqual(preview["ready"], ready)
                if planned:
                    margin = next(
                        m
                        for m in preview["calculated_metrics"]
                        if m["metric_id"] == "ebit_margin_pct"
                    )
                    self.assertEqual(margin["status"], "unavailable")
                    self.assertIsNone(margin["calculation_evidence"])
        with self.assertRaises(InvalidUpload):
            choices_from_json(
                json.dumps({**choices.model_dump(), "planning_version": "9"}),
                UploadChoices,
            )

    def test_validation_summary_preserves_gates_and_explains_requirements(self):
        for language in ("pt", "en", "es", "fr"):
            choices = UploadChoices(
                source_language="en", format_locale="en-GB", report_language=language
            )
            _, preview, _, _, _ = upload_case(choices=choices)
            summary = preview["validation_summary"]
            with self.subTest(language=language):
                self.assertEqual(
                    summary["quality_level"],
                    preview["quality"]["coverage"]["quality_level"],
                )
                for decision, original in zip(
                    summary["capabilities"], preview["quality"]["decisions"]
                ):
                    self.assertEqual(decision["status"], original["decision_status"])
                    self.assertEqual(decision["reason_codes"], original["reason_codes"])
                    self.assertEqual(
                        [r["code"] for r in decision["requirements"]],
                        original["requirements_to_unlock"],
                    )
                    self.assertTrue(
                        all(
                            r["label"] != r["code"] and r["code"] in r["label"]
                            for r in decision["requirements"]
                        )
                    )
                states = {d["capability"]: d["status"] for d in summary["capabilities"]}
                self.assertEqual(states["forecast"], "blocked")
                self.assertEqual(states["calculate"], "allowed")

    def test_reconciliation_preview_preserves_pass_rounding_and_blocking(self):
        for equity, tolerance, status, ready, delta in (
            ("40", "0", "pass", True, "0"),
            ("40.005", "0.01", "rounding_pass", True, "0.005"),
            ("41", "0", "blocking", False, "1"),
        ):
            choices = UploadChoices(
                source_language="en",
                format_locale="en-GB",
                report_language="pt",
                rounding_tolerance=tolerance,
            )
            _, preview, _, _, _ = upload_case(
                content=csv_content(changes={"equity": {"amount": equity}}),
                choices=choices,
            )
            result = preview["validation_summary"]["reconciliations"][0]
            with self.subTest(status=status):
                self.assertEqual(result["status"], status)
                self.assertEqual(result["absolute_delta"], delta)
                self.assertEqual(preview["ready"], ready)
                self.assertEqual(
                    result["display_delta"], delta.replace(".", ",") + " EUR"
                )
                self.assertEqual(
                    set(result["source_references"]), {"csv:4", "csv:5", "csv:6"}
                )
                self.assertEqual(len(result["result_hash"]), 64)

    def test_unexecuted_reconciliation_has_neither_values_nor_pass_status(self):
        _, preview, _, _, _ = upload_case(
            content=csv_content(changes={"revenue": {"line": "unknown account"}})
        )
        result = preview["validation_summary"]["reconciliations"][0]
        self.assertFalse(preview["ready"])
        self.assertEqual(result["status"], "not_evaluated")
        for field in (
            "expected",
            "observed",
            "absolute_delta",
            "display_delta",
            "result_hash",
        ):
            self.assertIsNone(result[field])
        self.assertEqual(result["reason_codes"], ["pipeline.mapping_required"])

    def test_no_balance_data_is_not_a_verified_identity(self):
        from saleslab_v2.product.validation import input_label

        content = csv_content().decode().splitlines()
        _, preview, _, _, _ = upload_case(
            content=("\n".join(content[:3]) + "\n").encode()
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["validation_summary"]["reconciliations"], [])
        self.assertEqual(input_label("future_requirement", "pt"), "future_requirement")

    def test_metric_preview_uses_report_locale_and_preserves_exact_trace_values(self):
        for language, expected in (
            ("pt", "10,00 %"),
            ("en", "10.00 %"),
            ("es", "10,00 %"),
            ("fr", "10,00 %"),
        ):
            choices = UploadChoices(
                source_language="en", format_locale="en-GB", report_language=language
            )
            parsed, preview, _, _, user = upload_case(choices=choices)
            value, repeated = prepare_upload(parsed, choices, user, "preview")
            margin = next(
                m
                for m in preview["calculated_metrics"]
                if m["metric_id"] == "net_margin_pct"
            )
            with self.subTest(language=language):
                self.assertEqual(margin["display_value"], expected)
                self.assertEqual(margin["exact_value"], "10.0")
                evidence = margin["calculation_evidence"]
                self.assertEqual(
                    evidence["canonical_expression"], "net_income/revenue*100"
                )
                self.assertEqual(evidence["metric_definition_version"], "1")
                self.assertEqual(evidence["rounded_output"], "10")
                self.assertEqual(evidence["rounding_policy"], "percentage_2dp")
                self.assertIn("100", evidence["division_policy"])
                self.assertEqual(
                    [i["role"] for i in evidence["inputs"]], ["net_income", "revenue"]
                )
                self.assertEqual(
                    [i["value"] for i in evidence["inputs"]], ["100", "1000"]
                )
                self.assertEqual(
                    evidence["inputs"][0]["sources"][0]["line"], "net income"
                )
                self.assertEqual(margin["status"], "calculated")
                self.assertEqual(margin["period"]["label"], "2026")
                self.assertEqual(set(margin["source_references"]), {"csv:2", "csv:3"})
                self.assertIsNotNone(margin["calculation_trace_id"])
                self.assertEqual(
                    repeated["calculated_metrics"], preview["calculated_metrics"]
                )
                self.assertEqual(
                    sorted(preview["planned_metrics"]),
                    sorted(s.metric_id for s in value.metrics),
                )

    def test_failed_calculation_has_no_preview_value_and_does_not_unlock_generation(
        self,
    ):
        _, preview, _, _, _ = upload_case(
            content=csv_content(changes={"revenue": {"amount": "0"}})
        )
        margin = next(
            m
            for m in preview["calculated_metrics"]
            if m["metric_id"] == "net_margin_pct"
        )
        self.assertFalse(preview["ready"])
        self.assertEqual(margin["status"], "unavailable")
        self.assertIsNone(margin["display_value"])
        self.assertIsNone(margin["exact_value"])
        self.assertIsNone(margin["calculation_trace_id"])
        self.assertIsNone(margin["calculation_evidence"])
        self.assertEqual(margin["reason_codes"], ["pipeline.metric_invalid"])

    def test_evidence_preserves_original_mapped_labels_scale_and_period_roles(self):
        from saleslab_v2.product.pilot import sample

        case = sample()
        choices = UploadChoices(**case["choices"])
        parsed, preview, _, _, user = upload_case(
            content=case["content"].encode(), choices=choices, filename=case["filename"]
        )
        metrics = {m["metric_id"]: m for m in preview["calculated_metrics"]}
        evidence = metrics["revenue_growth_reported_pct"]["calculation_evidence"]
        current, prior = evidence["inputs"]
        self.assertEqual(current["role"], "revenue_current")
        self.assertEqual(prior["role"], "revenue_prior")
        self.assertEqual(current["value"], "33464000000")
        self.assertEqual(prior["value"], "30608000000")
        self.assertEqual(current["period"][0:2], ["2024-01-01", "2024-12-31"])
        self.assertEqual(prior["period"][0:2], ["2023-01-01", "2023-12-31"])
        for operand in (current, prior):
            original = next(
                row
                for row in parsed.original_rows
                if row["location"] == operand["source_references"][0]
            )
            self.assertEqual(operand["sources"], [original])
            self.assertEqual(original["scale"], "millions")
            self.assertNotEqual(original["line"], "revenue")
        # The projection exposes the engine's trace, not a second implementation.
        from datetime import UTC, datetime

        from saleslab_v2.pipeline.evaluator import evaluate

        request, _ = prepare_upload(parsed, choices, user, "preview")
        evaluated = evaluate(request, uuid4(), datetime.now(UTC))
        for key, _, trace in evaluated.metrics:
            projected = next(m for m in metrics.values() if m["key"] == key)
            self.assertEqual(
                projected["calculation_evidence"]["trace_hash"], trace.trace_hash
            )
            self.assertEqual(
                projected["calculation_evidence"]["exact_output"],
                trace.payload["exact_output"],
            )

    def test_evidence_retains_precise_source_decimals_and_disappears_when_mapping_blocks(
        self,
    ):
        amount = "123456789012345678901234567890.123456789"
        _, preview, _, _, _ = upload_case(
            content=csv_content(changes={"revenue": {"amount": amount}})
        )
        evidence = next(
            m for m in preview["calculated_metrics"] if m["metric_id"] == "revenue"
        )["calculation_evidence"]
        self.assertEqual(evidence["inputs"][0]["value"], amount)
        self.assertEqual(evidence["inputs"][0]["sources"][0]["amount"], amount)
        self.assertEqual(evidence["exact_output"], amount)
        self.assertIsNone(evidence["rounding_policy"])
        _, blocked, _, _, _ = upload_case(
            content=csv_content(changes={"net_income": {"line": "unmapped account"}})
        )
        self.assertFalse(blocked["ready"])
        self.assertTrue(
            all(
                m["calculation_evidence"] is None for m in blocked["calculated_metrics"]
            )
        )

    def test_valid_preview_metrics_do_not_override_failed_balance_reconciliation(self):
        _, preview, _, _, _ = upload_case(
            content=csv_content(changes={"equity": {"amount": "41"}})
        )
        self.assertFalse(preview["ready"])
        self.assertTrue(
            all(m["status"] == "calculated" for m in preview["calculated_metrics"])
        )

    def test_template_validation_and_preview_binding_in_all_four_languages(self):
        for language, locale in (
            ("pt", "pt-PT"),
            ("en", "en-GB"),
            ("es", "es-ES"),
            ("fr", "fr-FR"),
        ):
            with self.subTest(language=language):
                choices = UploadChoices(
                    source_language=language,
                    format_locale=locale,
                    report_language=language,
                )
                parsed, preview, generate, _, user_id = upload_case(
                    content=csv_content(language=language), choices=choices
                )
                self.assertTrue(preview["ready"])
                self.assertEqual(
                    preview["planned_metrics"], ["revenue", "net_margin_pct"]
                )
                _, repeated = prepare_upload(
                    parsed, generate, user_id, generate.attempt_key
                )
                self.assertEqual(preview["preview_hash"], repeated["preview_hash"])
                _, explicit = prepare_upload(
                    parsed,
                    generate.model_copy(
                        update={"target_period": preview["target_period"]}
                    ),
                    user_id,
                    generate.attempt_key,
                )
                self.assertEqual(preview["preview_hash"], explicit["preview_hash"])

    def test_explicit_mapping_preserves_original_and_must_match_statement(self):
        content = csv_content(changes={"revenue": {"line": "unmapped revenue heading"}})
        _, preview, _, _, _ = upload_case(content=content)
        self.assertFalse(preview["ready"])
        choices = UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            mappings={"csv:2": "revenue"},
        )
        parsed, preview, _, _, _ = upload_case(content=content, choices=choices)
        self.assertTrue(preview["ready"])
        self.assertEqual(parsed.original_rows[0]["line"], "unmapped revenue heading")
        self.assertEqual(parsed.decisions, {"csv:2": "revenue"})
        for mappings in ({"csv:2": "total_assets"}, {"csv:999": "revenue"}):
            with self.subTest(mappings=mappings), self.assertRaises(InvalidUpload):
                upload_case(
                    content=content,
                    choices=choices.model_copy(update={"mappings": mappings}),
                )

    def test_changed_choices_file_or_filename_changes_preview_hash(self):
        parsed, preview, choices, content, user_id = upload_case()
        for change in ({"report_language": "en"}, {"rounding_tolerance": "0.01"}):
            _, other = prepare_upload(
                parsed, choices.model_copy(update=change), user_id, choices.attempt_key
            )
            self.assertNotEqual(preview["preview_hash"], other["preview_hash"])
        renamed = parse_upload(
            parsed.batch.document.workspace_id, "another.csv", content, choices
        )
        _, other = prepare_upload(renamed, choices, user_id, choices.attempt_key)
        self.assertNotEqual(preview["preview_hash"], other["preview_hash"])

    def test_multiple_current_periods_require_selection_and_never_aggregate(self):
        extra = [
            (
                "revenue",
                "500",
                "2025",
                "income_statement",
                "consolidated",
                "EUR",
                "raw",
                "current",
            )
        ]
        parsed, preview, choices, _, user_id = upload_case(
            content=csv_content(extra=extra)
        )
        self.assertFalse(preview["ready"])
        _, selected = prepare_upload(
            parsed,
            choices.model_copy(update={"target_period": "2026"}),
            user_id,
            choices.attempt_key,
        )
        self.assertTrue(selected["ready"])

    def test_zero_denominator_and_currency_conflict_block_without_guessed_values(self):
        for changes in (
            {"revenue": {"amount": "0"}},
            {"net_income": {"currency": "USD"}},
        ):
            with self.subTest(changes=changes):
                _, preview, _, _, _ = upload_case(content=csv_content(changes=changes))
                self.assertFalse(preview["ready"])
                self.assertIn(
                    "pipeline.metric_invalid", {i["code"] for i in preview["issues"]}
                )

    def test_balance_tolerance_is_explicit_and_strict_by_default(self):
        content = csv_content(changes={"total_assets": {"amount": "100.005"}})
        _, preview, choices, _, _ = upload_case(content=content)
        self.assertFalse(preview["ready"])
        base = UploadChoices(
            **choices.model_dump(exclude={"preview_hash", "attempt_key"})
        ).model_copy(update={"rounding_tolerance": "0.01"})
        _, tolerant, _, _, _ = upload_case(content=content, choices=base)
        self.assertTrue(tolerant["ready"])

    def test_unknown_or_client_supplied_identity_fields_and_duplicate_json_are_rejected(
        self,
    ):
        values = {
            "source_language": "en",
            "format_locale": "en-GB",
            "report_language": "pt",
        }
        for raw in (
            json.dumps({**values, "workspace_id": str(uuid4())}),
            '{"source_language":"en","source_language":"pt"}',
            "NaN",
            json.dumps({**values, "rounding_tolerance": "NaN"}),
            json.dumps({**values, "rounding_tolerance": "1e999999999"}),
            json.dumps({**values, "rounding_tolerance": "1e-999999999"}),
        ):
            with self.subTest(raw=raw), self.assertRaises(InvalidUpload):
                choices_from_json(raw, UploadChoices)
        self.assertFalse(V2Settings.from_mapping({}).financial_product_enabled)

    def test_csv_limits_encoding_headers_scope_and_ragged_rows(self):
        choices = UploadChoices(
            source_language="en", format_locale="en-GB", report_language="pt"
        )
        for filename, content in (
            ("file.pdf", b"%PDF"),
            ("file.csv", b"\xff"),
            ("file.csv", b"line;amount\na;1\n"),
            ("file.csv", b"x" * (MAX_BYTES + 1)),
            ("file.csv", csv_content(changes={"revenue": {"scope": "segment"}})),
            (
                "file.csv",
                csv_content(changes={"revenue": {"statement_type": "cash_flow"}}),
            ),
            (
                "file.csv",
                csv_content(changes={"revenue": {"comparison_basis": "unknown"}}),
            ),
            ("file.csv", csv_content() + b"only;two\n"),
            ("file.csv", csv_content() + (b";".join([b"x"] * 8) + b"\n") * 500),
        ):
            with self.subTest(filename=filename, size=len(content)), self.assertRaises(
                InvalidUpload
            ):
                parse_upload(uuid4(), filename, content, choices)

    def workbook(self, *, formula=False, second_sheet=False, extra_column=False):
        book = Workbook()
        sheet = book.active
        sheet.append(HEADERS)
        for row in csv_content().decode().splitlines()[1:]:
            values = row.split(";")
            values[1] = float(values[1])
            sheet.append(values)
        if formula:
            sheet["B2"] = "=1+1"
        if second_sheet:
            book.create_sheet("unread")
        if extra_column:
            sheet["Z2"] = "undisclosed data"
        buffer = io.BytesIO()
        book.save(buffer)
        return buffer.getvalue()

    def test_xlsx_numeric_cells_are_normalized_in_the_explicit_locale(self):
        choices = UploadChoices(
            source_language="en", format_locale="pt-PT", report_language="pt"
        )
        _, preview, _, _, _ = upload_case(
            content=self.workbook(), choices=choices, filename="report.xlsx"
        )
        self.assertTrue(preview["ready"])
        self.assertEqual(
            next(r["value"] for r in preview["rows"] if r["concept_id"] == "revenue"),
            "1000",
        )

    def test_xlsx_formulas_extra_sheets_columns_and_false_dimensions_are_rejected(self):
        for kwargs in (
            {"formula": True},
            {"second_sheet": True},
            {"extra_column": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidUpload):
                upload_case(content=self.workbook(**kwargs), filename="report.xlsx")
        source = self.workbook(extra_column=True)
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(source)) as archive, zipfile.ZipFile(
            output, "w"
        ) as target:
            for member in archive.infolist():
                data = archive.read(member)
                if member.filename == "xl/worksheets/sheet1.xml":
                    data = data.replace(b'ref="A1:Z6"', b'ref="A1:H6"')
                target.writestr(member, data)
        with self.assertRaises(InvalidUpload):
            upload_case(content=output.getvalue(), filename="report.xlsx")

    def test_sparse_workbook_coordinates_are_bounded_before_gap_expansion(self):
        source = self.workbook()
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(source)) as archive, zipfile.ZipFile(
            output, "w"
        ) as target:
            for member in archive.infolist():
                data = archive.read(member)
                if member.filename == "xl/worksheets/sheet1.xml":
                    data = data.replace(b'r="6"', b'r="1048576"')
                target.writestr(member, data)
        with self.assertRaises(InvalidUpload):
            upload_case(content=output.getvalue(), filename="report.xlsx")

    def test_request_limit_handles_declared_and_chunked_bodies(self):
        app = FastAPI()
        app.add_middleware(FinancialUploadLimit)

        @app.post("/financial-v2/preview")
        async def route(request: Request):
            await request.body()
            return {"ok": True}

        async def scenario():
            async with AsyncClient(
                transport=ASGITransport(app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/financial-v2/preview",
                    content=b"x",
                    headers={"Content-Length": str(7 * 1024 * 1024)},
                )
                self.assertEqual(response.status_code, 413)

                async def chunks():
                    for _ in range(7):
                        yield b"x" * (1024 * 1024)

                response = await client.post("/financial-v2/preview", content=chunks())
                self.assertEqual(response.status_code, 413)

        asyncio.run(scenario())

    def test_production_middleware_keeps_cors_on_early_upload_rejection(self):
        from main import ALLOWED_ORIGINS, app

        async def scenario():
            async with AsyncClient(
                transport=ASGITransport(app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/financial-v2/preview",
                    content=b"x",
                    headers={
                        "Content-Length": str(7 * 1024 * 1024),
                        "Origin": ALLOWED_ORIGINS[0],
                    },
                )
                self.assertEqual(response.status_code, 413)
                self.assertEqual(
                    response.headers["access-control-allow-origin"], ALLOWED_ORIGINS[0]
                )
                self.assertIn("request limit", response.json()["detail"])

        asyncio.run(scenario())
