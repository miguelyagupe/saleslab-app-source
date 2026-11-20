import asyncio
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from audit_fixtures import critic_output, draft_fixture
from pipeline_fixtures import request, with_growth

from saleslab_v2.audit.context import available_comparatives, comparative_directions
from saleslab_v2.audit.contracts import (
    CriticUnavailable,
    InvalidCriticOutput,
    assemble_result,
    audit_check,
    bind_critique,
)
from saleslab_v2.audit.provider import TOOL_NAME, AnthropicFinancialCritic
from saleslab_v2.audit.statement import statement_checks
from saleslab_v2.config import V2Settings
from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.pipeline.replay import restore_request


class AuditTests(unittest.TestCase):
    def test_pipeline_ytd_source_mismatch_is_omitted_before_critic_context(self):
        from saleslab_v2.normalization.financial import FinancialRow

        value = request()
        batch = value.sources[0]
        for prior_label, expected in (("YTD Q4 2025", False), ("YTD Q1 2025", True)):
            rows = (
                FinancialRow(
                    "cash flow from operations",
                    "200",
                    "YTD Q1 2026",
                    "cash_flow_statement",
                    "consolidated",
                    source_reference="cfo_current",
                ),
                FinancialRow(
                    "cash flow from operations",
                    "100",
                    prior_label,
                    "cash_flow_statement",
                    "consolidated",
                    source_reference="cfo_prior",
                    comparison_basis="comparative",
                ),
            )
            case = replace(value, sources=(replace(batch, rows=(*batch.rows, *rows)),))
            run, stages, context, saved, draft = draft_fixture(case)
            checks = statement_checks(
                run, stages, draft, saved, context, audit_id=uuid4()
            )
            with self.subTest(prior_label=prior_label):
                self.assertTrue(all(c["status"] == "pass" for c in checks))
                self.assertEqual(bool(comparative_directions(stages)), expected)

    def test_directions_require_comparable_calendar_and_fiscal_ytd_cutoffs(self):
        for fiscal_month in (None, 6):
            for current_months, prior_months, expected in (
                (3, 12, False),
                (12, 3, False),
                (3, 3, True),
                (12, 12, True),
            ):
                with self.subTest(
                    fiscal_month=fiscal_month,
                    current_months=current_months,
                    prior_months=prior_months,
                ):
                    import calendar
                    from datetime import date

                    stages = self.direction_stages("200", "100")
                    rows = stages["normalization"].payload["observations"]
                    for row, year, months in zip(
                        rows, (2024, 2023), (current_months, prior_months)
                    ):
                        start = (
                            date(year - 1, 7, 1) if fiscal_month else date(year, 1, 1)
                        )
                        end_index = start.year * 12 + start.month - 1 + months - 1
                        end_year, end_month = end_index // 12, end_index % 12 + 1
                        row["period"].update(
                            start_date=start.isoformat(),
                            end_date=date(
                                end_year,
                                end_month,
                                calendar.monthrange(end_year, end_month)[1],
                            ).isoformat(),
                            granularity="ytd",
                            fiscal_year=year if fiscal_month else None,
                            fiscal_year_end_month=fiscal_month,
                        )
                    result = comparative_directions(stages)
                    self.assertEqual(bool(result), expected)
                    if expected:
                        self.assertEqual(
                            result[0]["direction_current_vs_comparative"], "increase"
                        )

    def test_public_jm_fixture_orders_current_cfo_below_prior_source(self):
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
        value, _ = prepare_upload(parsed, choices, uuid4(), str(uuid4()))
        _, stages, _, context = context_fixture(value)
        before = deepcopy(context)
        directions = comparative_directions(stages)
        self.assertEqual(len(directions), 3)
        cfo = next(r for r in directions if r["current"]["value"] == "1654000000")
        self.assertEqual(cfo["comparative"]["value"], "2025000000")
        self.assertEqual(cfo["current"]["period"]["label"], "2024")
        self.assertEqual(cfo["comparative"]["period"]["label"], "2023")
        self.assertEqual(cfo["direction_current_vs_comparative"], "decrease")
        self.assertEqual(context, before)

    def direction_stages(self, current="1654000000", prior="2025000000"):
        def row(value, year, comparison):
            return {
                "concept_id": "cash_flow_from_operations",
                "value": value,
                "unit": "monetary",
                "currency": "EUR",
                "scope": "consolidated",
                "dimensions": [],
                "normalization_status": "normalized",
                "issues": [],
                "source_document_id": "source",
                "source_content_hash": "a" * 64,
                "source_reference": str(year),
                "period": {
                    "start_date": f"{year}-01-01",
                    "end_date": f"{year}-12-31",
                    "granularity": "year",
                    "comparison_basis": comparison,
                },
            }

        return {
            "normalization": SimpleNamespace(
                payload={
                    "observations": [
                        row(current, 2024, "current"),
                        row(prior, 2023, "comparative"),
                    ]
                }
            )
        }

    def test_exact_direction_is_current_relative_to_prior_without_arithmetic(self):
        for current, prior, direction in (
            ("1654000000", "2025000000", "decrease"),
            ("2025000000", "1654000000", "increase"),
            ("0", "0", "unchanged"),
            ("1", "0", "increase"),
            ("-1", "0", "decrease"),
            ("-1", "-2", "increase"),
            ("-2", "-1", "decrease"),
            ("1." + "0" * 100 + "1", "1", "increase"),
        ):
            with self.subTest(current=current, prior=prior):
                stages = self.direction_stages(current, prior)
                before = deepcopy(stages["normalization"].payload)
                result = comparative_directions(stages)
                self.assertEqual(len(result), 1)
                self.assertEqual(
                    result[0]["direction_current_vs_comparative"], direction
                )
                self.assertEqual(result[0]["current"]["value"], current)
                self.assertEqual(result[0]["comparative"]["source_reference"], "2023")
                self.assertEqual(stages["normalization"].payload, before)
                self.assertNotIn("percentage", result[0])
                self.assertNotIn("capability", result[0])

    def test_ambiguous_incompatible_or_invalid_source_pairs_have_no_direction(self):
        for field, value in (
            ("concept_id", "net_income"),
            ("currency", "USD"),
            ("unit", "percentage"),
            ("scope", "parent"),
            ("dimensions", [["segment", "other"]]),
            ("normalization_status", "unresolved"),
            ("issues", ["ambiguous"]),
            ("value", None),
            ("value", "NaN"),
            ("value", "Infinity"),
        ):
            with self.subTest(field=field, value=value):
                stages = self.direction_stages()
                stages["normalization"].payload["observations"][1][field] = value
                self.assertEqual(comparative_directions(stages), [])
        for period in (
            {"granularity": "quarter"},
            {"fiscal_year_end_month": 6},
            {"end_date": "2024-06-30"},
            {"comparison_basis": "current"},
        ):
            stages = self.direction_stages()
            stages["normalization"].payload["observations"][1]["period"].update(period)
            self.assertEqual(comparative_directions(stages), [])
        for index in (0, 1):
            stages = self.direction_stages()
            rows = stages["normalization"].payload["observations"]
            rows.append(deepcopy(rows[index]))
            self.assertEqual(comparative_directions(stages), [])

    def test_critic_comparatives_preserve_source_period_scope_and_value_without_authorizing_claims(
        self,
    ):
        _, stages, context, _, _ = draft_fixture(with_growth(request()))
        before = deepcopy(context)
        rows = available_comparatives(stages)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["value"], "800")
        self.assertEqual(row["period"]["label"], "2025")
        self.assertEqual(row["period"]["comparison_basis"], "comparative")
        self.assertEqual(row["scope"], "consolidated")
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["source_reference"], "revenue_prior")
        self.assertEqual(context, before)
        self.assertNotIn("capability", row)

    def test_restored_request_and_independent_engines_match_in_all_source_languages_and_scales(
        self,
    ):
        for language in ("pt", "en", "es", "fr"):
            for scale in ("raw", "thousands", "millions"):
                with self.subTest(language=language, scale=scale):
                    value = request(language=language, scale=scale)
                    run, stages, context, saved, draft = draft_fixture(value)
                    restored = restore_request(
                        stages["normalization"].payload["request"], run=run
                    )
                    self.assertEqual(restored.fingerprint, value.fingerprint)
                    checks = statement_checks(
                        run, stages, draft, saved, context, audit_id=uuid4()
                    )
                    self.assertEqual(len(checks), 5)
                    self.assertTrue(all(c["status"] == "pass" for c in checks))

    def test_full_growth_chain_is_recomputed_and_all_semantic_families_bound(self):
        run, stages, context, saved, draft = draft_fixture(with_growth(request()))
        audit_id = uuid4()
        checks = statement_checks(run, stages, draft, saved, context, audit_id=audit_id)
        semantic, _ = bind_critique(
            critic_output({"draft": draft, "evidence_context": context}),
            draft,
            audit_id=audit_id,
            language=context["language"],
        )
        self.assertEqual(len(semantic), 18)
        self.assertTrue(all(not c["deterministic"] for c in semantic))
        result = self.result(draft, audit_id, checks + semantic)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(
            result["approved_object_ids"],
            sorted(f["finding_id"] for f in draft["findings"]),
        )
        self.assertFalse(result["report_published"])

    def result(self, draft, audit_id, checks, revision_number=0):
        return assemble_result(
            audit_id=audit_id,
            run_id=uuid4(),
            draft_stage_id=uuid4(),
            draft_hash="a" * 64,
            checkpoint_hash="b" * 64,
            checks=checks,
            finding_ids={f["finding_id"] for f in draft["findings"]},
            configuration={"role": "independent_critic"},
            audited_at=datetime.now(UTC),
            revision_number=revision_number,
        )

    def test_draft_bindings_schema_values_and_permissions_are_rechecked(self):
        corruptions = (
            lambda d: d["findings"][0]["value_bindings"].clear(),
            lambda d: d["findings"][0].update(causal_status="reconciled_driver"),
            lambda d: d["findings"][0].update(capability="recommend_material"),
            lambda d: d["findings"][0].update(statement_template="Margin is 99%."),
            lambda d: d["findings"][0].update(confidence_score=99),
            lambda d: d.update(recommendations=[{"action": "hedge"}]),
            lambda d: d.update(audited=True),
            lambda d: d.update(unexpected="hidden narrative"),
            lambda d: d["financial_snapshot"][0].update(value="999"),
        )
        for mutate in corruptions:
            with self.subTest(mutate=mutate):
                run, stages, context, saved, draft = draft_fixture(request())
                # Deepcopy: source context and draft may share fixture objects.
                draft = deepcopy(draft)
                mutate(draft)
                checks = statement_checks(
                    run, stages, draft, saved, context, audit_id=uuid4()
                )
                self.assertTrue(any(c["status"] == "block" for c in checks))

    def test_recalculation_detects_consistently_rehashed_but_wrong_formula_or_value(
        self,
    ):
        for field, value in (
            ("canonical_expression", "net_income/revenue*1000"),
            ("exact_output", "999"),
        ):
            with self.subTest(field=field):
                run, stages, context, saved, draft = draft_fixture(request())
                stages["metrics"].payload["metrics"][0]["trace"][field] = value
                checks = statement_checks(
                    run, stages, draft, saved, context, audit_id=uuid4()
                )
                self.assertEqual(
                    checks[-1]["reason_code"], "audit.deterministic_validation_failed"
                )

    def test_critic_must_cover_each_finding_and_every_check_without_overriding_status(
        self,
    ):
        _, _, context, _, draft = draft_fixture(request())
        good = critic_output({"draft": draft, "evidence_context": context})
        corruptions = (
            lambda raw: raw.update(status="approved"),
            lambda raw: raw.update(findings=[]),
            lambda raw: raw["findings"][0].update(finding_id=str(uuid4())),
            lambda raw: raw["findings"][0]["checks"].pop(),
            lambda raw: raw["findings"].append(deepcopy(raw["findings"][0])),
            lambda raw: raw["findings"][0]["checks"][0].update(outcome="block"),
            lambda raw: raw.update(language="es"),
            lambda raw: raw["findings"][0]["checks"][0].update(check_type="invented"),
        )
        for mutate in corruptions:
            with self.subTest(mutate=mutate):
                raw = deepcopy(good)
                mutate(raw)
                with self.assertRaises(InvalidCriticOutput):
                    bind_critique(
                        raw, draft, audit_id=uuid4(), language=context["language"]
                    )

    def test_blocking_deterministic_check_wins_even_if_critic_passes_everything(self):
        _, _, context, _, draft = draft_fixture(request())
        aid = uuid4()
        semantic, _ = bind_critique(
            critic_output({"draft": draft, "evidence_context": context}),
            draft,
            audit_id=aid,
            language=context["language"],
        )
        result = self.result(
            draft,
            aid,
            [audit_check(aid, "capability", "block", "test.blocking"), *semantic],
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["approved_object_ids"], [])

    def test_warning_revision_and_block_precedence_and_revision_budget(self):
        _, _, _, _, draft = draft_fixture(request())
        aid, fid = uuid4(), draft["findings"][0]["finding_id"]
        warning = audit_check(
            aid,
            "claim_support",
            "warning",
            "limitation",
            refs=(fid,),
            deterministic=False,
        )
        revise = audit_check(
            aid,
            "causality",
            "revise",
            "causal_overstatement",
            refs=(fid,),
            remediation="Use observed wording.",
            deterministic=False,
        )
        blocked = audit_check(
            aid,
            "recommendation",
            "block",
            "unsupported_hedge",
            refs=(fid,),
            remediation="Remove the material commitment.",
            deterministic=False,
        )
        self.assertEqual(
            self.result(draft, aid, [warning])["status"], "approved_with_warnings"
        )
        self.assertEqual(
            self.result(draft, aid, [warning, revise])["status"], "revision_required"
        )
        self.assertEqual(
            self.result(draft, aid, [warning, revise, blocked])["status"], "blocked"
        )
        exhausted = self.result(draft, aid, [revise], revision_number=2)
        self.assertEqual(exhausted["status"], "blocked")
        self.assertIn(
            "audit.revision_budget_exhausted",
            [c["reason_code"] for c in exhausted["checks"]],
        )
        self.assertEqual(
            self.result(draft, aid, [], revision_number=2)["status"], "approved"
        )

    def test_report_languages_are_bound_and_do_not_change_values(self):
        for language in ("pt", "en", "es", "fr"):
            with self.subTest(language=language):
                value = request()
                value = replace(
                    value,
                    language=replace(
                        value.language, report_language=LanguageCode(language)
                    ),
                )
                run, stages, context, saved, draft = draft_fixture(value)
                self.assertTrue(
                    all(
                        c["status"] == "pass"
                        for c in statement_checks(
                            run, stages, draft, saved, context, audit_id=uuid4()
                        )
                    )
                )
                checks, _ = bind_critique(
                    critic_output({"draft": draft, "evidence_context": context}),
                    draft,
                    audit_id=uuid4(),
                    language=language,
                )
                self.assertEqual(len(checks), 6)

    def test_audit_flag_defaults_off_and_is_strict(self):
        self.assertFalse(V2Settings.from_mapping({}).financial_audit_enabled)
        self.assertTrue(
            V2Settings.from_mapping(
                {"SALESLAB_V2_FINANCIAL_AUDIT_ENABLED": "true"}
            ).financial_audit_enabled
        )
        with self.assertRaises(ValueError):
            V2Settings.from_mapping({"SALESLAB_V2_FINANCIAL_AUDIT_ENABLED": "yes"})

    def test_critic_transport_is_independent_structured_and_has_no_hidden_retries(self):
        client = Mock()
        block = SimpleNamespace(
            type="tool_use", name=TOOL_NAME, input={"response": "test"}
        )
        create = AsyncMock(
            return_value=SimpleNamespace(stop_reason="tool_use", content=[block])
        )
        client.with_options.return_value.messages.create = create
        critic = AnthropicFinancialCritic(client, "claude-sonnet-4-6")
        self.assertEqual(asyncio.run(critic.generate({})), block.input)
        self.assertEqual(critic.configuration["role"], "independent_critic")
        self.assertEqual(create.call_args.kwargs["tools"][0]["name"], TOOL_NAME)
        client.with_options.assert_called_once_with(timeout=60, max_retries=0)

    def test_critic_truncation_refusal_wrong_tool_and_transport_error_fail_closed(self):
        for response in (
            SimpleNamespace(stop_reason="max_tokens", content=[]),
            SimpleNamespace(stop_reason="refusal", content=[]),
            SimpleNamespace(
                stop_reason="tool_use",
                content=[SimpleNamespace(type="tool_use", name="wrong", input={})],
            ),
        ):
            client = Mock()
            client.with_options.return_value.messages.create = AsyncMock(
                return_value=response
            )
            with self.assertRaises(CriticUnavailable):
                asyncio.run(
                    AnthropicFinancialCritic(client, "explicit-model").generate({})
                )
        client = Mock()
        client.with_options.return_value.messages.create = AsyncMock(
            side_effect=TimeoutError("SECRET")
        )
        with self.assertRaisesRegex(
            CriticUnavailable, "independent critic request failed"
        ):
            asyncio.run(AnthropicFinancialCritic(client, "explicit-model").generate({}))
