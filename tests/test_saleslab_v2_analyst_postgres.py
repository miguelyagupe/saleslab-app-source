"""Real PostgreSQL pipeline → analyst → audit handoff; provider is injected."""

import asyncio
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

URL = os.environ.get("TEST_DATABASE_URL", "")
if os.environ.get("SALESLAB_REQUIRE_POSTGRES_TESTS") == "1" and not URL:
    raise RuntimeError("TEST_DATABASE_URL is required for F-06 integration")
os.environ.setdefault("DATABASE_URL", URL or "postgresql://unused/analyst_test")

from analyst_fixtures import RecordingModel
from pipeline_fixtures import change_row, request, with_growth

from database import Base
from models import Analysis, UsageCounter, User, Workspace, WorkspaceMember
from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.analyst.contracts import AnalystUnavailable
from saleslab_v2.analyst.service import FinancialAnalyst
from saleslab_v2.config import V2Settings
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.persistence.metadata import legacy_tables
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    CalculationTraceModel,
    CapabilityDecisionModel,
    CoverageAssessmentModel,
    MetricObservationModel,
    ReconciliationResultModel,
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


@unittest.skipUnless(
    URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class AnalystPostgresTests(unittest.TestCase):
    def test_duplicate_plan_aliases_reuse_persisted_metric_and_analyst_evidence(self):
        async def scenario():
            def duplicate(value):
                return replace(
                    value,
                    metrics=(
                        *value.metrics,
                        replace(value.metrics[0], key="same_metric_alias"),
                    ),
                )

            value, settings, checkpoint = await self.setup_pipeline(duplicate)
            async with self.sessions.begin() as session:
                result = await self.execute(
                    session, settings, RecordingModel(), value, checkpoint
                )
                self.assertEqual(result.payload["status"], "draft")
                self.assertEqual(len(result.payload["financial_snapshot"]), 1)
                self.assertEqual(
                    await session.scalar(select(func.count(MetricObservationModel.id))),
                    1,
                )

        asyncio.run(scenario())

    def test_replay_rejects_missing_or_corrupt_analyst_input_audit(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()
            model = RecordingModel()
            async with self.sessions.begin() as session:
                await self.execute(session, settings, model, value, checkpoint)
            for action in ("delete", "hash", "payload"):
                async with self.sessions() as session, session.begin():
                    saved = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.stage_name == "financial_analyst_input"
                        )
                    )
                    if action == "delete":
                        await session.delete(saved)
                    elif action == "hash":
                        saved.output_hash = "c" * 64
                    else:
                        saved.payload = {"corrupt": True}
                    await session.flush()
                    with self.assertRaises(InvalidCheckpoint):
                        await self.execute(session, settings, model, value, checkpoint)
                    await session.rollback()
            self.assertEqual(len(model.calls), 1)

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

    async def setup_pipeline(self, transform=None):
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
            session.add(Workspace(id=value.workspace_id, company_name="Synthetic F-06"))
            session.add(
                User(id=value.user_id, email="analyst@example.test", password_hash="x")
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
        )
        async with self.sessions.begin() as session:
            checkpoint = await FinancialPipeline(session, settings).execute(value)
        return value, settings, checkpoint

    async def execute(self, session, settings, model, value, checkpoint, **kwargs):
        return await FinancialAnalyst(session, settings, model).execute(
            workspace_id=value.workspace_id,
            user_id=value.user_id,
            run_id=checkpoint.run_id,
            **kwargs,
        )

    def test_persisted_checkpoint_to_evidence_bound_draft_and_exact_replay(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline(with_growth)
            model = RecordingModel()
            async with self.sessions.begin() as session:
                result = await self.execute(session, settings, model, value, checkpoint)
                self.assertEqual(result.payload["status"], "draft")
                self.assertFalse(result.payload["audited"])
                self.assertEqual(len(result.payload["findings"]), 3)
                self.assertEqual(len(result.payload["data_confidence"]["decisions"]), 9)
                self.assertTrue(result.payload["missing_data_requests"])
                self.assertEqual(result.payload["forecasts"], [])
                self.assertEqual(result.payload["recommendations"], [])
                self.assertTrue(
                    all(
                        e["source_references"]
                        for e in result.payload["financial_snapshot"]
                    )
                )
                run = await session.get(AnalysisRunModel, checkpoint.run_id)
                self.assertEqual(run.workflow_status, "auditing")
                self.assertIsNone(run.completed_at)
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))), 7
                )
                self.assertEqual(
                    await session.scalar(select(func.count(MetricObservationModel.id))),
                    3,
                )
                self.assertEqual(
                    await session.scalar(select(func.count(CalculationTraceModel.id))),
                    3,
                )
                self.assertEqual(
                    await session.scalar(select(func.count(Analysis.id))), 0
                )
                self.assertEqual(
                    await session.scalar(select(func.count(UsageCounter.id))), 0
                )
                saved = await session.scalar(
                    select(StageResultModel).where(
                        StageResultModel.stage_name == "financial_analyst_input"
                    )
                )
                self.assertEqual(saved.output_hash, semantic_hash(saved.payload))
                self.assertEqual(saved.payload["context"], model.calls[0])
                replay = await self.execute(session, settings, model, value, checkpoint)
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.payload, result.payload)
                replay.payload["findings"].clear()
                self.assertEqual(
                    len(
                        (
                            await self.execute(
                                session, settings, model, value, checkpoint
                            )
                        ).payload["findings"]
                    ),
                    3,
                )
                deterministic_replay = await FinancialPipeline(
                    session, settings
                ).execute(value)
                self.assertEqual(deterministic_replay.payload, checkpoint.payload)
                self.assertEqual(len(model.calls), 1)

        asyncio.run(scenario())

    def test_concurrent_identical_calls_invoke_model_once(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()
            model = RecordingModel()

            async def invoke():
                async with self.sessions.begin() as session:
                    return await self.execute(
                        session, settings, model, value, checkpoint
                    )

            first, second = await asyncio.gather(invoke(), invoke())
            self.assertEqual(first.payload, second.payload)
            self.assertEqual(sorted((first.replayed, second.replayed)), [False, True])
            self.assertEqual(len(model.calls), 1)
            async with self.sessions() as session:
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))), 7
                )

        asyncio.run(scenario())

    def test_blocked_or_mapping_required_pipeline_never_invokes_model(self):
        async def scenario():
            for transform in (
                lambda v: change_row(v, "assets", text="999"),
                lambda v: change_row(v, "revenue", name="unmapped unknown"),
            ):
                value, settings, checkpoint = await self.setup_pipeline(transform)
                model = RecordingModel()
                async with self.sessions.begin() as session:
                    result = await self.execute(
                        session, settings, model, value, checkpoint
                    )
                    self.assertEqual(result.payload["status"], "blocked")
                    self.assertEqual(model.calls, [])
                    self.assertEqual(
                        await session.scalar(select(func.count(StageResultModel.id))), 5
                    )
                    self.assertEqual(
                        (
                            await session.get(AnalysisRunModel, checkpoint.run_id)
                        ).workflow_status,
                        checkpoint.payload["workflow_status"],
                    )

        asyncio.run(scenario())

    def test_adversarial_outputs_are_persisted_as_failed_attempts_and_can_retry_explicitly(
        self,
    ):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()
            for n, mutate in enumerate(
                (
                    lambda raw: raw.update(
                        recommendations=[{"action": "hedge half the debt"}]
                    ),
                    lambda raw: raw["findings"][0].update(capability="compare"),
                    lambda raw: raw["findings"][0].update(evidence_ids=[str(uuid4())]),
                    lambda raw: raw["findings"][0].update(statement="Margin is 99%."),
                )
            ):

                def transform(raw, context, mutate=mutate):
                    mutate(raw)
                    return raw

                model = RecordingModel(transform)
                async with self.sessions.begin() as session:
                    result = await self.execute(
                        session,
                        settings,
                        model,
                        value,
                        checkpoint,
                        attempt_key=f"rejected-{n}",
                    )
                    self.assertEqual(
                        result.payload["reason_code"], "analyst.invalid_output"
                    )
                    self.assertEqual(
                        (
                            await session.get(AnalysisRunModel, checkpoint.run_id)
                        ).workflow_status,
                        "calculating",
                    )
                    replay = await self.execute(
                        session,
                        settings,
                        model,
                        value,
                        checkpoint,
                        attempt_key=f"rejected-{n}",
                    )
                    self.assertTrue(replay.replayed)
                    self.assertEqual(len(model.calls), 1)
            model = RecordingModel()
            async with self.sessions.begin() as session:
                result = await self.execute(
                    session, settings, model, value, checkpoint, attempt_key="corrected"
                )
                self.assertEqual(result.payload["status"], "draft")
                self.assertEqual(
                    await session.scalar(select(func.count(MetricObservationModel.id))),
                    1,
                )

        asyncio.run(scenario())

    def test_provider_error_is_sanitized_and_retry_requires_new_attempt_key(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()

            class Unavailable(RecordingModel):
                async def generate(self, context):
                    self.calls.append(context)
                    raise AnalystUnavailable("SECRET token and source data")

            model = Unavailable()
            async with self.sessions.begin() as session:
                result = await self.execute(session, settings, model, value, checkpoint)
                self.assertEqual(
                    result.payload["reason_code"], "analyst.provider_unavailable"
                )
                self.assertNotIn("SECRET", str(result.payload))
                stage = await session.scalar(
                    select(StageResultModel).where(
                        StageResultModel.stage_name == "financial_analyst"
                    )
                )
                self.assertEqual(stage.status, "failed")
                self.assertNotIn("SECRET", str(stage.error))
                recovered = await self.execute(
                    session,
                    settings,
                    RecordingModel(),
                    value,
                    checkpoint,
                    attempt_key="retry",
                )
                self.assertEqual(recovered.payload["status"], "draft")

        asyncio.run(scenario())

    def test_input_snapshot_survives_model_mutation(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()

            def transform(raw, context):
                context["evidence"][0]["value"] = "999"
                context["quality"]["decisions"].clear()
                return raw

            async with self.sessions.begin() as session:
                result = await self.execute(
                    session, settings, RecordingModel(transform), value, checkpoint
                )
                self.assertEqual(result.payload["financial_snapshot"][0]["value"], "10")
                self.assertEqual(len(result.payload["data_confidence"]["decisions"]), 9)

        asyncio.run(scenario())

    def test_disabled_flags_membership_and_cross_workspace_run_are_denied(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()
            model = RecordingModel()
            for denied in (
                replace(settings, enabled=False),
                replace(settings, persist_stages=False),
                replace(settings, financial_analyst_enabled=False),
                replace(settings, internal_workspaces=frozenset()),
            ):
                async with self.sessions.begin() as session:
                    with self.assertRaises(PermissionError):
                        await self.execute(session, denied, model, value, checkpoint)
            async with self.sessions.begin() as session:
                with self.assertRaises(PermissionError):
                    await FinancialAnalyst(session, settings, model).execute(
                        workspace_id=value.workspace_id,
                        user_id=uuid4(),
                        run_id=checkpoint.run_id,
                    )
                foreign = uuid4()
                session.add(Workspace(id=foreign, company_name="Other workspace"))
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
                    await FinancialAnalyst(session, foreign_settings, model).execute(
                        workspace_id=foreign,
                        user_id=value.user_id,
                        run_id=checkpoint.run_id,
                    )
            self.assertEqual(model.calls, [])

        asyncio.run(scenario())

    def test_live_gate_metric_trace_and_checkpoint_changes_fail_before_model(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()
            for model_type, attr, new_value in (
                (CapabilityDecisionModel, "decision_status", "blocked"),
                (MetricObservationModel, "value", Decimal(999)),
                (CalculationTraceModel, "exact_output", "999"),
                (ReconciliationResultModel, "status", "critical"),
                (SourceDocumentModel, "content_hash", "b" * 64),
                (CoverageAssessmentModel, "quality_level", "insufficient"),
                (StageResultModel, "output_hash", "f" * 64),
            ):
                model = RecordingModel()
                async with self.sessions() as session, session.begin():
                    row = await session.scalar(select(model_type).limit(1))
                    if model_type is CapabilityDecisionModel:
                        row = await session.scalar(
                            select(model_type).where(
                                model_type.capability == "calculate"
                            )
                        )
                    setattr(row, attr, new_value)
                    await session.flush()
                    with self.assertRaises(InvalidCheckpoint):
                        await self.execute(session, settings, model, value, checkpoint)
                    await session.rollback()
                self.assertEqual(model.calls, [])

        asyncio.run(scenario())

    def test_configuration_conflicts_and_new_attempt_after_success_do_not_call_model(
        self,
    ):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()
            async with self.sessions.begin() as session:
                await self.execute(
                    session, settings, RecordingModel(), value, checkpoint
                )
                changed = RecordingModel()
                changed.configuration["model"] = "different"
                with self.assertRaises(IdempotencyConflict):
                    await self.execute(session, settings, changed, value, checkpoint)
                with self.assertRaises(IdempotencyConflict):
                    await self.execute(
                        session, settings, changed, value, checkpoint, attempt_key="new"
                    )
                self.assertEqual(changed.calls, [])

        asyncio.run(scenario())

    def test_unexpected_persistence_failure_rolls_back_and_session_can_retry(self):
        async def scenario():
            value, settings, checkpoint = await self.setup_pipeline()
            original = V2Repository.persist_stage

            async def fail_output(repository, stage):
                if stage.stage_name == "financial_analyst":
                    raise RuntimeError("injected write failure")
                return await original(repository, stage)

            model = RecordingModel()
            async with self.sessions.begin() as session:
                with (
                    patch.object(V2Repository, "persist_stage", fail_output),
                    self.assertRaisesRegex(RuntimeError, "injected write failure"),
                ):
                    await self.execute(session, settings, model, value, checkpoint)
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))), 5
                )
                self.assertEqual(
                    (
                        await session.get(AnalysisRunModel, checkpoint.run_id)
                    ).workflow_status,
                    "calculating",
                )
                result = await self.execute(session, settings, model, value, checkpoint)
                self.assertEqual(result.payload["status"], "draft")
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))), 7
                )

        asyncio.run(scenario())
