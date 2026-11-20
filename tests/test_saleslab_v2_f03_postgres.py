"""Required live PostgreSQL contract coverage for the F-03 migration."""

# ruff: noqa: E402

import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL", "")
if os.environ.get("SALESLAB_REQUIRE_POSTGRES_TESTS") == "1" and not URL:
    raise RuntimeError("TEST_DATABASE_URL is required for F-03 PostgreSQL tests")
os.environ.setdefault("DATABASE_URL", URL or "postgresql://unused/f03_test")

from database import Base
import models  # noqa: F401
from models import User, Workspace
from saleslab_v2.domain.enums import LanguageCode, ModuleType
from saleslab_v2.domain.idempotency import IdempotencyConflict
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun
from saleslab_v2.domain.value_objects import Period
from saleslab_v2.metrics.engine import MetricEngine, MetricInput
from saleslab_v2.metrics.financial_v1 import FINANCIAL_V1
from saleslab_v2.persistence.metadata import V2_TABLE_NAMES, legacy_tables
from saleslab_v2.persistence.models import CalculationTraceModel, MetricObservationModel
from saleslab_v2.persistence.repositories import V2Repository


def async_url(value):
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def alembic(*args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": URL},
        check=True,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(
    URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class F03PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = make_url(async_url(URL))
        if parsed.host not in {"localhost", "127.0.0.1", "postgres"} or not any(
            marker in (parsed.database or "").lower()
            for marker in ("test", "f03", "disposable")
        ):
            raise RuntimeError("refusing a non-local or non-disposable database")
        cls.engine = create_async_engine(async_url(URL), poolclass=NullPool)
        cls.sessions = async_sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.engine.dispose())

    async def reset_to_0003(self):
        async with self.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=legacy_tables())
            )
        alembic("stamp", "20260727_0001")
        alembic("upgrade", "20260727_0003")

    async def schema(self, tables=None):
        def fingerprint(connection):
            inspector = inspect(connection)
            names = sorted(tables or inspector.get_table_names())
            return {
                name: {
                    "columns": [
                        (c["name"], str(c["type"]), c["nullable"])
                        for c in inspector.get_columns(name)
                    ],
                    "pk": tuple(
                        inspector.get_pk_constraint(name)["constrained_columns"]
                    ),
                    "fks": sorted(
                        (
                            tuple(fk["constrained_columns"]),
                            fk["referred_table"],
                            tuple(fk["referred_columns"]),
                            (fk.get("options") or {}).get("ondelete"),
                        )
                        for fk in inspector.get_foreign_keys(name)
                    ),
                    "uniques": sorted(
                        tuple(item["column_names"])
                        for item in inspector.get_unique_constraints(name)
                    ),
                    "checks": sorted(
                        item["sqltext"].replace("::text", "")
                        for item in inspector.get_check_constraints(name)
                    ),
                    "indexes": sorted(
                        tuple(item["column_names"])
                        for item in inspector.get_indexes(name)
                        if not item.get("duplicates_constraint")
                    ),
                }
                for name in names
            }

        async with self.engine.connect() as connection:
            return await connection.run_sync(fingerprint)

    def test_0003_0004_round_trip_structure_and_legacy_invariance(self):
        async def scenario():
            await self.reset_to_0003()
            legacy_names = sorted(
                set((await self.schema())) - V2_TABLE_NAMES - {"alembic_version"}
            )
            legacy_before = await self.schema(legacy_names)
            schema_0003 = await self.schema()
            alembic("upgrade", "20260729_0004")
            schema_0004 = await self.schema()
            self.assertEqual(
                set(schema_0004) - set(schema_0003),
                {"calculation_traces_v2", "metric_observations_v2"},
            )
            self.assertEqual(await self.schema(legacy_names), legacy_before)

            expected = {}
            for table in (
                CalculationTraceModel.__table__,
                MetricObservationModel.__table__,
            ):
                expected[table.name] = {
                    "columns": [
                        (
                            c.name,
                            str(c.type).replace("DATETIME", "TIMESTAMP"),
                            c.nullable,
                        )
                        for c in table.columns
                    ],
                    "pk": tuple(c.name for c in table.primary_key.columns),
                    "fks": sorted(
                        (
                            tuple(c.parent.name for c in fk.elements),
                            next(iter(fk.elements)).column.table.name,
                            tuple(c.column.name for c in fk.elements),
                            fk.ondelete,
                        )
                        for fk in table.foreign_key_constraints
                    ),
                    "uniques": sorted(
                        tuple(c.name for c in constraint.columns)
                        for constraint in table.constraints
                        if constraint.__class__.__name__ == "UniqueConstraint"
                    ),
                    "checks": sorted(
                        str(constraint.sqltext)
                        for constraint in table.constraints
                        if constraint.__class__.__name__ == "CheckConstraint"
                    ),
                    "indexes": sorted(
                        tuple(c.name for c in index.columns) for index in table.indexes
                    ),
                }
            self.assertEqual(
                schema_0004["calculation_traces_v2"], expected["calculation_traces_v2"]
            )
            self.assertEqual(
                schema_0004["metric_observations_v2"],
                expected["metric_observations_v2"],
            )

            alembic("downgrade", "20260727_0003")
            self.assertEqual(await self.schema(), schema_0003)
            self.assertEqual(await self.schema(legacy_names), legacy_before)
            alembic("upgrade", "20260729_0004")
            self.assertEqual(await self.schema(), schema_0004)

        asyncio.run(scenario())

    def test_persistence_replay_conflict_scope_transactions_and_cascades(self):
        async def scenario():
            await self.reset_to_0003()
            alembic("upgrade", "20260729_0004")
            user_id, workspace_a, workspace_b = uuid4(), uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f03@example.test", password_hash="x")
                )
                session.add_all(
                    [
                        Workspace(id=workspace_a, company_name="A"),
                        Workspace(id=workspace_b, company_name="B"),
                    ]
                )
            run = AnalysisRun(
                workspace_a,
                user_id,
                ModuleType.FINANCIAL,
                "1",
                "1",
                LanguageContext(
                    (LanguageCode.PT,),
                    LanguageCode.PT,
                    LanguageCode.PT,
                    LocaleTag.PT_PT,
                ),
                (),
                (),
                "f03",
                "replay",
                allow_empty_sources=True,
            )
            period = Period(
                date(2026, 1, 1),
                date(2026, 12, 31),
                granularity="year",
                comparison_basis="current",
            )
            observation, trace = MetricEngine(FINANCIAL_V1).calculate(
                "free_cash_flow",
                {
                    "cfo": MetricInput(
                        uuid4(),
                        "cfo",
                        Decimal("100"),
                        "monetary",
                        "EUR",
                        period,
                        "consolidated",
                    ),
                    "capex": MetricInput(
                        uuid4(),
                        "capex",
                        Decimal("30"),
                        "monetary",
                        "EUR",
                        period,
                        "consolidated",
                    ),
                },
            )
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_a)
                await repo.create_run(run)
                with self.assertRaises(IdempotencyConflict):
                    await repo.persist_metric(
                        run.run_id, replace(observation, value=Decimal("71")), trace
                    )
                self.assertEqual(
                    await session.scalar(select(func.count(CalculationTraceModel.id))),
                    0,
                )
                self.assertEqual(
                    await session.scalar(select(func.count(MetricObservationModel.id))),
                    0,
                )
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_a)
                stored, created = await repo.persist_metric(
                    run.run_id, observation, trace
                )
                self.assertTrue(created)
                replay, created = await repo.persist_metric(
                    run.run_id, observation, trace
                )
                self.assertFalse(created)
                self.assertEqual(replay.id, stored.id)
                with self.assertRaises(IdempotencyConflict):
                    await repo.persist_metric(
                        run.run_id, replace(observation, value=Decimal("71")), trace
                    )
                with self.assertRaises(PermissionError):
                    await V2Repository(session, workspace_b).persist_metric(
                        run.run_id, observation, trace
                    )

            async def concurrent_replay():
                async with self.sessions.begin() as session:
                    return await V2Repository(session, workspace_a).persist_metric(
                        run.run_id, observation, trace
                    )

            concurrent = await asyncio.gather(concurrent_replay(), concurrent_replay())
            self.assertEqual({result[0].id for result in concurrent}, {stored.id})
            self.assertTrue(all(not result[1] for result in concurrent))

            run_a2 = replace(run, run_id=uuid4(), idempotency_key="replay-2")
            run_b = replace(
                run,
                workspace_id=workspace_b,
                run_id=uuid4(),
                idempotency_key="replay-b",
            )
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_a).create_run(run_a2)
                await V2Repository(session, workspace_b).create_run(run_b)
                stored_a2, _ = await V2Repository(session, workspace_a).persist_metric(
                    run_a2.run_id, observation, trace
                )
                stored_b, _ = await V2Repository(session, workspace_b).persist_metric(
                    run_b.run_id, observation, trace
                )
                self.assertEqual(len({stored.id, stored_a2.id, stored_b.id}), 3)

            rollback_trace = replace(trace, metric_id="rollback")
            rollback_observation = replace(
                observation,
                observation_id=uuid4(),
                metric_id="rollback",
                calculation_trace_id=rollback_trace.trace_id,
                calculation_trace_hash=rollback_trace.trace_hash,
            )
            try:
                async with self.sessions.begin() as session:
                    await V2Repository(session, workspace_a).persist_metric(
                        run.run_id, rollback_observation, rollback_trace
                    )
                    raise RuntimeError("force rollback")
            except RuntimeError:
                pass
            async with self.sessions() as session:
                self.assertIsNone(
                    await session.scalar(
                        select(CalculationTraceModel).where(
                            CalculationTraceModel.id == rollback_trace.trace_id
                        )
                    )
                )
                await session.execute(
                    text("DELETE FROM workspaces WHERE id=:id"), {"id": workspace_a}
                )
                await session.commit()
                self.assertIsNone(await session.get(MetricObservationModel, stored.id))
                self.assertIsNotNone(
                    await session.get(MetricObservationModel, stored_b.id)
                )

        asyncio.run(scenario())
