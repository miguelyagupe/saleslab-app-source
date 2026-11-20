import asyncio
import unittest
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from analyst_fixtures import context_fixture, model_output
from pipeline_fixtures import request, with_growth

from saleslab_v2.analyst.context import (
    InvalidCheckpoint,
    build_context,
    verify_checkpoint,
)
from saleslab_v2.analyst.contracts import InvalidAnalystOutput, bind_findings
from saleslab_v2.analyst.provider import TOOL_NAME, AnthropicFinancialModel
from saleslab_v2.config import V2Settings
from saleslab_v2.domain.enums import Capability, LanguageCode


class AnalystTests(unittest.TestCase):
    def test_duplicate_plan_aliases_share_one_canonical_evidence_item(self):
        value = request()
        value = replace(
            value,
            metrics=(
                *value.metrics,
                replace(value.metrics[0], key="same_metric_alias"),
            ),
        )
        _, _, _, context = context_fixture(value)
        self.assertEqual(len(context["evidence"]), 1)
        self.assertEqual(len(bind_findings(model_output(context), context)), 1)

    def test_binds_calculated_values_provenance_and_gates_without_model_arithmetic(
        self,
    ):
        _, _, _, context = context_fixture(with_growth(request()))
        findings = bind_findings(model_output(context), context)
        self.assertEqual(len(findings), 3)
        growth = next(f for f in findings if f["capability"] == "compare")
        binding = next(iter(growth["value_bindings"].values()))
        self.assertEqual(binding["value"], "25")
        self.assertEqual(
            {r["location"] for r in binding["source_references"]},
            {"revenue", "revenue_prior"},
        )
        self.assertEqual(len(binding["trace"]["inputs"]), 2)
        self.assertEqual(growth["status"], "draft")
        self.assertIsNone(growth["confidence_score"])
        self.assertTrue(growth["requires_independent_audit"])
        self.assertEqual(bind_findings(model_output(context), context), findings)

    def test_report_languages_do_not_change_canonical_values_or_gates(self):
        for language in ("pt", "en", "es", "fr"):
            with self.subTest(language=language):
                value = request()
                value = replace(
                    value,
                    language=replace(
                        value.language, report_language=LanguageCode(language)
                    ),
                )
                _, _, _, context = context_fixture(value)
                findings = bind_findings(model_output(context), context)
                self.assertEqual(context["language"], language)
                self.assertEqual(context["evidence"][0]["value"], "10")
                self.assertEqual(context["sources"][0]["languages"], ["en"])
                self.assertEqual(findings[0]["capability"], "calculate")

    def test_summary_uses_direct_source_evidence_only_when_requested_and_allowed(self):
        value = request()
        batch = value.sources[0]
        value = replace(
            value,
            sources=(
                replace(
                    batch,
                    rows=tuple(
                        r for r in batch.rows if r.statement_type == "balance_sheet"
                    ),
                ),
            ),
            metrics=(),
            requested_capabilities=(Capability.SUMMARIZE,),
        )
        _, _, _, context = context_fixture(value)
        self.assertEqual(len(context["evidence"]), 3)
        self.assertTrue(all(e["type"] == "direct" for e in context["evidence"]))
        self.assertTrue(
            all(
                f["claim_type"] == "fact"
                for f in bind_findings(model_output(context), context)
            )
        )

    def test_schema_evidence_and_capability_violations_are_rejected(self):
        _, _, _, context = context_fixture(request())
        good = model_output(context)
        corruptions = (
            lambda r: r.update(forecast={"revenue": "999"}),
            lambda r: r.update(language="fr"),
            lambda r: r.update(findings=[]),
            lambda r: r["findings"][0].update(evidence_ids=[str(uuid4())]),
            lambda r: r["findings"][0].update(
                capability="summarize", claim_type="fact"
            ),
            lambda r: r["findings"][0].update(claim_type="hypothesis"),
            lambda r: r["findings"][0].update(confidence_score=99),
            lambda r: r["findings"][0].update(statement="A margem é 99%."),
            lambda r: r["findings"][0].update(title="Growth ９９%"),
            lambda r: r["findings"][0].update(
                statement=r["findings"][0]["statement"] + " {unbound}"
            ),
            lambda r: r["findings"].append(deepcopy(r["findings"][0])),
            lambda r: r["findings"][0]["evidence_ids"].append(
                r["findings"][0]["evidence_ids"][0]
            ),
        )
        for mutate in corruptions:
            with self.subTest(mutate=mutate):
                raw = deepcopy(good)
                mutate(raw)
                with self.assertRaises(InvalidAnalystOutput):
                    bind_findings(raw, context)

    def test_cannot_mix_scope_or_period_under_a_single_finding(self):
        _, _, _, context = context_fixture(with_growth(request()))
        calc = [e for e in context["evidence"] if e["capability"] == "calculate"]
        self.assertEqual(len(calc), 2)
        raw = model_output(context)
        first = next(f for f in raw["findings"] if f["capability"] == "calculate")
        other = next(e for e in calc if e["evidence_id"] not in first["evidence_ids"])
        first["evidence_ids"].append(other["evidence_id"])
        first["statement"] += " {{" + other["evidence_id"] + "}}"
        other["scope"] = "segment"
        with self.assertRaises(InvalidAnalystOutput):
            bind_findings(raw, context)

    def test_hash_chain_and_result_bindings_fail_closed(self):
        for stage_name in (
            "normalization",
            "metrics",
            "reconciliation",
            "quality_gates",
            "checkpoint",
        ):
            with self.subTest(stage=stage_name):
                run, stages, _, _ = context_fixture(request())
                stages[stage_name].payload["tampered"] = True
                with self.assertRaises(InvalidCheckpoint):
                    verify_checkpoint(run, stages)

    def test_foreign_or_changed_source_provenance_is_rejected(self):
        run, stages, sources, _ = context_fixture(request())
        sources[0].content_hash = "b" * 64
        with self.assertRaises(InvalidCheckpoint):
            build_context(run, stages["checkpoint"].payload, stages, sources)

    def test_analyst_flag_defaults_off_and_requires_strict_boolean(self):
        self.assertFalse(V2Settings.from_mapping({}).financial_analyst_enabled)
        self.assertTrue(
            V2Settings.from_mapping(
                {"SALESLAB_V2_FINANCIAL_ANALYST_ENABLED": "true"}
            ).financial_analyst_enabled
        )
        with self.assertRaises(ValueError):
            V2Settings.from_mapping({"SALESLAB_V2_FINANCIAL_ANALYST_ENABLED": "yes"})

    def test_async_provider_sends_separate_system_contract_and_tool_schema(self):
        _, _, _, context = context_fixture(request())
        block = SimpleNamespace(
            type="tool_use", name=TOOL_NAME, input=model_output(context)
        )
        create = AsyncMock(
            return_value=SimpleNamespace(stop_reason="tool_use", content=[block])
        )
        client = Mock()
        client.with_options.return_value.messages.create = create
        model = AnthropicFinancialModel(client, "claude-sonnet-4-6")
        result = asyncio.run(model.generate(context))
        self.assertEqual(result, block.input)
        kwargs = create.call_args.kwargs
        self.assertNotIn("messages", kwargs["system"])
        self.assertEqual(
            kwargs["tools"][0]["input_schema"]["additionalProperties"], False
        )
        self.assertEqual(kwargs["tool_choice"]["type"], "auto")
        client.with_options.assert_called_once_with(timeout=60, max_retries=0)

    def test_truncation_refusal_text_extra_blocks_and_provider_failures_are_closed(
        self,
    ):
        from saleslab_v2.analyst.contracts import AnalystUnavailable

        for stop, blocks in (
            ("max_tokens", []),
            ("refusal", []),
            ("end_turn", []),
            ("tool_use", [SimpleNamespace(type="text")]),
            ("tool_use", [Mock(), Mock()]),
        ):
            with self.subTest(stop=stop, blocks=blocks):
                client = Mock()
                client.with_options.return_value.messages.create = AsyncMock(
                    return_value=SimpleNamespace(stop_reason=stop, content=blocks)
                )
                with self.assertRaises(AnalystUnavailable):
                    asyncio.run(
                        AnthropicFinancialModel(client, "explicit-model").generate({})
                    )
        client = Mock()
        client.with_options.return_value.messages.create = AsyncMock(
            side_effect=TimeoutError("secret provider detail")
        )
        with self.assertRaisesRegex(
            AnalystUnavailable, "financial model request failed"
        ):
            asyncio.run(AnthropicFinancialModel(client, "explicit-model").generate({}))
