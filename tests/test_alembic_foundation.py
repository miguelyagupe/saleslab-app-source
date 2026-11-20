import asyncio
import os
from pathlib import Path
import subprocess
import sys
import unittest

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "20260727_0001"
CURRENT_REVISION = "20260727_0006"


def async_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def require_disposable_database(value: str) -> None:
    parsed = make_url(async_url(value))
    database = parsed.database or ""
    if parsed.host not in {"localhost", "127.0.0.1", "postgres"}:
        raise RuntimeError("F-00 tests refuse non-local PostgreSQL hosts")
    if not any(marker in database.lower() for marker in ("test", "f00", "disposable")):
        raise RuntimeError(
            "F-00 tests require a database name containing test, f00, or disposable"
        )


def run_alembic(*arguments: str, database_url: str, check: bool = True):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


class AlembicConfigurationTests(unittest.TestCase):
    def test_f01_is_the_only_head_after_baseline(self):
        result = run_alembic(
            "heads",
            database_url="postgresql://unused:unused@localhost/saleslab_f00_test",
        )
        self.assertIn(f"{CURRENT_REVISION} (head)", result.stdout)

    def test_offline_baseline_contains_no_legacy_ddl(self):
        result = run_alembic(
            "upgrade",
            "head",
            "--sql",
            database_url="postgresql://unused:unused@localhost/saleslab_f00_test",
        )
        sql = result.stdout.lower()
        for table in (
            "users",
            "workspaces",
            "workspace_members",
            "analyses",
            "usage_counters",
            "magic_link_tokens",
            "stripe_webhook_events",
        ):
            self.assertNotIn(f"create table {table}", sql)
            self.assertNotIn(f"drop table {table}", sql)
            self.assertNotIn(f"alter table {table}", sql)


@unittest.skipUnless(
    os.environ.get("TEST_DATABASE_URL"),
    "TEST_DATABASE_URL is required for disposable PostgreSQL integration tests",
)
class AlembicPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ["TEST_DATABASE_URL"]
        require_disposable_database(cls.database_url)
        os.environ["DATABASE_URL"] = cls.database_url

        from database import Base
        import models  # noqa: F401
        from saleslab_v2.persistence.metadata import legacy_tables

        cls.Base = Base
        cls.legacy_tables = legacy_tables
        asyncio.run(cls._reset_and_create_legacy_schema())

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls._drop_test_schema())

    @classmethod
    async def _reset_and_create_legacy_schema(cls):
        engine = create_async_engine(async_url(cls.database_url))
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DROP SCHEMA public CASCADE"))
                await connection.execute(text("CREATE SCHEMA public"))
                legacy_tables = cls.legacy_tables(cls.Base.metadata)
                await connection.run_sync(
                    lambda sync_connection: cls.Base.metadata.create_all(
                        sync_connection, tables=legacy_tables
                    )
                )
        finally:
            await engine.dispose()

    @classmethod
    async def _drop_test_schema(cls):
        engine = create_async_engine(async_url(cls.database_url))
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DROP SCHEMA public CASCADE"))
                await connection.execute(text("CREATE SCHEMA public"))
        finally:
            await engine.dispose()

    async def _fingerprint(self):
        engine = create_async_engine(async_url(self.database_url))
        try:
            async with engine.connect() as connection:

                def inspect_schema(sync_connection):
                    inspector = inspect(sync_connection)
                    result = {}
                    for table in sorted(inspector.get_table_names()):
                        if table == "alembic_version":
                            continue
                        result[table] = {
                            "columns": sorted(
                                column["name"]
                                for column in inspector.get_columns(table)
                            ),
                            "primary_key": sorted(
                                inspector.get_pk_constraint(table).get(
                                    "constrained_columns"
                                )
                                or []
                            ),
                            "foreign_keys": sorted(
                                (
                                    tuple(sorted(fk.get("constrained_columns") or [])),
                                    fk.get("referred_table"),
                                    tuple(sorted(fk.get("referred_columns") or [])),
                                )
                                for fk in inspector.get_foreign_keys(table)
                            ),
                        }
                    return result

                return await connection.run_sync(inspect_schema)
        finally:
            await engine.dispose()

    async def _alembic_versions(self):
        engine = create_async_engine(async_url(self.database_url))
        try:
            async with engine.connect() as connection:
                return list(
                    (
                        await connection.execute(
                            text(
                                "SELECT version_num FROM alembic_version ORDER BY version_num"
                            )
                        )
                    ).scalars()
                )
        finally:
            await engine.dispose()

    def test_stamp_adopts_legacy_schema_without_changing_it(self):
        before = asyncio.run(self._fingerprint())
        run_alembic("stamp", BASELINE_REVISION, database_url=self.database_url)
        after = asyncio.run(self._fingerprint())

        self.assertEqual(before, after)
        self.assertEqual(asyncio.run(self._alembic_versions()), [BASELINE_REVISION])

        inspection = subprocess.run(
            [sys.executable, "scripts/inspect_database_schema.py"],
            cwd=ROOT,
            env={**os.environ, "DATABASE_URL": self.database_url},
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn('"mode": "read_only"', inspection.stdout)
        self.assertIn('"version_table_present": true', inspection.stdout)

    def test_upgrade_and_downgrade_baseline_leave_legacy_schema_unchanged(self):
        run_alembic("stamp", "base", database_url=self.database_url)
        before = asyncio.run(self._fingerprint())
        run_alembic("upgrade", "head", database_url=self.database_url)
        run_alembic("downgrade", "base", database_url=self.database_url)
        after = asyncio.run(self._fingerprint())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
