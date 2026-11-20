"""PostgreSQL 16 integration coverage for F-01 persistence.

Direct execution fails without TEST_DATABASE_URL. Discovery skips the class so
legacy-only environments can still run their suite; the required F-01 command
must set SALESLAB_REQUIRE_POSTGRES_TESTS=1 and TEST_DATABASE_URL.
"""

# ruff: noqa: E402

import asyncio
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL", "")
if os.environ.get("SALESLAB_REQUIRE_POSTGRES_TESTS") == "1" and not URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is required for the F-01 PostgreSQL verification"
    )
os.environ.setdefault(
    "DATABASE_URL", URL or "postgresql://unused@localhost/saleslab_f01_test"
)

from database import Base
import models  # noqa: F401,E402
from models import User, Workspace
from saleslab_v2.domain.enums import (
    LanguageCode,
    ModuleType,
    SourceFormat,
    StageStatus,
    TrustLevel,
    WorkflowStatus,
)
from saleslab_v2.domain.idempotency import IdempotencyConflict
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun, Failure, StageResult
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    SourceDocumentModel,
    StageResultModel,
)
from saleslab_v2.persistence.metadata import V2_TABLE_NAMES, legacy_tables
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
class V2PostgresPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = make_url(async_url(URL))
        if parsed.host not in {"localhost", "127.0.0.1", "postgres"} or not any(
            x in (parsed.database or "").lower() for x in ("test", "f01", "disposable")
        ):
            raise RuntimeError(
                "refusing a non-local or non-disposable PostgreSQL database"
            )
        cls.engine = create_async_engine(async_url(URL), poolclass=NullPool)
        cls.sessions = async_sessionmaker(cls.engine, expire_on_commit=False)
        asyncio.run(cls._setup())

    @classmethod
    async def _setup(cls):
        async with cls.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            legacy = legacy_tables(Base.metadata)
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=legacy)
            )
        cls.legacy_before = await cls._legacy_fingerprint()
        alembic("stamp", "20260727_0001")
        alembic("upgrade", "20260727_0003")
        cls.user_id, cls.workspace_a, cls.workspace_b = uuid4(), uuid4(), uuid4()
        async with cls.sessions.begin() as session:
            session.add(
                User(id=cls.user_id, email="f01@example.test", password_hash="x")
            )
            session.add_all(
                [
                    Workspace(id=cls.workspace_a, company_name="A"),
                    Workspace(id=cls.workspace_b, company_name="B"),
                ]
            )

    @classmethod
    async def _legacy_fingerprint(cls):
        def fingerprint(connection):
            inspector = inspect(connection)
            return {
                name: [
                    (c["name"], str(c["type"]), c["nullable"])
                    for c in inspector.get_columns(name)
                ]
                for name in sorted(inspector.get_table_names())
                if name != "alembic_version" and name not in V2_TABLE_NAMES
            }

        async with cls.engine.connect() as connection:
            return await connection.run_sync(fingerprint)

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls._teardown())

    @classmethod
    async def _teardown(cls):
        alembic("downgrade", "20260727_0001")
        async with cls.engine.connect() as connection:
            names = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
        assert not names.intersection(
            {"analysis_runs_v2", "stage_results_v2", "source_documents_v2"}
        )
        assert cls.legacy_before == await cls._legacy_fingerprint()
        alembic("upgrade", "20260727_0003")
        await cls.engine.dispose()

    def source(self, workspace, **changes):
        source = SourceDocument(
            workspace,
            "source.csv",
            SourceFormat.CSV,
            "a" * 64,
            "1",
            TrustLevel.DECLARED,
            (LanguageCode.PT,),
        )
        return replace(source, **changes)

    def run_value(self, workspace, source_id, **changes):
        run = AnalysisRun(
            workspace,
            self.user_id,
            ModuleType.FINANCIAL,
            "1",
            "1",
            LanguageContext(
                (LanguageCode.PT,), LanguageCode.PT, LanguageCode.PT, LocaleTag.PT_PT
            ),
            (source_id,),
            ("b" * 64,),
            "analysis",
            "key",
        )
        return replace(run, **changes)

    def test_sources_runs_failure_isolation_supersession_and_rollback(self):
        async def scenario():
            async with self.sessions.begin() as session:
                repo_a, repo_b = V2Repository(session, self.workspace_a), V2Repository(
                    session, self.workspace_b
                )
                first = self.source(self.workspace_a)
                await repo_a.create_source(first)
                self.assertIsNotNone(await repo_a.get_source(first.source_id))
                self.assertIsNone(await repo_b.get_source(first.source_id))
                second = self.source(
                    self.workspace_a, supersedes_source_id=first.source_id
                )
                await repo_a.create_source(second)
                with self.assertRaises(PermissionError):
                    await repo_b.create_source(
                        self.source(
                            self.workspace_b, supersedes_source_id=first.source_id
                        )
                    )
                with self.assertRaises(PermissionError):
                    await repo_a.create_source(
                        self.source(self.workspace_a, supersedes_source_id=uuid4())
                    )
                with self.assertRaises(ValueError):
                    await repo_a.create_source(
                        replace(first, supersedes_source_id=first.source_id)
                    )
                with self.assertRaises(ValueError):
                    self.run_value(
                        self.workspace_a,
                        first.source_id,
                        source_ids=(first.source_id, first.source_id),
                    )
                with self.assertRaises(PermissionError):
                    await repo_b.create_run(
                        self.run_value(self.workspace_b, first.source_id)
                    )
                with self.assertRaises(PermissionError):
                    await repo_a.create_run(self.run_value(self.workspace_a, uuid4()))
                run = self.run_value(self.workspace_a, first.source_id)
                stored = await repo_a.create_run(run)
                self.assertEqual((await repo_a.get_run(stored.id)).id, stored.id)
            failed = self.run_value(
                self.workspace_a, first.source_id, idempotency_key="failed"
            ).transition(WorkflowStatus.FAILED, Failure("E_PARSE", "bad", {"line": 2}))
            async with self.sessions.begin() as session:
                stored = await V2Repository(session, self.workspace_a).create_run(
                    failed
                )
                failed_id = stored.id
            async with self.sessions() as session:
                reloaded = await V2Repository(session, self.workspace_a).get_run(
                    failed_id
                )
                self.assertEqual(
                    reloaded.failure,
                    {"code": "E_PARSE", "message": "bad", "details": {"line": 2}},
                )
            transient_id = uuid4()
            try:
                async with self.sessions.begin() as session:
                    session.add(
                        AnalysisRunModel(
                            **{
                                **V2Repository(session, self.workspace_a)._run_values(
                                    self.run_value(
                                        self.workspace_a,
                                        first.source_id,
                                        run_id=transient_id,
                                        idempotency_key="rollback",
                                    )
                                )
                            }
                        )
                    )
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass
            async with self.sessions() as session:
                self.assertIsNone(await session.get(AnalysisRunModel, transient_id))

        asyncio.run(scenario())

    def test_migration_matches_models(self):
        async def scenario():
            def compare(connection):
                inspector = inspect(connection)
                for model in (
                    AnalysisRunModel.__table__,
                    SourceDocumentModel.__table__,
                    StageResultModel.__table__,
                ):
                    columns = {
                        item["name"]: item for item in inspector.get_columns(model.name)
                    }
                    self.assertEqual(set(columns), set(model.columns.keys()))
                    for expected in model.columns:
                        self.assertEqual(
                            columns[expected.name]["nullable"], expected.nullable
                        )
                        self.assertEqual(
                            columns[expected.name]["type"]._type_affinity,
                            expected.type._type_affinity,
                        )
                    observed_fks = {
                        (
                            tuple(item["constrained_columns"]),
                            item["referred_table"],
                            (item.get("options") or {}).get("ondelete"),
                        )
                        for item in inspector.get_foreign_keys(model.name)
                    }
                    expected_fks = {
                        (
                            tuple(fk.parent.name for fk in constraint.elements),
                            next(iter(constraint.elements)).column.table.name,
                            constraint.ondelete,
                        )
                        for constraint in model.foreign_key_constraints
                    }
                    self.assertEqual(observed_fks, expected_fks)
                    observed_pk = inspector.get_pk_constraint(model.name)
                    self.assertEqual(observed_pk["name"], model.primary_key.name)
                    self.assertEqual(
                        tuple(observed_pk["constrained_columns"]),
                        tuple(column.name for column in model.primary_key.columns),
                    )
                    observed_uniques = {
                        (item["name"], tuple(item["column_names"]))
                        for item in inspector.get_unique_constraints(model.name)
                    }
                    expected_uniques = {
                        (item.name, tuple(column.name for column in item.columns))
                        for item in model.constraints
                        if item.__class__.__name__ == "UniqueConstraint"
                    }
                    self.assertEqual(observed_uniques, expected_uniques)
                    observed_indexes = {
                        (item["name"], tuple(item["column_names"]), item["unique"])
                        for item in inspector.get_indexes(model.name)
                        if not item.get("duplicates_constraint")
                    }
                    expected_indexes = {
                        (
                            item.name,
                            tuple(column.name for column in item.columns),
                            item.unique,
                        )
                        for item in model.indexes
                    }
                    self.assertEqual(observed_indexes, expected_indexes)
                source_indexes = inspector.get_indexes("source_documents_v2")
                self.assertFalse(
                    next(
                        item
                        for item in source_indexes
                        if item["name"] == "ix_source_documents_v2_workspace_hash"
                    )["unique"]
                )

            async with self.engine.connect() as connection:
                await connection.run_sync(compare)

        asyncio.run(scenario())

    def test_atomic_idempotency_stage_replay_and_cascade(self):
        async def scenario():
            async with self.sessions.begin() as session:
                source = self.source(self.workspace_a, content_hash="c" * 64)
                await V2Repository(session, self.workspace_a).create_source(source)
                source_b = self.source(self.workspace_b, content_hash="c" * 64)
                await V2Repository(session, self.workspace_b).create_source(source_b)
            run1, run2 = self.run_value(
                self.workspace_a, source.source_id, idempotency_key="concurrent"
            ), self.run_value(
                self.workspace_a, source.source_id, idempotency_key="concurrent"
            )

            async def worker(run):
                async with self.sessions.begin() as session:
                    model, created = await V2Repository(
                        session, self.workspace_a
                    ).get_or_create_run(run)
                    return model.id, created

            results = await asyncio.gather(worker(run1), worker(run2))
            self.assertEqual(results[0][0], results[1][0])
            self.assertEqual(sum(created for _, created in results), 1)
            async with self.sessions.begin() as session:
                other, other_created = await V2Repository(
                    session, self.workspace_b
                ).get_or_create_run(
                    self.run_value(
                        self.workspace_b,
                        source_b.source_id,
                        idempotency_key="concurrent",
                    )
                )
                self.assertTrue(other_created)
                self.assertNotEqual(other.id, results[0][0])
            async with self.sessions.begin() as session:
                repo = V2Repository(session, self.workspace_a)
                source_two = self.source(self.workspace_a, content_hash="9" * 64)
                await repo.create_source(source_two)
                conflicts = (
                    replace(run1, module=ModuleType.SALES),
                    replace(run1, schema_version="2"),
                    replace(run1, pipeline_version="2"),
                    replace(
                        run1,
                        language_context=replace(
                            run1.language_context,
                            analysis_language=LanguageCode.EN,
                        ),
                    ),
                    replace(
                        run1,
                        language_context=replace(
                            run1.language_context, report_language=LanguageCode.EN
                        ),
                    ),
                    replace(
                        run1,
                        language_context=replace(
                            run1.language_context,
                            source_languages=(LanguageCode.EN,),
                        ),
                    ),
                    replace(
                        run1,
                        language_context=replace(
                            run1.language_context, accounting_context="IFRS"
                        ),
                    ),
                    replace(run1, source_ids=(source_two.source_id,)),
                    replace(run1, input_hashes=("d" * 64,)),
                )
                for conflicting_run in conflicts:
                    with self.subTest(field=conflicting_run.idempotency_payload()):
                        with self.assertRaises(IdempotencyConflict):
                            await repo.get_or_create_run(conflicting_run)
                        # ON CONFLICT avoids a failed transaction; prove the same
                        # session remains usable after every semantic conflict.
                        self.assertIsNotNone(await repo.get_run(results[0][0]))
                locale_only = replace(
                    run1,
                    language_context=replace(
                        run1.language_context, format_locale=LocaleTag.EN_GB
                    ),
                )
                equivalent, created = await repo.get_or_create_run(locale_only)
                self.assertEqual(equivalent.id, results[0][0])
                self.assertFalse(created)
                run_count = await session.scalar(
                    select(text("count(*)"))
                    .select_from(AnalysisRunModel)
                    .where(
                        AnalysisRunModel.workspace_id == self.workspace_a,
                        AnalysisRunModel.idempotency_purpose == "analysis",
                        AnalysisRunModel.idempotency_key == "concurrent",
                    )
                )
                self.assertEqual(run_count, 1)
                now = datetime.now(timezone.utc)
                stage = StageResult(
                    results[0][0],
                    "parse",
                    "1",
                    StageStatus.COMPLETED,
                    "e" * 64,
                    "f" * 64,
                    "1",
                    {"ok": True},
                    completed_at=now,
                )
                self.assertIsNone(await repo.persist_stage(stage))
                enabled = V2Repository(session, self.workspace_a, persist_stages=True)
                stored = await enabled.persist_stage(stage)
                self.assertEqual((await enabled.persist_stage(stage)).id, stored.id)
                with self.assertRaises(IdempotencyConflict):
                    await enabled.persist_stage(replace(stage, payload={"ok": False}))
                await session.execute(
                    delete(AnalysisRunModel).where(AnalysisRunModel.id == results[0][0])
                )
            async with self.sessions() as session:
                self.assertIsNone(
                    await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == results[0][0]
                        )
                    )
                )

        asyncio.run(scenario())

    def test_source_supersession_rejects_cross_workspace_predecessor(self):
        async def scenario():
            async with self.sessions.begin() as session:
                previous = self.source(self.workspace_a, content_hash="7" * 64)
                await V2Repository(session, self.workspace_a).create_source(previous)
                candidate = self.source(
                    self.workspace_b,
                    content_hash="8" * 64,
                    supersedes_source_id=previous.source_id,
                )
                with self.assertRaises(PermissionError):
                    await V2Repository(session, self.workspace_b).create_source(
                        candidate
                    )
                self.assertIsNotNone(
                    await V2Repository(session, self.workspace_a).get_source(
                        previous.source_id
                    )
                )
                self.assertIsNone(
                    await V2Repository(session, self.workspace_b).get_source(
                        candidate.source_id
                    )
                )

        asyncio.run(scenario())
