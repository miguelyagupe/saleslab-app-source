"""Real PostgreSQL F-01 → F-07 tests with recording analyst and critic."""

import asyncio
import os
import subprocess
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

URL = os.environ.get("TEST_DATABASE_URL", "")
if os.environ.get("SALESLAB_REQUIRE_POSTGRES_TESTS") == "1" and not URL:
    raise RuntimeError("TEST_DATABASE_URL is required for F-07 integration")
os.environ.setdefault("DATABASE_URL", URL or "postgresql://unused/audit_test")

from analyst_fixtures import RecordingModel
from audit_fixtures import RecordingCritic
from pipeline_fixtures import request, with_growth

from database import Base
from models import Analysis, UsageCounter, User, Workspace, WorkspaceMember
from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.analyst.contracts import AnalystUnavailable
from saleslab_v2.analyst.service import FinancialAnalyst
from saleslab_v2.audit.contracts import CriticUnavailable
from saleslab_v2.audit.service import FinancialAuditor
from saleslab_v2.audit.workflow import FinancialReviewWorkflow
from saleslab_v2.config import V2Settings
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.metadata import legacy_tables
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    CalculationTraceModel,
    CapabilityDecisionModel,
    MetricObservationModel,
    SourceDocumentModel,
    StageResultModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.service import FinancialPipeline

ROOT = Path(__file__).resolve().parents[1]


def async_url(value):
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def judgment(raw, family, outcome):
    for finding in raw["findings"]:
        check = next(c for c in finding["checks"] if c["check_type"] == family)
        check.update(
            outcome=outcome,
            message="The proposed wording exceeds its evidence.",
            remediation="Use only the observed metric and its documented basis.",
        )
    return raw


@unittest.skipUnless(
    URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class AuditPostgresTests(unittest.TestCase):
    def test_versioned_critic_context_includes_comparatives_and_replays_without_model_calls(
        self,
    ):
        from saleslab_v2.audit.context import CONTEXT_VERSION, LEGACY_CONTEXT_VERSION

        async def scenario(version):
            value, settings, checkpoint, _, _ = await self.setup_review(with_growth)
            critic = RecordingCritic()
            critic.configuration["context_version"] = version
            async with self.sessions.begin() as session:
                first = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                rows = critic.calls[0]["available_comparatives"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["value"], "800")
                self.assertEqual(rows[0]["period"]["label"], "2025")
                if version == CONTEXT_VERSION:
                    directions = critic.calls[0]["comparative_directions"]
                    self.assertEqual(len(directions), 1)
                    self.assertEqual(
                        directions[0]["direction_current_vs_comparative"], "increase"
                    )
                else:
                    self.assertNotIn("comparative_directions", critic.calls[0])
                self.assertEqual(critic.calls[0]["presentation"]["locale"], "pt-PT")
                self.assertTrue(
                    any(
                        "25,00 %" in f["statement"]
                        for f in critic.calls[0]["presentation"]["findings"]
                    )
                )
            async with self.sessions.begin() as session:
                replay = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.payload, first.payload)
                self.assertEqual(len(critic.calls), 1)

        for version in (LEGACY_CONTEXT_VERSION, CONTEXT_VERSION):
            with self.subTest(version=version):
                asyncio.run(scenario(version))

    def test_rehashed_revision_counters_cannot_override_actual_attempt_history(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            model = RecordingModel()
            critic = RecordingCritic(
                lambda raw, ctx: judgment(raw, "causality", "revise")
            )
            async with self.sessions.begin() as session:
                first = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                await FinancialAnalyst(session, settings, model).execute(
                    **self.args(value, checkpoint),
                    attempt_key="revision-1",
                    revision_of=UUID(first.payload["audit_id"]),
                )
                second = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint),
                    analyst_attempt_key="revision-1",
                    attempt_key="audit-1",
                )
                await FinancialAnalyst(session, settings, model).execute(
                    **self.args(value, checkpoint),
                    attempt_key="revision-2",
                    revision_of=UUID(second.payload["audit_id"]),
                )
            for field, value_to_forge in (
                ("number", 1),
                ("budget_exhausted", True),
                ("revision", None),
                ("revision", {}),
            ):
                probe = RecordingCritic()
                async with self.sessions() as session, session.begin():
                    saved = await session.get(
                        StageResultModel,
                        uuid5(
                            TRACE_NAMESPACE,
                            f"analyst-input:{checkpoint.run_id}:revision-2",
                        ),
                    )
                    altered = deepcopy(saved.payload)
                    self.assertEqual(altered["context"]["revision"]["number"], 2)
                    if field == "revision":
                        altered["context"]["revision"] = value_to_forge
                    else:
                        altered["context"]["revision"][field] = value_to_forge
                    saved.payload = altered
                    saved.input_hash = saved.output_hash = semantic_hash(altered)
                    child = await session.get(
                        StageResultModel,
                        uuid5(
                            TRACE_NAMESPACE, f"analyst:{checkpoint.run_id}:revision-2"
                        ),
                    )
                    child_payload = deepcopy(child.payload)
                    child_payload["context_hash"] = semantic_hash(altered["context"])
                    child.payload = child_payload
                    child.output_hash = semantic_hash(child_payload)
                    child.input_hash = saved.input_hash
                    await session.flush()
                    with self.assertRaises((InvalidCheckpoint, IdempotencyConflict)):
                        await FinancialAnalyst(session, settings, model).execute(
                            **self.args(value, checkpoint),
                            attempt_key="revision-2",
                            revision_of=UUID(second.payload["audit_id"]),
                        )
                    result = await FinancialAuditor(session, settings, probe).execute(
                        **self.args(value, checkpoint),
                        analyst_attempt_key="revision-2",
                        attempt_key="forgery-check",
                    )
                    self.assertEqual(result.payload["status"], "blocked")
                    self.assertEqual(result.payload["approved_object_ids"], [])
                    self.assertEqual(probe.calls, [])
                    await session.rollback()
            self.assertEqual(len(model.calls), 2)

        asyncio.run(scenario())

    @classmethod
    def setUpClass(cls):
        parsed = make_url(async_url(URL))
        if parsed.host not in {"localhost", "127.0.0.1"} or not any(
            marker in (parsed.database or "").lower()
            for marker in ("test", "disposable")
        ):
            raise RuntimeError("refusing a nonlocal or nondisposable database")
        cls.engine = create_async_engine(async_url(URL), poolclass=NullPool)
        cls.sessions = async_sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.engine.dispose())

    async def setup_review(self, transform=None, model=None):
        async with self.engine.begin() as connection:
            self.assertEqual(
                int(await connection.scalar(text("SHOW server_version_num"))) // 10000,
                16,
            )
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=legacy_tables())
            )
        for args in (("stamp", "20260727_0001"), ("upgrade", "head")):
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", *args],
                cwd=ROOT,
                env={**os.environ, "DATABASE_URL": URL},
                capture_output=True,
                text=True,
                check=True,
            )
        value = request(uuid4(), uuid4())
        if transform:
            value = transform(value)
        async with self.sessions.begin() as session:
            session.add(Workspace(id=value.workspace_id, company_name="Synthetic F-07"))
            session.add(
                User(id=value.user_id, email="audit@example.test", password_hash="x")
            )
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=value.workspace_id, user_id=value.user_id, role="owner"
                )
            )
        settings = V2Settings(
            True,
            True,
            frozenset((str(value.workspace_id),)),
            financial_analyst_enabled=True,
            financial_audit_enabled=True,
        )
        model = model or RecordingModel()
        async with self.sessions.begin() as session:
            checkpoint = await FinancialPipeline(session, settings).execute(value)
            draft = await FinancialAnalyst(session, settings, model).execute(
                workspace_id=value.workspace_id,
                user_id=value.user_id,
                run_id=checkpoint.run_id,
            )
            self.assertEqual(draft.payload["status"], "draft")
        return value, settings, checkpoint, draft, model

    def args(self, value, checkpoint):
        return {
            "workspace_id": value.workspace_id,
            "user_id": value.user_id,
            "run_id": checkpoint.run_id,
        }

    async def state(self, session, checkpoint):
        return (
            await session.get(
                AnalysisRunModel, checkpoint.run_id, populate_existing=True
            )
        ).workflow_status

    def test_complete_pipeline_approval_preserves_draft_and_hands_off_to_compiler(self):
        async def scenario():
            value, settings, checkpoint, draft, model = await self.setup_review(
                with_growth
            )
            critic = RecordingCritic()
            async with self.sessions.begin() as session:
                result = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                self.assertEqual(result.payload["status"], "approved")
                self.assertEqual(len(result.payload["checks"]), 23)
                self.assertEqual(
                    result.payload["approved_object_ids"],
                    sorted(f["finding_id"] for f in draft.payload["findings"]),
                )
                self.assertEqual(await self.state(session, checkpoint), "compiling")
                audit_input = await session.scalar(
                    select(StageResultModel).where(
                        StageResultModel.stage_name == "financial_audit_input"
                    )
                )
                self.assertEqual(
                    semantic_hash(audit_input.payload), audit_input.input_hash
                )
                self.assertEqual(len(audit_input.payload["deterministic_checks"]), 5)
                self.assertIsNone(
                    (
                        await session.get(AnalysisRunModel, checkpoint.run_id)
                    ).completed_at
                )
                original = await session.get(
                    StageResultModel,
                    uuid5(TRACE_NAMESPACE, f"analyst:{checkpoint.run_id}:initial"),
                )
                self.assertEqual(original.payload, draft.payload)
                self.assertFalse(original.payload["audited"])
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))), 9
                )
                self.assertEqual(
                    await session.scalar(select(func.count(Analysis.id))), 0
                )
                self.assertEqual(
                    await session.scalar(select(func.count(UsageCounter.id))), 0
                )
                replay = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.payload, result.payload)
                replay.payload["approved_object_ids"].clear()
                self.assertTrue(
                    (
                        await FinancialAuditor(session, settings, critic).execute(
                            **self.args(value, checkpoint)
                        )
                    ).payload["approved_object_ids"]
                )
                self.assertEqual(
                    (await FinancialPipeline(session, settings).execute(value)).payload,
                    checkpoint.payload,
                )
                self.assertEqual(
                    (
                        await FinancialAnalyst(session, settings, model).execute(
                            **self.args(value, checkpoint)
                        )
                    ).payload,
                    draft.payload,
                )
                self.assertEqual(len(critic.calls), 1)

        asyncio.run(scenario())

    def test_concurrent_same_audit_calls_critic_once(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            critic = RecordingCritic()

            async def invoke():
                async with self.sessions.begin() as session:
                    return await FinancialAuditor(session, settings, critic).execute(
                        **self.args(value, checkpoint)
                    )

            first, second = await asyncio.gather(invoke(), invoke())
            self.assertEqual(first.payload, second.payload)
            self.assertEqual(sorted((first.replayed, second.replayed)), [False, True])
            self.assertEqual(len(critic.calls), 1)

        asyncio.run(scenario())

    def test_warning_revision_and_blocked_semantic_judgments_control_run_state(self):
        async def scenario():
            for outcome, status, state in (
                ("warning", "approved_with_warnings", "compiling"),
                ("revise", "revision_required", "analyzing"),
                ("block", "blocked", "blocked"),
            ):
                value, settings, checkpoint, _, _ = await self.setup_review()
                critic = RecordingCritic(
                    lambda raw, context, outcome=outcome: judgment(
                        raw, "causality", outcome
                    )
                )
                async with self.sessions.begin() as session:
                    result = await FinancialAuditor(session, settings, critic).execute(
                        **self.args(value, checkpoint)
                    )
                    self.assertEqual(result.payload["status"], status)
                    self.assertEqual(await self.state(session, checkpoint), state)
                    self.assertEqual(
                        bool(result.payload["required_revisions"]), outcome != "warning"
                    )
                    self.assertEqual(
                        (
                            await FinancialPipeline(session, settings).execute(value)
                        ).payload,
                        checkpoint.payload,
                    )

        asyncio.run(scenario())

    def test_revision_loop_corrects_wording_and_reaudits_without_mutating_prior_stages(
        self,
    ):
        async def scenario():
            def overstate(raw, context):
                raw["findings"][0]["title"] = "The metric proves the cause"
                return raw

            value, settings, checkpoint, draft, initial_model = await self.setup_review(
                model=RecordingModel(overstate)
            )
            critic = RecordingCritic(
                lambda raw, ctx: (
                    judgment(raw, "causality", "revise")
                    if "proves the cause" in ctx["draft"]["findings"][0]["title"]
                    else raw
                )
            )
            revised_model = RecordingModel()
            async with self.sessions.begin() as session:
                workflow = FinancialReviewWorkflow(
                    session, settings, revised_model, critic
                )
                result = await workflow.execute(**self.args(value, checkpoint))
                self.assertEqual(result["status"], "approved")
                self.assertEqual(len(result["audits"]), 2)
                self.assertEqual(len(result["revisions"]), 1)
                self.assertEqual(len(revised_model.calls), 1)
                self.assertEqual(len(critic.calls), 2)
                feedback = revised_model.calls[0]["revision"]
                self.assertEqual(
                    feedback["previous_findings"], draft.payload["findings"]
                )
                self.assertTrue(feedback["required_revisions"])
                self.assertEqual(feedback["number"], 1)
                self.assertEqual(await self.state(session, checkpoint), "compiling")
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))), 13
                )
                self.assertEqual(
                    (
                        await FinancialAnalyst(
                            session, settings, initial_model
                        ).execute(**self.args(value, checkpoint))
                    ).payload,
                    draft.payload,
                )
                replay = await workflow.execute(**self.args(value, checkpoint))
                self.assertEqual(replay, result)
                self.assertEqual(len(revised_model.calls), 1)
                self.assertEqual(len(critic.calls), 2)

        asyncio.run(scenario())

    def test_two_revision_budget_stops_endless_critic_requests(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            model = RecordingModel()
            critic = RecordingCritic(
                lambda raw, ctx: judgment(raw, "claim_support", "revise")
            )
            async with self.sessions.begin() as session:
                result = await FinancialReviewWorkflow(
                    session, settings, model, critic
                ).execute(**self.args(value, checkpoint))
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(len(result["revisions"]), 2)
                self.assertEqual(len(model.calls), 2)
                self.assertEqual(len(critic.calls), 3)
                self.assertIn(
                    "audit.revision_budget_exhausted",
                    [c["reason_code"] for c in result["audits"][-1]["checks"]],
                )
                self.assertEqual(await self.state(session, checkpoint), "blocked")
                with self.assertRaises(IdempotencyConflict):
                    await FinancialAnalyst(session, settings, model).execute(
                        **self.args(value, checkpoint),
                        attempt_key="bypass",
                        revision_of=UUID(result["audits"][0]["audit_id"]),
                    )
                self.assertEqual(len(model.calls), 2)

        asyncio.run(scenario())

    def test_failed_revision_attempts_count_toward_budget_and_cannot_bypass_it(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            critic = RecordingCritic(
                lambda raw, ctx: judgment(raw, "causality", "revise")
            )

            class UnavailableModel(RecordingModel):
                async def generate(self, context):
                    self.calls.append(context)
                    raise AnalystUnavailable("SECRET")

            model = UnavailableModel()
            async with self.sessions.begin() as session:
                parent = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                for index in range(3):
                    result = await FinancialAnalyst(session, settings, model).execute(
                        **self.args(value, checkpoint),
                        attempt_key=f"retry-{index}",
                        revision_of=UUID(parent.payload["audit_id"]),
                    )
                    self.assertEqual(result.payload["status"], "failed")
                    if index < 2:
                        self.assertEqual(
                            result.payload["reason_code"],
                            "analyst.provider_unavailable",
                        )
                    else:
                        self.assertEqual(
                            result.payload["reason_code"],
                            "analyst.revision_budget_exhausted",
                        )
                self.assertEqual(len(model.calls), 2)
                self.assertEqual(await self.state(session, checkpoint), "blocked")
                replay = await FinancialAnalyst(session, settings, model).execute(
                    **self.args(value, checkpoint),
                    attempt_key="retry-0",
                    revision_of=UUID(parent.payload["audit_id"]),
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(len(model.calls), 2)

        asyncio.run(scenario())

    def test_provider_failure_and_invalid_critique_never_approve_and_retry_is_explicit(
        self,
    ):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()

            class UnavailableCritic(RecordingCritic):
                async def generate(self, context):
                    self.calls.append(context)
                    raise CriticUnavailable("SECRET headers and source data")

            bad = UnavailableCritic()
            async with self.sessions.begin() as session:
                result = await FinancialAuditor(session, settings, bad).execute(
                    **self.args(value, checkpoint)
                )
                self.assertTrue(result.payload["retryable"])
                self.assertEqual(result.payload["approved_object_ids"], [])
                self.assertNotIn("SECRET", str(result.payload))
                self.assertEqual(await self.state(session, checkpoint), "auditing")
                self.assertTrue(
                    (
                        await FinancialAuditor(session, settings, bad).execute(
                            **self.args(value, checkpoint)
                        )
                    ).replayed
                )
                malformed = RecordingCritic(
                    lambda raw, ctx: {**raw, "status": "approved"}
                )
                result = await FinancialAuditor(session, settings, malformed).execute(
                    **self.args(value, checkpoint), attempt_key="invalid-schema"
                )
                self.assertTrue(result.payload["retryable"])
                self.assertEqual(await self.state(session, checkpoint), "auditing")
                result = await FinancialAuditor(
                    session, settings, RecordingCritic()
                ).execute(**self.args(value, checkpoint), attempt_key="corrected")
                self.assertEqual(result.payload["status"], "approved")
                self.assertEqual(len(bad.calls), 1)

        asyncio.run(scenario())

    def test_deterministic_tampering_blocks_without_calling_critic(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            for model_type, attr, new_value in (
                (MetricObservationModel, "value", Decimal(999)),
                (CapabilityDecisionModel, "decision_status", "blocked"),
                (CalculationTraceModel, "exact_output", "999"),
                (SourceDocumentModel, "content_hash", "c" * 64),
            ):
                critic = RecordingCritic()
                async with self.sessions() as session, session.begin():
                    query = select(model_type)
                    if model_type is CapabilityDecisionModel:
                        query = query.where(model_type.capability == "calculate")
                    row = await session.scalar(query.limit(1))
                    setattr(row, attr, new_value)
                    await session.flush()
                    result = await FinancialAuditor(session, settings, critic).execute(
                        **self.args(value, checkpoint)
                    )
                    self.assertEqual(result.payload["status"], "blocked")
                    self.assertEqual(result.payload["approved_object_ids"], [])
                    self.assertFalse(result.payload["retryable"])
                    self.assertEqual(critic.calls, [])
                    await session.rollback()

        asyncio.run(scenario())

    def test_rehashed_finding_tampering_is_rejected_and_original_stage_not_rewritten(
        self,
    ):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            critic = RecordingCritic()
            async with self.sessions.begin() as session:
                stage = await session.get(
                    StageResultModel,
                    uuid5(TRACE_NAMESPACE, f"analyst:{checkpoint.run_id}:initial"),
                )
                altered = deepcopy(stage.payload)
                altered["findings"][0]["value_bindings"].clear()
                stage.payload, stage.output_hash = altered, semantic_hash(altered)
                await session.flush()
                result = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                self.assertEqual(result.payload["status"], "blocked")
                self.assertEqual(critic.calls, [])
                self.assertEqual(stage.payload, altered)

        asyncio.run(scenario())

    def test_permissions_flags_and_foreign_runs_are_denied(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            critic = RecordingCritic()
            for disabled in (
                replace(settings, enabled=False),
                replace(settings, persist_stages=False),
                replace(settings, financial_audit_enabled=False),
                replace(settings, internal_workspaces=frozenset()),
            ):
                async with self.sessions.begin() as session:
                    with self.assertRaises(PermissionError):
                        await FinancialAuditor(session, disabled, critic).execute(
                            **self.args(value, checkpoint)
                        )
            async with self.sessions.begin() as session:
                with self.assertRaises(PermissionError):
                    await FinancialAuditor(session, settings, critic).execute(
                        **{**self.args(value, checkpoint), "user_id": uuid4()}
                    )
                foreign = uuid4()
                session.add(Workspace(id=foreign, company_name="Other"))
                await session.flush()
                session.add(
                    WorkspaceMember(
                        workspace_id=foreign, user_id=value.user_id, role="owner"
                    )
                )
                await session.flush()
                foreign_settings = replace(
                    settings, internal_workspaces=frozenset((str(foreign),))
                )
                with self.assertRaises(PermissionError):
                    await FinancialAuditor(session, foreign_settings, critic).execute(
                        **{**self.args(value, checkpoint), "workspace_id": foreign}
                    )
            self.assertEqual(critic.calls, [])

        asyncio.run(scenario())

    def test_unauthorized_revision_and_changed_configuration_fail_before_model(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            model, critic = RecordingModel(), RecordingCritic()
            async with self.sessions.begin() as session:
                with self.assertRaises(InvalidCheckpoint):
                    await FinancialAnalyst(session, settings, model).execute(
                        **self.args(value, checkpoint),
                        attempt_key="forged",
                        revision_of=uuid4(),
                    )
                approved = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                with self.assertRaises(InvalidCheckpoint):
                    await FinancialAnalyst(session, settings, model).execute(
                        **self.args(value, checkpoint),
                        attempt_key="not-a-revision",
                        revision_of=UUID(approved.payload["audit_id"]),
                    )
                changed = RecordingCritic()
                changed.configuration["model"] = "changed-model"
                with self.assertRaises(IdempotencyConflict):
                    await FinancialAuditor(session, settings, changed).execute(
                        **self.args(value, checkpoint)
                    )
                self.assertEqual(changed.calls, [])
                self.assertEqual(model.calls, [])

        asyncio.run(scenario())

    def test_missing_audit_input_rejects_replay_without_another_critic_call(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            critic = RecordingCritic()
            async with self.sessions.begin() as session:
                await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                saved = await session.scalar(
                    select(StageResultModel).where(
                        StageResultModel.stage_name == "financial_audit_input"
                    )
                )
                await session.delete(saved)
                await session.flush()
                with self.assertRaises(InvalidCheckpoint):
                    await FinancialAuditor(session, settings, critic).execute(
                        **self.args(value, checkpoint)
                    )
                self.assertEqual(len(critic.calls), 1)

        asyncio.run(scenario())

    def test_unexpected_write_failure_rolls_back_audit_and_session_can_retry(self):
        async def scenario():
            value, settings, checkpoint, _, _ = await self.setup_review()
            critic = RecordingCritic()
            original = V2Repository.persist_stage

            async def fail(repository, stage):
                if stage.stage_name == "financial_audit":
                    raise RuntimeError("injected audit persistence failure")
                return await original(repository, stage)

            async with self.sessions.begin() as session:
                with patch.object(
                    V2Repository, "persist_stage", fail
                ), self.assertRaisesRegex(
                    RuntimeError, "injected audit persistence failure"
                ):
                    await FinancialAuditor(session, settings, critic).execute(
                        **self.args(value, checkpoint)
                    )
                self.assertEqual(await self.state(session, checkpoint), "auditing")
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))), 7
                )
                result = await FinancialAuditor(session, settings, critic).execute(
                    **self.args(value, checkpoint)
                )
                self.assertEqual(result.payload["status"], "approved")

        asyncio.run(scenario())

    def test_critic_cannot_mutate_canonical_context_or_draft(self):
        async def scenario():
            value, settings, checkpoint, draft, _ = await self.setup_review()

            def mutate(raw, context):
                context["draft"]["findings"].clear()
                context["evidence_context"]["quality"]["decisions"].clear()
                return raw

            async with self.sessions.begin() as session:
                result = await FinancialAuditor(
                    session, settings, RecordingCritic(mutate)
                ).execute(**self.args(value, checkpoint))
                self.assertEqual(result.payload["status"], "approved")
                self.assertEqual(
                    result.payload["approved_object_ids"],
                    [draft.payload["findings"][0]["finding_id"]],
                )
                original = await session.get(
                    StageResultModel,
                    uuid5(TRACE_NAMESPACE, f"analyst:{checkpoint.run_id}:initial"),
                )
                self.assertEqual(original.payload, draft.payload)

        asyncio.run(scenario())
