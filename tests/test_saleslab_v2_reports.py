import base64
import json
import unittest
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from report_fixtures import report_fixture

from saleslab_v2.config import V2Settings
from saleslab_v2.reports.compiler import compile_report, verify_approval
from saleslab_v2.reports.contracts import InvalidReport, ReportParityError, ReportResult
from saleslab_v2.reports.presentation import (
    document,
    finding_text,
    format_value,
    manifest,
)
from saleslab_v2.reports.render import (
    render_channels,
    render_html,
    render_pdf,
    sha256,
    verify_artifacts,
    verify_channels,
)


class ReportTests(unittest.TestCase):
    def test_reader_requirements_are_localized_lossless_and_share_web_pdf_content(self):
        from saleslab_v2.reports.requirements import (
            HEADINGS,
            REQUIREMENTS,
            requirement_text,
        )

        for language in ("pt", "en", "es", "fr"):
            report, _ = report_fixture(language, warning=True)
            report["render_version"] = "financial-render-v3"
            original = deepcopy(report)
            blocks = {b.key: b.text for b in document(report)}
            self.assertEqual(blocks["missing"], HEADINGS[language])
            self.assertIn("missing/context", blocks)
            for item in report["missing_data_requests"]:
                self.assertEqual(
                    blocks["missing/" + item["request_id"]],
                    requirement_text(item["requirement_code"], language),
                )
            for code in REQUIREMENTS:
                self.assertIn(code, requirement_text(code, language))
                self.assertNotEqual(code, requirement_text(code, language))
            self.assertEqual(
                requirement_text("future_requirement", language), "future_requirement"
            )
            verify_artifacts(report, render_channels(report))
            self.assertEqual(report, original)

    def test_v2_reading_and_critic_preview_retain_their_original_contract(self):
        from saleslab_v2.audit.context import presentation_preview

        report, data = report_fixture("pt", warning=True)
        report["render_version"] = "financial-render-v2"
        blocks = {b.key: b.text for b in document(report)}
        self.assertEqual(blocks["missing"], "Dados em falta")
        self.assertNotIn("missing/context", blocks)
        _, _, context, _, draft, _, _ = data
        self.assertEqual(
            presentation_preview(draft, context)["render_version"],
            "financial-render-v2",
        )

    def test_new_renderer_uses_verified_rounding_and_preserves_exact_appendix(self):
        from saleslab_v2.reports.contracts import REPORT_LOCALES

        for language, locale in REPORT_LOCALES.items():
            report, data = report_fixture(language)
            run, stages, context, _, draft, audit, _ = data
            current = compile_report(
                run=run,
                context=context,
                draft=draft,
                audit=audit,
                checkpoint=stages["checkpoint"].payload,
                generated_at=datetime.fromisoformat(report["generated_at"]),
                locale=locale,
            )
            with self.subTest(language=language):
                self.assertEqual(current["render_version"], "financial-render-v3")
                self.assertEqual(
                    current["metric_observations"], report["metric_observations"]
                )
                self.assertEqual(current["findings"], report["findings"])
                blocks = {b.key: b.text for b in document(current)}
                self.assertIn("rounding", blocks)
                growth = next(
                    f for f in current["findings"] if f["capability"] == "compare"
                )
                self.assertIn(
                    "25.00 %" if language == "en" else "25,00 %",
                    blocks["finding/" + growth["finding_id"] + "/statement"],
                )
                verify_artifacts(current, render_channels(current))

    def test_public_margin_display_uses_trace_policy_without_losing_canonical_precision(
        self,
    ):
        from uuid import uuid4

        from analyst_fixtures import context_fixture

        from saleslab_v2.product.contracts import UploadChoices
        from saleslab_v2.product.ingestion import parse_upload
        from saleslab_v2.product.pilot import sample
        from saleslab_v2.product.planning import prepare_upload

        case = sample()
        choices = UploadChoices(**case["choices"])
        parsed = parse_upload(
            uuid4(), case["filename"], case["content"].encode(), choices
        )
        request, _ = prepare_upload(parsed, choices, uuid4(), str(uuid4()))
        _, _, _, context = context_fixture(request)
        evidence = next(
            e for e in context["evidence"] if e["concept_id"] == "net_margin_pct"
        )
        original = deepcopy(evidence)
        self.assertEqual(format_value(evidence, "pt-PT", trace_rounding=True), "1,81 %")
        self.assertGreater(len(format_value(evidence, "pt-PT")), 90)
        self.assertEqual(evidence, original)
        for key, value in (
            ("rounded_output", "1.82"),
            ("rounding_version", "other"),
            ("rounding_policy", "unknown"),
        ):
            bad = deepcopy(evidence)
            bad["trace"][key] = value
            with self.subTest(key=key), self.assertRaises(InvalidReport):
                format_value(bad, "pt-PT", trace_rounding=True)

    def test_quality_limitations_and_independent_audit_warnings_keep_distinct_labels(
        self,
    ):
        from saleslab_v2.reports.presentation import LABELS

        for language in ("pt", "en", "es", "fr"):
            with self.subTest(language=language):
                report, _ = report_fixture(language, warning=True)
                report["limitations"].append(
                    {
                        "limitation_id": "quality-fixture",
                        "kind": "quality_issue",
                        "detail": {"message_key": "pipeline.reconciliation_warning"},
                    }
                )
                blocks = {b.key: b.text for b in document(report)}
                quality = blocks["limitation/quality-fixture"]
                self.assertEqual(
                    quality,
                    LABELS[language]["quality_warning"]
                    + ": pipeline.reconciliation_warning",
                )
                self.assertNotIn(LABELS[language]["warning"] + ":", quality)
                audit_key = "limitation/" + report["limitations"][0]["limitation_id"]
                self.assertTrue(
                    blocks[audit_key].startswith(LABELS[language]["warning"] + ":")
                )
                artifacts = render_channels(report)
                verify_artifacts(report, artifacts)

    def test_jsonb_key_reordering_preserves_document_html_pdf_and_artifact_replay(self):
        report, _ = report_fixture(warning=True)

        def reorder(value):
            if isinstance(value, dict):
                return {
                    k: reorder(value[k])
                    for k in sorted(value, key=lambda k: (len(k), k))
                }
            if isinstance(value, list):
                return [reorder(item) for item in value]
            return value

        stored = reorder(report)
        self.assertEqual(stored, report)
        self.assertNotEqual(list(stored), list(report))
        self.assertEqual(document(report), document(stored))
        artifacts = render_channels(report)
        self.assertEqual(render_html(stored), artifacts["html"])
        self.assertEqual(render_pdf(stored), base64.b64decode(artifacts["pdf_base64"]))
        verify_artifacts(stored, reorder(artifacts))

    def test_golden_document_and_raster_layout_in_four_languages(self):
        from report_visual import page_fingerprints

        from saleslab_v2.domain.idempotency import semantic_hash

        golden = json.loads(
            (
                Path(__file__).parent / "fixtures/saleslab_v2/report_goldens.json"
            ).read_text()
        )
        for language in ("pt", "en", "es", "fr"):
            with self.subTest(language=language):
                report, _ = report_fixture(language, warning=True)
                self.assertEqual(
                    semantic_hash(manifest(report)), golden[language]["manifest_hash"]
                )
                pages = page_fingerprints(render_pdf(report))
                self.assertEqual(len(pages), golden[language]["page_count"])
                self.assertEqual(
                    semantic_hash(pages), golden[language]["page_ink_hash"]
                )

    def test_four_languages_share_exact_web_pdf_content_and_version(self):
        for language in ("pt", "en", "es", "fr"):
            with self.subTest(language=language):
                report, data = report_fixture(language, warning=True)
                artifacts = render_channels(report)
                verify_artifacts(report, artifacts)
                self.assertGreater(artifacts["parity"]["page_count"], 1)
                self.assertEqual(report["language"], language)
                self.assertEqual(report["report_version"], "1")
                self.assertEqual(
                    report["audit_result"]["status"], "approved_with_warnings"
                )
                self.assertEqual(len(report["findings"]), 3)
                self.assertEqual(report["forecasts"], [])
                self.assertEqual(report["recommendations"], [])
                self.assertTrue(
                    all(f["status"] == "audited" for f in report["findings"])
                )
                self.assertTrue(
                    all(f["status"] == "draft" for f in data[4]["findings"])
                )
                self.assertFalse(report["audit_result"]["report_published"])

    def test_decimal_locale_is_independent_of_report_language_and_has_no_float_rounding(
        self,
    ):
        report, _ = report_fixture()
        value = deepcopy(report["evidence"][0])
        value.update(
            value="-12345678901234567890.123456789", unit="monetary", currency="EUR"
        )
        for locale, expected in (
            ("en-GB", "-12,345,678,901,234,567,890.123456789 EUR"),
            ("pt-PT", "-12 345 678 901 234 567 890,123456789 EUR"),
            ("fr-FR", "-12 345 678 901 234 567 890,123456789 EUR"),
            ("es-ES", "-12.345.678.901.234.567.890,123456789 EUR"),
        ):
            with self.subTest(locale=locale):
                self.assertEqual(format_value(value, locale), expected)
        changed = deepcopy(report)
        changed["locale"] = "en-GB"
        self.assertEqual(report["findings"], changed["findings"])
        self.assertEqual(report["language"], changed["language"])

    def test_omitted_web_warning_changed_value_or_hidden_text_blocks_parity(self):
        report, _ = report_fixture(warning=True)
        html, pdf = render_html(report), render_pdf(report)
        mutations = (
            html.replace(
                "Scope remains limited to supplied statements.", "Nothing to disclose."
            ),
            html.replace('class="notice"', 'class="notice" hidden', 1),
            html.replace("10 %", "99 %"),
            html.replace("<main>", "<main style='display:none'>"),
        )
        for altered in mutations:
            with self.subTest(altered=altered[:80]):
                self.assertNotEqual(altered, html)
                with self.assertRaises(ReportParityError):
                    verify_channels(report, altered, pdf)

    def test_omitted_or_reordered_pdf_page_is_blocking_even_with_valid_manifest(self):
        report, _ = report_fixture(warning=True)
        html, pdf = render_html(report), render_pdf(report)
        reader = PdfReader(BytesIO(pdf))
        for pages in (reader.pages[:-1], list(reversed(reader.pages))):
            writer = PdfWriter()
            for page in pages:
                writer.add_page(page)
            writer.add_metadata(
                {"/Title": report["report_id"], "/Subject": report["report_version"]}
            )
            buffer = BytesIO()
            writer.write(buffer)
            with self.assertRaises(ReportParityError):
                verify_channels(report, html, buffer.getvalue())

    def test_warning_and_missing_data_are_visible_and_not_only_in_metadata(self):
        report, _ = report_fixture(warning=True)
        blocks = document(report)
        self.assertTrue(
            any(
                b.kind == "notice" and "Scope remains limited" in b.text for b in blocks
            )
        )
        self.assertTrue(any(b.key.startswith("missing/") for b in blocks))
        summary = report["executive_summary"]
        self.assertEqual(
            summary["verified_fact_ids"], [f["finding_id"] for f in report["findings"]]
        )
        self.assertEqual(summary["material_finding_ids"], [])
        self.assertEqual(summary["next_action_ids"], [])

    def test_rehashed_forged_approval_or_incomplete_semantic_checks_are_rejected(self):
        _, (_, _, context, _, draft, audit, checks) = report_fixture()
        corruptions = (
            lambda a: a["checks"].pop(),
            lambda a: a["checks"][-1].update(status="block", severity="blocking"),
            lambda a: a.update(approved_object_ids=[]),
            lambda a: a.update(
                rejected_object_ids=[draft["findings"][0]["finding_id"]]
            ),
            lambda a: a.update(revision_number=1),
            lambda a: a.update(report_published=True),
            lambda a: a.update(retryable=True),
            lambda a: a.update(draft_hash="0" * 64),
            lambda a: a["checks"][-1].update(reason_code="invented"),
            lambda a: a["checks"][-1].update(deterministic=True),
        )
        for mutate in corruptions:
            with self.subTest(mutate=mutate):
                forged = deepcopy(audit)
                mutate(forged)
                with self.assertRaises(InvalidReport):
                    verify_approval(
                        forged,
                        draft,
                        context,
                        deterministic_checks=checks,
                        revision_number=0,
                    )

    def test_schema_extra_fields_naive_timestamps_and_unbound_placeholders_fail(self):
        report, (run, stages, context, _, draft, audit, _) = report_fixture()
        with self.assertRaises(ValueError):
            ReportResult.model_validate({**report, "model_summary": "invented"})
        with self.assertRaises(InvalidReport):
            compile_report(
                run=run,
                context=context,
                draft=draft,
                audit=audit,
                checkpoint=stages["checkpoint"].payload,
                generated_at=datetime(2026, 1, 1),
                locale="pt-PT",
            )
        finding = deepcopy(report["findings"][0])
        finding["statement_template"] += " {{bad}}"
        with self.assertRaises(InvalidReport):
            finding_text(finding, "pt-PT")

    def test_untrusted_markup_is_escaped_and_unsupported_glyphs_fail_closed(self):
        report, _ = report_fixture()
        report["findings"][0]["title"] = '<script>alert("x")</script> & <b>not bold</b>'
        html = render_html(report)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        verify_channels(report, html, render_pdf(report))
        report["findings"][0]["title"] = "Unsupported \U0001f680"
        with self.assertRaises(ReportParityError):
            render_pdf(report)

    def test_persisted_artifacts_are_checked_not_just_the_parity_sidecar(self):
        report, _ = report_fixture()
        original = render_channels(report)
        for key, value in (
            ("html", "<p>incomplete</p>"),
            ("pdf_base64", base64.b64encode(b"broken").decode()),
            ("parity", {"status": "pass"}),
            (
                "unexpected_narrative",
                "An unapproved conclusion must not survive replay.",
            ),
        ):
            with self.subTest(key=key):
                artifacts = deepcopy(original)
                artifacts[key] = value
                artifacts["html_hash"] = sha256(artifacts["html"].encode())
                artifacts["pdf_hash"] = sha256(
                    base64.b64decode(artifacts["pdf_base64"])
                )
                with self.assertRaises(ReportParityError):
                    verify_artifacts(report, artifacts)

    def test_report_flag_is_strict_default_off_and_independent(self):
        self.assertFalse(V2Settings.from_mapping({}).financial_report_enabled)
        self.assertTrue(
            V2Settings.from_mapping(
                {"SALESLAB_V2_FINANCIAL_REPORT_ENABLED": "true"}
            ).financial_report_enabled
        )
        with self.assertRaises(ValueError):
            V2Settings.from_mapping({"SALESLAB_V2_FINANCIAL_REPORT_ENABLED": "yes"})
