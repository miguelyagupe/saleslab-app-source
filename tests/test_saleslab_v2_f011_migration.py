"""PostgreSQL backfill coverage for the immutable 0002 -> F-01.1 migration."""

# ruff: noqa: E402

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import os
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL", "")
if os.environ.get("SALESLAB_REQUIRE_POSTGRES_TESTS") == "1" and not URL:
    raise RuntimeError("TEST_DATABASE_URL is required for F-01.1 PostgreSQL tests")
os.environ.setdefault("DATABASE_URL", URL or "postgresql://unused/unused_test")

from database import Base
import models  # noqa: F401,E402
from models import User, Workspace
from saleslab_v2.domain.enums import LanguageCode, ModuleType, SourceFormat, TrustLevel
from saleslab_v2.domain.idempotency import IdempotencyConflict
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.persistence.metadata import V2_TABLE_NAMES, legacy_tables
from saleslab_v2.persistence.repositories import V2Repository


def async_url(value):
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def alembic(*args, check=True):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": URL},
        check=check,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(
    URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class F011BackfillPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = make_url(async_url(URL))
        if parsed.host not in {"localhost", "127.0.0.1", "postgres"} or not any(
            marker in (parsed.database or "").lower()
            for marker in ("test", "f01", "disposable")
        ):
            raise RuntimeError("refusing a non-local or non-disposable database")
        cls.engine = create_async_engine(async_url(URL), poolclass=NullPool)
        cls.sessions = async_sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.engine.dispose())

    async def reset_to_0002(self):
        async with self.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync, tables=legacy_tables(Base.metadata)
                )
            )
        alembic("stamp", "20260727_0001")
        alembic("upgrade", "20260727_0002")

    async def fingerprint(self, *, legacy_only=False):
        def inspect_schema(connection):
            inspector = inspect(connection)
            names = sorted(inspector.get_table_names())
            if legacy_only:
                names = [
                    name
                    for name in names
                    if name != "alembic_version" and name not in V2_TABLE_NAMES
                ]
            return {
                name: [
                    (column["name"], str(column["type"]), column["nullable"])
                    for column in inspector.get_columns(name)
                ]
                for name in names
            }

        async with self.engine.connect() as connection:
            return await connection.run_sync(inspect_schema)

    def test_existing_run_backfill_failure_rollback_and_reupgrade(self):
        async def scenario():
            await self.reset_to_0002()
            user_id, workspace_id, source_id, run_id = (
                uuid4(),
                uuid4(),
                uuid4(),
                uuid4(),
            )
            context = LanguageContext(
                (LanguageCode.PT, LanguageCode.EN),
                LanguageCode.PT,
                LanguageCode.EN,
                LocaleTag.PT_PT,
                "IFRS",
            )
            old_run = AnalysisRun(
                workspace_id,
                user_id,
                ModuleType.FINANCIAL,
                "schema-1",
                "pipeline-1",
                context,
                (source_id,),
                ("a" * 64,),
                "analysis",
                "migrated-key",
                run_id=run_id,
            )
            source = SourceDocument(
                workspace_id,
                "old.csv",
                SourceFormat.CSV,
                "b" * 64,
                "parser-1",
                TrustLevel.DECLARED,
                (LanguageCode.PT,),
                source_id=source_id,
            )
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="backfill@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="Backfill"))
                await session.flush()
                await session.execute(
                    text(
                        """INSERT INTO source_documents_v2
                        (id, workspace_id, filename, source_format, content_hash,
                         parser_version, trust_level, language_metadata, uploaded_at,
                         created_at, supersedes_source_id)
                        VALUES (:id, :workspace, :filename, :format, :hash, :parser,
                                :trust, CAST(:languages AS jsonb), :uploaded, :created, NULL)"""
                    ),
                    {
                        "id": source.source_id,
                        "workspace": workspace_id,
                        "filename": source.filename,
                        "format": source.source_format.value,
                        "hash": source.content_hash,
                        "parser": source.parser_version,
                        "trust": source.trust_level.value,
                        "languages": '["pt"]',
                        "uploaded": source.uploaded_at,
                        "created": source.created_at,
                    },
                )
                await session.execute(
                    text("""INSERT INTO analysis_runs_v2
                        (id, workspace_id, created_by_user_id, module, workflow_status,
                         schema_version, pipeline_version, language_context, source_ids,
                         input_hashes, idempotency_purpose, idempotency_key, created_at)
                        VALUES (:id, :workspace, :user_id, :module, 'received', :schema,
                                :pipeline, CAST(:language AS jsonb), CAST(:sources AS jsonb),
                                CAST(:hashes AS jsonb), :purpose, :key, :created)"""),
                    {
                        "id": run_id,
                        "workspace": workspace_id,
                        "user_id": user_id,
                        "module": old_run.module.value,
                        "schema": old_run.schema_version,
                        "pipeline": old_run.pipeline_version,
                        "language": '{"source_languages":["pt","en"],"analysis_language":"pt","report_language":"en","format_locale":"pt-PT","accounting_context":"IFRS"}',
                        "sources": f'["{source_id}"]',
                        "hashes": f'["{"a" * 64}"]',
                        "purpose": old_run.idempotency_purpose,
                        "key": old_run.idempotency_key,
                        "created": datetime.now(timezone.utc),
                    },
                )
            legacy_before = await self.fingerprint(legacy_only=True)
            schema_0002 = await self.fingerprint()
            self.assertNotIn(
                "request_fingerprint",
                {column[0] for column in schema_0002["analysis_runs_v2"]},
            )

            alembic("upgrade", "20260727_0003")
            async with self.engine.connect() as connection:
                stored = await connection.scalar(
                    text(
                        "SELECT request_fingerprint FROM analysis_runs_v2 WHERE id=:id"
                    ),
                    {"id": run_id},
                )
                nullable = await connection.run_sync(
                    lambda sync: next(
                        column["nullable"]
                        for column in inspect(sync).get_columns("analysis_runs_v2")
                        if column["name"] == "request_fingerprint"
                    )
                )
            self.assertEqual(stored, old_run.request_fingerprint)
            self.assertFalse(nullable)

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                equivalent, created = await repo.get_or_create_run(
                    replace(
                        old_run,
                        run_id=uuid4(),
                        language_context=replace(
                            context, format_locale=LocaleTag.EN_GB
                        ),
                    )
                )
                self.assertEqual(equivalent.id, run_id)
                self.assertFalse(created)
                with self.assertRaises(IdempotencyConflict):
                    await repo.get_or_create_run(
                        replace(old_run, run_id=uuid4(), pipeline_version="pipeline-2")
                    )
                new_run, new_created = await repo.get_or_create_run(
                    replace(old_run, run_id=uuid4(), idempotency_key="new-key")
                )
                self.assertTrue(new_created)
                self.assertEqual(
                    new_run.request_fingerprint, old_run.request_fingerprint
                )

            alembic("downgrade", "20260727_0002")
            schema_after_downgrade = await self.fingerprint()
            self.assertEqual(schema_after_downgrade, schema_0002)
            self.assertEqual(await self.fingerprint(legacy_only=True), legacy_before)

            async with self.sessions.begin() as session:
                await session.execute(
                    text(
                        "UPDATE analysis_runs_v2 SET language_context = "
                        "jsonb_set(language_context, '{analysis_language}', '123') "
                        "WHERE id=:id"
                    ),
                    {"id": run_id},
                )
            failed = alembic("upgrade", "20260727_0003", check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("cannot safely backfill request fingerprint", failed.stderr)
            self.assertEqual(await self.fingerprint(), schema_after_downgrade)

            async with self.sessions.begin() as session:
                await session.execute(
                    text(
                        "UPDATE analysis_runs_v2 SET language_context = "
                        "jsonb_set(language_context, '{analysis_language}', '\"pt\"') "
                        "WHERE id=:id"
                    ),
                    {"id": run_id},
                )
            alembic("upgrade", "20260727_0003")
            self.assertEqual(await self.fingerprint(legacy_only=True), legacy_before)

        asyncio.run(scenario())
