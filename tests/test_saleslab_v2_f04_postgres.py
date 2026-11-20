"""Required live PostgreSQL 16 contract coverage for the F-04 migration."""

# ruff: noqa: E402

import asyncio
from datetime import datetime, timezone
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
    raise RuntimeError("TEST_DATABASE_URL is required for F-04 PostgreSQL tests")
os.environ.setdefault("DATABASE_URL", URL or "postgresql://unused/f04_test")

from database import Base
import models  # noqa: F401
from models import User, Workspace
from saleslab_v2.domain.enums import LanguageCode, ModuleType
from saleslab_v2.domain.idempotency import IdempotencyConflict
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun
from saleslab_v2.persistence.metadata import V2_TABLE_NAMES, legacy_tables
from saleslab_v2.persistence.models import ReconciliationResultModel
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.reconciliation import (
    FINANCIAL_RULES_V1,
    InputContractError,
    ReconciliationEngine,
    ReconciliationInput,
    TolerancePolicy,
)


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


def run(workspace_id, user_id, key):
    return AnalysisRun(
        workspace_id,
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
        "f04-postgres",
        key,
        allow_empty_sources=True,
    )


def result(*, assets="100", timestamp=None):
    period = ("2026-01-01", "2026-12-31", "year", 2026, "current", 12)

    def item(role, identity, value):
        return ReconciliationInput(
            role,
            uuid4(),
            identity,
            Decimal(value),
            "monetary",
            "EUR",
            "millions",
            period,
            "consolidated",
            (),
            "confirmed",
            ("synthetic:postgres",),
            (),
        )

    policy = TolerancePolicy(
        "F04-PG",
        "1",
        "monetary",
        "EUR",
        "millions",
        Decimal("0.1"),
        Decimal("1"),
        Decimal("5"),
        Decimal("0.01"),
        Decimal("0.05"),
        "synthetic-test-only",
    )
    return ReconciliationEngine().evaluate(
        FINANCIAL_RULES_V1.get("REC-BS-001"),
        (
            item("liabilities", "total_liabilities", "60"),
            item("equity", "equity", "40"),
            item("assets", "total_assets", assets),
        ),
        policy,
        timestamp=timestamp or datetime(2026, 7, 29, tzinfo=timezone.utc),
    )


@unittest.skipUnless(
    URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class F04PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = make_url(async_url(URL))
        if parsed.host not in {"localhost", "127.0.0.1"} or not any(
            marker in (parsed.database or "").lower()
            for marker in ("test", "f04", "disposable")
        ):
            raise RuntimeError("refusing a non-local or non-disposable database")
        cls.engine = create_async_engine(async_url(URL), poolclass=NullPool)
        cls.sessions = async_sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.engine.dispose())

    async def reset_to_0004(self):
        async with self.engine.begin() as connection:
            version = await connection.scalar(text("SHOW server_version_num"))
            if int(version) // 10000 != 16:
                raise RuntimeError(f"PostgreSQL 16 required, got {version}")
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=legacy_tables())
            )
        alembic("stamp", "20260727_0001")
        alembic("upgrade", "20260729_0004")

    async def schema(self, tables=None):
        def fingerprint(connection):
            inspector = inspect(connection)
            names = sorted(tables or inspector.get_table_names())

            def normalize_check(sqltext):
                value = sqltext.replace("::text", "")
                if "status = ANY" in value:
                    return "status IN ('pass','rounding_pass','warning','critical','blocking')"
                return value

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
                        normalize_check(item["sqltext"])
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

    def test_0004_0005_round_trip_exclusivity_invariance_and_orm_alignment(self):
        async def scenario():
            await self.reset_to_0004()
            before = await self.schema()
            protected = sorted(
                set(before) & (V2_TABLE_NAMES - {"reconciliation_results_v2"})
            )
            legacy = sorted(set(before) - V2_TABLE_NAMES - {"alembic_version"})
            protected_before = await self.schema(protected)
            legacy_before = await self.schema(legacy)

            alembic("upgrade", "20260729_0005")
            after = await self.schema()
            self.assertEqual(set(after) - set(before), {"reconciliation_results_v2"})
            self.assertEqual(await self.schema(protected), protected_before)
            self.assertEqual(await self.schema(legacy), legacy_before)

            table = ReconciliationResultModel.__table__
            expected = {
                "columns": [
                    (c.name, str(c.type).replace("DATETIME", "TIMESTAMP"), c.nullable)
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
            self.assertEqual(after[table.name], expected)

            alembic("downgrade", "20260729_0004")
            self.assertEqual(await self.schema(), before)
            alembic("upgrade", "20260729_0005")
            self.assertEqual(await self.schema(), after)

        asyncio.run(scenario())

    def test_validation_replay_concurrency_scope_transactions_and_cascades(self):
        async def scenario():
            await self.reset_to_0004()
            alembic("upgrade", "20260729_0005")
            user_id, workspace_a, workspace_b = uuid4(), uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f04@example.test", password_hash="x")
                )
                session.add_all(
                    [
                        Workspace(id=workspace_a, company_name="A"),
                        Workspace(id=workspace_b, company_name="B"),
                    ]
                )

            run_a = run(workspace_a, user_id, "a")
            run_a2 = run(workspace_a, user_id, "a2")
            run_a3 = run(workspace_a, user_id, "a3-rollback")
            run_b = run(workspace_b, user_id, "b")
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_a).create_run(run_a)
                await V2Repository(session, workspace_a).create_run(run_a2)
                await V2Repository(session, workspace_a).create_run(run_a3)
                await V2Repository(session, workspace_b).create_run(run_b)

            # Contract validation and scope validation both happen before a first write.
            with self.assertRaises((InputContractError, TypeError)):
                result(assets="NaN")
            async with self.sessions.begin() as session:
                with self.assertRaises(PermissionError):
                    await V2Repository(session, workspace_b).persist_reconciliation(
                        run_a.run_id, result()
                    )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(ReconciliationResultModel.id))
                    ),
                    0,
                )

            shared = result()

            async def concurrent_insert():
                async with self.sessions.begin() as session:
                    return await V2Repository(
                        session, workspace_a
                    ).persist_reconciliation(run_a.run_id, shared)

            first, second = await asyncio.gather(
                concurrent_insert(), concurrent_insert()
            )
            self.assertEqual(first[0].id, second[0].id)
            self.assertEqual(sorted((first[1], second[1])), [False, True])

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_a)
                replay, created = await repo.persist_reconciliation(
                    run_a.run_id, shared
                )
                self.assertFalse(created)
                self.assertEqual(replay.id, first[0].id)
                with self.assertRaises(IdempotencyConflict):
                    await repo.persist_reconciliation(
                        run_a.run_id, result(assets="101")
                    )
                # A semantic conflict must not poison the surrounding session.
                self.assertEqual(
                    await session.scalar(
                        select(func.count(ReconciliationResultModel.id))
                    ),
                    1,
                )
                with self.assertRaises(PermissionError):
                    await V2Repository(session, workspace_b).persist_reconciliation(
                        run_a.run_id, shared
                    )

            async with self.sessions.begin() as session:
                stored_a2, _ = await V2Repository(
                    session, workspace_a
                ).persist_reconciliation(run_a2.run_id, shared)
                stored_b, _ = await V2Repository(
                    session, workspace_b
                ).persist_reconciliation(run_b.run_id, shared)
                self.assertEqual(len({first[0].id, stored_a2.id, stored_b.id}), 3)

            rollback = result(assets="102")
            try:
                async with self.sessions.begin() as session:
                    await V2Repository(session, workspace_a).persist_reconciliation(
                        run_a3.run_id, rollback
                    )
                    raise RuntimeError("force rollback")
            except RuntimeError:
                pass
            async with self.sessions() as session:
                self.assertEqual(
                    await session.scalar(
                        select(func.count(ReconciliationResultModel.id)).where(
                            ReconciliationResultModel.run_id == run_a2.run_id
                        )
                    ),
                    1,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(ReconciliationResultModel.id)).where(
                            ReconciliationResultModel.run_id == run_a3.run_id
                        )
                    ),
                    0,
                )
                await session.execute(
                    text("DELETE FROM workspaces WHERE id=:id"), {"id": workspace_a}
                )
                await session.commit()
                self.assertIsNone(
                    await session.get(ReconciliationResultModel, first[0].id)
                )
                self.assertIsNotNone(
                    await session.get(ReconciliationResultModel, stored_b.id)
                )

        asyncio.run(scenario())
