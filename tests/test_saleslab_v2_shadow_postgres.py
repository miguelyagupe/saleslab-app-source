"""Real PostgreSQL F-09 isolation, replay, failures and full F-01 → F-08 checks."""

import asyncio
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import test_saleslab_v2_audit_postgres as audit_tests
from analyst_fixtures import RecordingModel
from audit_fixtures import RecordingCritic
from pipeline_fixtures import change_row
from shadow_fixtures import RecordingLegacy, shadow_case
from sqlalchemy import func, select

from models import Analysis, UsageCounter
from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    MetricObservationModel,
    StageResultModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.shadow.campaign import FinancialShadowCampaign
from saleslab_v2.shadow.contracts import InvalidShadow
from saleslab_v2.shadow.service import FinancialShadowRunner


@unittest.skipUnless(
    audit_tests.URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class ShadowPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        audit_tests.AuditPostgresTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        audit_tests.AuditPostgresTests.tearDownClass.__func__(cls)

    setup_review = audit_tests.AuditPostgresTests.setup_review

    async def ready(self):
        value, settings, original, _, _ = await self.setup_review()
        settings = replace(
            settings, financial_report_enabled=True, financial_shadow_enabled=True
        )
        return shadow_case(value), settings, original

    def runner(self, session, settings, model=None, critic=None, legacy=None):
        return FinancialShadowRunner(
            session,
            settings,
            model or RecordingModel(),
            critic or RecordingCritic(),
            legacy or RecordingLegacy(),
        )

    async def stage(self, session, result, suffix):
        return await session.scalar(
            select(StageResultModel).where(
                StageResultModel.run_id == result.run_id,
                StageResultModel.stage_name == "financial_shadow_" + suffix,
            )
        )

    def test_full_comparison_replays_without_models_history_or_quota(self):
        async def scenario():
            case, settings, original = await self.ready()
            model, critic, legacy = (
                RecordingModel(),
                RecordingCritic(),
                RecordingLegacy(),
            )
            async with self.sessions.begin() as session:
                runner = self.runner(session, settings, model, critic, legacy)
                first = await runner.execute(case, attempt_key="golden")
                self.assertFalse(first.replayed)
                self.assertEqual(first.payload["status"], "pass")
                self.assertFalse(first.payload["rollout_authorized"])
                original_run = await session.get(AnalysisRunModel, original.run_id)
                self.assertEqual(original_run.workflow_status, "auditing")
                row = await self.stage(session, first, "input")
                child_id = UUID(row.payload["v2_run_id"])
                self.assertNotEqual(child_id, original.run_id)
                child = await session.get(AnalysisRunModel, child_id)
                self.assertEqual(child.workflow_status, "completed")
                self.assertEqual(child.workspace_id, case.request.workspace_id)
                self.assertEqual(legacy.calls[0], row.payload["shared_input"])
            async with self.sessions.begin() as session:
                replay = await self.runner(
                    session, settings, model, critic, legacy
                ).execute(case, attempt_key="golden")
                self.assertTrue(replay.replayed)
                self.assertEqual(first.payload, replay.payload)
                for table in (Analysis, UsageCounter):
                    self.assertEqual(
                        await session.scalar(select(func.count()).select_from(table)), 0
                    )
                parent_stages = (
                    await session.scalars(
                        select(StageResultModel).where(
                            StageResultModel.run_id == first.run_id
                        )
                    )
                ).all()
                self.assertEqual(len(parent_stages), 4)
                self.assertTrue(
                    all(
                        s.output_hash == semantic_hash(s.payload) for s in parent_stages
                    )
                )
            self.assertEqual(
                (len(model.calls), len(critic.calls), len(legacy.calls)), (1, 1, 1)
            )

        asyncio.run(scenario())

    def test_concurrent_attempts_execute_once(self):
        async def scenario():
            case, settings, _ = await self.ready()
            model, critic, legacy = (
                RecordingModel(),
                RecordingCritic(),
                RecordingLegacy(),
            )

            async def execute():
                async with self.sessions.begin() as session:
                    return await self.runner(
                        session, settings, model, critic, legacy
                    ).execute(case, attempt_key="concurrent")

            left, right = await asyncio.gather(execute(), execute())
            self.assertEqual(left.run_id, right.run_id)
            self.assertEqual(left.payload, right.payload)
            self.assertEqual({left.replayed, right.replayed}, {False, True})
            self.assertEqual(
                (len(model.calls), len(critic.calls), len(legacy.calls)), (1, 1, 1)
            )

        asyncio.run(scenario())

    def test_negative_corpus_blocks_models_and_captures_known_legacy_zero_bug(self):
        async def scenario():
            case, settings, _ = await self.ready()
            for name, reference, changes, state, code, legacy_state in (
                (
                    "zero",
                    "revenue",
                    {"text": "0"},
                    "blocked",
                    "pipeline.metric_invalid",
                    "failed",
                ),
                (
                    "mapping",
                    "equity",
                    {"name": "EBITDA unknown"},
                    "mapping_required",
                    "pipeline.mapping_required",
                    "completed",
                ),
                (
                    "balance",
                    "assets",
                    {"text": "200"},
                    "blocked",
                    "pipeline.reconciliation_blocked",
                    "completed",
                ),
            ):
                with self.subTest(case=name):
                    negative = replace(
                        case,
                        case_id=name,
                        request=change_row(case.request, reference, **changes),
                        expected_v2_status=state,
                        expected_legacy_status=legacy_state,
                        metrics=(),
                        required_capabilities=(
                            ("calculate", "blocked"),
                            ("forecast", "blocked"),
                        ),
                        expected_issue_codes=(code,),
                        expected_audit_status=None,
                    )
                    model, critic = RecordingModel(), RecordingCritic()
                    async with self.sessions.begin() as session:
                        result = await self.runner(
                            session, settings, model, critic
                        ).execute(negative, attempt_key=name)
                        self.assertEqual(result.payload["status"], "pass")
                        self.assertTrue(
                            result.payload["migration_differences_require_review"]
                        )
                        saved = await self.stage(session, result, "v2")
                        self.assertIsNone(saved.payload["snapshot"]["report"])
                        replay = await self.runner(
                            session, settings, model, critic
                        ).execute(negative, attempt_key=name)
                        self.assertTrue(replay.replayed)
                    self.assertEqual(model.calls, [])
                    self.assertEqual(critic.calls, [])

        asyncio.run(scenario())

    def test_flags_membership_and_changed_case_configuration_fail_before_models(self):
        async def scenario():
            case, settings, _ = await self.ready()
            model, critic, legacy = (
                RecordingModel(),
                RecordingCritic(),
                RecordingLegacy(),
            )
            async with self.sessions.begin() as session:
                for denied_settings, denied_case in (
                    (replace(settings, financial_shadow_enabled=False), case),
                    (replace(settings, internal_workspaces=frozenset()), case),
                    (
                        settings,
                        replace(case, request=replace(case.request, user_id=uuid4())),
                    ),
                ):
                    with self.assertRaises(PermissionError):
                        await self.runner(
                            session, denied_settings, model, critic, legacy
                        ).execute(denied_case, attempt_key="denied")
                self.assertEqual(
                    (model.calls, critic.calls, legacy.calls), ([], [], [])
                )
                await self.runner(session, settings, model, critic, legacy).execute(
                    case, attempt_key="fixed"
                )
                altered = replace(
                    case,
                    metrics=(
                        replace(
                            case.metrics[0], legacy_absolute_tolerance=Decimal("0.1")
                        ),
                    ),
                )
                with self.assertRaises(IdempotencyConflict):
                    await self.runner(session, settings, model, critic, legacy).execute(
                        altered, attempt_key="fixed"
                    )
                model.configuration = {**model.configuration, "model": "changed"}
                with self.assertRaises(IdempotencyConflict):
                    await self.runner(session, settings, model, critic, legacy).execute(
                        case, attempt_key="fixed"
                    )
            self.assertEqual(
                (len(model.calls), len(critic.calls), len(legacy.calls)), (1, 1, 1)
            )

        asyncio.run(scenario())

    def test_rehashed_result_input_and_invalid_legacy_envelope_are_rejected(self):
        async def scenario():
            case, settings, _ = await self.ready()
            async with self.sessions.begin() as session:
                first = await self.runner(session, settings).execute(
                    case, attempt_key="tamper"
                )
            for suffix in ("input", "result", "legacy", "v2"):
                async with self.sessions() as session, session.begin():
                    stage = await self.stage(session, first, suffix)
                    forged = deepcopy(stage.payload)
                    if suffix == "legacy":
                        forged["status"] = "failed"
                    elif suffix == "v2":
                        forged["snapshot"]["workflow_status"] = "blocked"
                    else:
                        forged["unexpected"] = True
                    stage.payload, stage.output_hash = forged, semantic_hash(forged)
                    await session.flush()
                    with self.assertRaises(
                        (InvalidShadow, IdempotencyConflict, ValueError)
                    ):
                        await self.runner(session, settings).execute(
                            case, attempt_key="tamper"
                        )
                    await session.rollback()

        asyncio.run(scenario())

    def test_failed_shadow_write_rolls_back_child_and_parent_then_retry_succeeds(self):
        async def scenario():
            case, settings, _ = await self.ready()
            original = V2Repository.persist_stage

            async def fail(repository, stage):
                if stage.stage_name == "financial_shadow_result":
                    raise RuntimeError("synthetic storage failure")
                return await original(repository, stage)

            async with self.sessions.begin() as session:
                before = await session.scalar(
                    select(func.count()).select_from(AnalysisRunModel)
                )
                with patch.object(
                    V2Repository, "persist_stage", fail
                ), self.assertRaises(RuntimeError):
                    await self.runner(session, settings).execute(
                        case, attempt_key="retry"
                    )
                self.assertEqual(
                    await session.scalar(
                        select(func.count()).select_from(AnalysisRunModel)
                    ),
                    before,
                )
                result = await self.runner(session, settings).execute(
                    case, attempt_key="retry"
                )
                self.assertEqual(result.payload["status"], "pass")

        asyncio.run(scenario())

    def test_changed_live_child_metric_invalidates_shadow_replay(self):
        async def scenario():
            case, settings, _ = await self.ready()
            model, critic, legacy = (
                RecordingModel(),
                RecordingCritic(),
                RecordingLegacy(),
            )
            async with self.sessions.begin() as session:
                first = await self.runner(
                    session, settings, model, critic, legacy
                ).execute(case, attempt_key="live-evidence")
                saved = await self.stage(session, first, "input")
                child_id = UUID(saved.payload["v2_run_id"])
            async with self.sessions() as session, session.begin():
                metric = await session.scalar(
                    select(MetricObservationModel).where(
                        MetricObservationModel.run_id == child_id
                    )
                )
                metric.value = Decimal(99)
                await session.flush()
                with self.assertRaises(InvalidCheckpoint):
                    await self.runner(session, settings, model, critic, legacy).execute(
                        case, attempt_key="live-evidence"
                    )
                await session.rollback()
            self.assertEqual(
                (len(model.calls), len(critic.calls), len(legacy.calls)), (1, 1, 1)
            )

        asyncio.run(scenario())

    def test_malformed_legacy_response_is_sanitized_and_does_not_approve_the_case(self):
        async def scenario():
            case, settings, _ = await self.ready()
            legacy = RecordingLegacy(
                lambda raw: {
                    "report": {},
                    "usage": {"input_tokens": -1, "output_tokens": 0},
                }
            )
            async with self.sessions.begin() as session:
                result = await self.runner(session, settings, legacy=legacy).execute(
                    case, attempt_key="malformed"
                )
                self.assertEqual(result.payload["status"], "fail")
                saved = await self.stage(session, result, "legacy")
                self.assertEqual(saved.payload["error_code"], "shadow.legacy_failed")
                self.assertIsNone(saved.payload["output"])
                self.assertNotIn("input_tokens", str(saved.payload))

        asyncio.run(scenario())

    def test_revision_loop_runs_before_comparison(self):
        async def scenario():
            case, settings, _ = await self.ready()
            count = 0

            def revise_once(raw, context):
                nonlocal count
                count += 1
                return (
                    audit_tests.judgment(raw, "scope", "revise") if count == 1 else raw
                )

            model, critic = RecordingModel(), RecordingCritic(revise_once)
            async with self.sessions.begin() as session:
                result = await self.runner(session, settings, model, critic).execute(
                    case, attempt_key="revision"
                )
                self.assertEqual(result.payload["status"], "pass")
            self.assertEqual((len(model.calls), len(critic.calls)), (2, 2))

        asyncio.run(scenario())

    def test_campaign_covers_entire_declared_corpus_and_replays_same_manifest(self):
        async def scenario():
            case, settings, _ = await self.ready()
            other = replace(case, case_id="second-case")
            model, critic, legacy = (
                RecordingModel(),
                RecordingCritic(),
                RecordingLegacy(),
            )
            async with self.sessions.begin() as session:
                campaign = FinancialShadowCampaign(
                    self.runner(session, settings, model, critic, legacy)
                )
                kwargs = {
                    "dataset_version": case.dataset_version,
                    "required_case_ids": (case.case_id, other.case_id),
                    "campaign_key": "campaign",
                }
                first = await campaign.execute((case, other), **kwargs)
                replay = await campaign.execute((other, case), **kwargs)
                self.assertEqual(first, replay)
                self.assertEqual(first["automated_checks"], "pass")
                self.assertFalse(first["rollout_authorized"])
                self.assertEqual(len(first["case_run_ids"]), 2)
            self.assertEqual(
                (len(model.calls), len(critic.calls), len(legacy.calls)), (2, 2, 2)
            )

        asyncio.run(scenario())
