"""Real PostgreSQL 16 end-to-end tests; never use production data or models."""

# ruff: noqa: E402

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

URL = os.environ.get("TEST_DATABASE_URL", "")
if os.environ.get("SALESLAB_REQUIRE_POSTGRES_TESTS") == "1" and not URL:
    raise RuntimeError("TEST_DATABASE_URL is required for pipeline integration")
os.environ.setdefault("DATABASE_URL", URL or "postgresql://unused/pipeline_test")

from database import Base
from models import Analysis, User, UsageCounter, Workspace, WorkspaceMember
from pipeline_fixtures import change_row, request, with_growth
from saleslab_v2.config import V2Settings
from saleslab_v2.domain.enums import Capability
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.persistence.metadata import legacy_tables
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    CalculationTraceModel,
    CapabilityDecisionModel,
    CoverageAssessmentModel,
    DataQualityIssueModel,
    MetricObservationModel,
    ReconciliationResultModel,
    SourceDocumentModel,
    StageResultModel,
)
from saleslab_v2.pipeline.contracts import STAGES
from saleslab_v2.pipeline.service import FinancialPipeline

ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    SourceDocumentModel,
    AnalysisRunModel,
    StageResultModel,
    CalculationTraceModel,
    MetricObservationModel,
    ReconciliationResultModel,
    DataQualityIssueModel,
    CoverageAssessmentModel,
    CapabilityDecisionModel,
)


def async_url(value):
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


@unittest.skipUnless(
    URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class PipelinePostgresTests(unittest.TestCase):
    def test_chained_calculations_and_temporal_comparisons_preserve_scoped_trace_links(
        self,
    ):
        async def scenario():
            value, settings = await self.setup_request()
            value = with_growth(value)
            async with self.sessions.begin() as session:
                result = await FinancialPipeline(session, settings).execute(value)
                self.assertTrue(result.ready)
                observations = {
                    o.metric_id: o
                    for o in (
                        await session.scalars(select(MetricObservationModel))
                    ).all()
                }
                self.assertEqual(observations["revenue_growth_reported_pct"].value, 25)
                self.assertEqual(len(observations), 3)
                trace = await session.get(
                    CalculationTraceModel,
                    observations["revenue_growth_reported_pct"].calculation_trace_id,
                )
                current = next(
                    i for i in trace.inputs if i["role"] == "revenue_current"
                )
                self.assertEqual(
                    current["observation_id"], str(observations["revenue"].id)
                )
                self.assertEqual(current["metric_id"], "revenue_current")
                gate = await session.scalar(
                    select(CapabilityDecisionModel).where(
                        CapabilityDecisionModel.capability == "compare"
                    )
                )
                self.assertEqual(gate.decision_status, "allowed")
                replay = await FinancialPipeline(session, settings).execute(value)
                self.assertEqual(replay.payload, result.payload)
                self.assertEqual(
                    await session.scalar(select(func.count(CalculationTraceModel.id))),
                    3,
                )

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

    async def setup_request(self):
        async with self.engine.begin() as connection:
            version = await connection.scalar(text("SHOW server_version_num"))
            self.assertEqual(int(version) // 10000, 16)
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=legacy_tables())
            )
        for args in (("stamp", "20260727_0001"), ("upgrade", "head")):
            subprocess.run(
                [sys.executable, "-m", "alembic", *args],
                cwd=ROOT,
                env={**os.environ, "DATABASE_URL": URL},
                capture_output=True,
                text=True,
                check=True,
            )
        value = request(uuid4(), uuid4())
        async with self.sessions.begin() as session:
            session.add(
                Workspace(id=value.workspace_id, company_name="Synthetic Pipeline")
            )
            session.add(
                User(id=value.user_id, email="pipeline@example.test", password_hash="x")
            )
            await session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=value.workspace_id, user_id=value.user_id, role="owner"
                )
            )
        settings = V2Settings(True, True, frozenset((str(value.workspace_id),)))
        return value, settings

    async def counts(self, session):
        result = {}
        for table in TABLES:
            result[table.__tablename__] = await session.scalar(
                select(func.count(table.id))
            )
        return result

    def test_real_source_to_gates_persists_an_auditable_checkpoint_and_replays(self):
        async def scenario():
            value, settings = await self.setup_request()
            async with self.sessions.begin() as session:
                result = await FinancialPipeline(session, settings).execute(value)
                self.assertTrue(result.ready)
                self.assertFalse(result.replayed)
                stages = (
                    await session.scalars(
                        select(StageResultModel).where(
                            StageResultModel.run_id == result.run_id
                        )
                    )
                ).all()
                self.assertEqual({s.stage_name for s in stages}, set(STAGES))
                self.assertTrue(
                    all(s.output_hash == semantic_hash(s.payload) for s in stages)
                )
                stage_by_name = {s.stage_name: s for s in stages}
                self.assertEqual(
                    semantic_hash(stage_by_name["normalization"].payload["request"]),
                    value.fingerprint,
                )
                for previous, following in zip(STAGES[:3], STAGES[1:4]):
                    self.assertEqual(
                        stage_by_name[previous].output_hash,
                        stage_by_name[following].input_hash,
                    )
                self.assertEqual(
                    (await session.scalar(select(AnalysisRunModel))).workflow_status,
                    "calculating",
                )
                metric = await session.scalar(select(MetricObservationModel))
                trace = await session.scalar(select(CalculationTraceModel))
                self.assertEqual(metric.value, 10)
                self.assertEqual(metric.calculation_trace_id, trace.id)
                self.assertEqual(
                    (await session.scalar(select(ReconciliationResultModel))).status,
                    "pass",
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    len(Capability),
                )
                self.assertEqual(
                    await session.scalar(select(func.count(Analysis.id))), 0
                )
                self.assertEqual(
                    await session.scalar(select(func.count(UsageCounter.id))), 0
                )
                before = await self.counts(session)
                result.payload["ready"] = (
                    False  # Response mutation cannot change saved evidence.
                )
            async with self.sessions.begin() as session:
                replay = await FinancialPipeline(session, settings).execute(value)
                self.assertTrue(replay.replayed and replay.ready)
                self.assertEqual(replay.run_id, result.run_id)
                self.assertEqual(await self.counts(session), before)

        asyncio.run(scenario())

    def test_concurrent_retries_create_one_run_one_source_and_one_stage_chain(self):
        async def scenario():
            value, settings = await self.setup_request()

            async def invoke():
                async with self.sessions.begin() as session:
                    return await FinancialPipeline(session, settings).execute(value)

            first, replay = await asyncio.gather(invoke(), invoke())
            self.assertEqual(first.run_id, replay.run_id)
            self.assertEqual(sorted((first.replayed, replay.replayed)), [False, True])
            self.assertEqual(first.payload, replay.payload)
            async with self.sessions.begin() as session:
                counts = await self.counts(session)
                for table in (
                    SourceDocumentModel,
                    AnalysisRunModel,
                    MetricObservationModel,
                    ReconciliationResultModel,
                ):
                    self.assertEqual(counts[table.__tablename__], 1)
                self.assertEqual(counts[StageResultModel.__tablename__], len(STAGES))

        asyncio.run(asyncio.wait_for(scenario(), timeout=25))

    def test_same_key_cannot_reuse_a_different_row_plan_profile_or_capability_request(
        self,
    ):
        async def scenario():
            value, settings = await self.setup_request()
            async with self.sessions.begin() as session:
                pipeline = FinancialPipeline(session, settings)
                await pipeline.execute(value)
                counts = await self.counts(session)
                changed = (
                    change_row(value, "net_income", text="101"),
                    replace(value, profile="financial_hedge_v1"),
                    replace(value, requested_capabilities=(Capability.FORECAST,)),
                    replace(value, reconciliations=()),
                )
                for other in changed:
                    with self.assertRaises(IdempotencyConflict):
                        await pipeline.execute(other)
                    self.assertEqual(await self.counts(session), counts)
                self.assertTrue((await pipeline.execute(value)).replayed)

        asyncio.run(scenario())

    def test_data_failures_are_persisted_and_never_reported_ready(self):
        async def scenario(reference, changes, status):
            value, settings = await self.setup_request()
            value = change_row(value, reference, **changes)
            async with self.sessions.begin() as session:
                result = await FinancialPipeline(session, settings).execute(value)
                self.assertFalse(result.ready)
                self.assertEqual(result.payload["workflow_status"], status)
                self.assertEqual(
                    await session.scalar(select(func.count(StageResultModel.id))),
                    len(STAGES),
                )
                self.assertGreater(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )
                gates = (await session.scalars(select(CapabilityDecisionModel))).all()
                self.assertTrue(all(g.decision_status == "blocked" for g in gates))
                stored_ids = {
                    str(x)
                    for x in (
                        await session.scalars(select(DataQualityIssueModel.id))
                    ).all()
                }
                self.assertTrue(
                    all(set(g.supporting_issue_ids) <= stored_ids for g in gates)
                )
                self.assertTrue(
                    (await FinancialPipeline(session, settings).execute(value)).replayed
                )

        for reference, changes, status in (
            ("assets", {"text": "200"}, "blocked"),
            ("revenue", {"text": "0"}, "blocked"),
            ("revenue", {"name": "unknown"}, "mapping_required"),
        ):
            with self.subTest(reference=reference, changes=changes):
                asyncio.run(scenario(reference, changes, status))

    def test_unexpected_write_failure_rolls_back_every_stage_and_preserves_session(
        self,
    ):
        from saleslab_v2.persistence.repositories import V2Repository

        async def broken(*args, **kwargs):
            raise RuntimeError("injected storage failure")

        async def scenario():
            value, settings = await self.setup_request()
            async with self.sessions.begin() as session:
                pipeline = FinancialPipeline(session, settings)
                with patch.object(V2Repository, "persist_quality_gate", broken):
                    with self.assertRaisesRegex(
                        RuntimeError, "injected storage failure"
                    ):
                        await pipeline.execute(value)
                self.assertTrue(
                    all(count == 0 for count in (await self.counts(session)).values())
                )
                self.assertEqual(await session.scalar(text("SELECT 1")), 1)
                self.assertTrue((await pipeline.execute(value)).ready)

        asyncio.run(scenario())

    def test_flags_allowlist_membership_and_immutable_source_metadata_are_enforced(
        self,
    ):
        async def scenario():
            value, settings = await self.setup_request()
            async with self.sessions.begin() as session:
                for denied in (
                    replace(settings, enabled=False),
                    replace(settings, persist_stages=False),
                    replace(settings, internal_workspaces=frozenset()),
                ):
                    with self.assertRaises(PermissionError):
                        await FinancialPipeline(session, denied).execute(value)
                with self.assertRaises(PermissionError):
                    await FinancialPipeline(session, settings).execute(
                        replace(value, user_id=uuid4())
                    )
                self.assertTrue(
                    all(count == 0 for count in (await self.counts(session)).values())
                )
                await FinancialPipeline(session, settings).execute(value)
                batch = value.sources[0]
                wrong = replace(
                    value,
                    idempotency_key="new-key",
                    sources=(
                        replace(
                            batch,
                            document=replace(batch.document, filename="changed.csv"),
                        ),
                    ),
                )
                with self.assertRaisesRegex(
                    IdempotencyConflict, "metadata is immutable"
                ):
                    await FinancialPipeline(session, settings).execute(wrong)
                self.assertEqual(
                    await session.scalar(select(func.count(AnalysisRunModel.id))), 1
                )

        asyncio.run(scenario())

    def test_source_ids_cannot_be_reused_by_another_workspace(self):
        async def scenario():
            value, settings = await self.setup_request()
            other_id = uuid4()
            async with self.sessions.begin() as session:
                await FinancialPipeline(session, settings).execute(value)
                session.add(Workspace(id=other_id, company_name="Other"))
                await session.flush()
                session.add(
                    WorkspaceMember(
                        workspace_id=other_id, user_id=value.user_id, role="manager"
                    )
                )
                await session.flush()
                batch = value.sources[0]
                foreign = replace(
                    value,
                    workspace_id=other_id,
                    sources=(
                        replace(
                            batch,
                            document=replace(batch.document, workspace_id=other_id),
                        ),
                    ),
                )
                allow = replace(
                    settings, internal_workspaces=frozenset((str(other_id),))
                )
                with self.assertRaisesRegex(PermissionError, "another workspace"):
                    await FinancialPipeline(session, allow).execute(foreign)
                self.assertEqual(
                    await session.scalar(select(func.count(AnalysisRunModel.id))), 1
                )

        asyncio.run(scenario())
