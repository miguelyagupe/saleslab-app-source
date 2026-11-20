import asyncio
import json
import os
from pathlib import Path
import unittest

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from scripts.inspect_database_schema import inspect_database
from tests.test_alembic_foundation import async_url, require_disposable_database

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
QUOTA = "20260724_fix_usage_counters_quota.sql"
MAGIC = "20260724_secure_magic_links.sql"
STRIPE = "20260724_sync_stripe_subscriptions.sql"

PRE_MIGRATION_SCHEMA = """
DROP TABLE IF EXISTS alembic_version CASCADE;
DROP TABLE IF EXISTS magic_link_tokens CASCADE;
DROP TABLE IF EXISTS stripe_webhook_events CASCADE;
DROP TABLE IF EXISTS usage_counters CASCADE;
DROP TABLE IF EXISTS workspaces CASCADE;
DROP TABLE IF EXISTS users CASCADE;
CREATE TABLE users (id uuid PRIMARY KEY);
CREATE TABLE workspaces (
  id uuid PRIMARY KEY,
  stripe_customer_id varchar(100),
  stripe_subscription_id varchar(100)
);
CREATE TABLE usage_counters (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  month varchar(7) NOT NULL,
  count integer NOT NULL DEFAULT 0
);
"""


@unittest.skipUnless(
    os.environ.get("TEST_DATABASE_URL"),
    "TEST_DATABASE_URL is required for disposable PostgreSQL integration tests",
)
class ManualMigrationInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ["TEST_DATABASE_URL"]
        require_disposable_database(cls.database_url)

    def setUp(self):
        asyncio.run(self.execute(PRE_MIGRATION_SCHEMA))

    async def execute(self, sql: str) -> None:
        engine = create_async_engine(
            async_url(self.database_url), isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as connection:
                for statement in sql.split(";"):
                    if statement.strip():
                        await connection.exec_driver_sql(statement)
        finally:
            await engine.dispose()

    def apply(self, filename: str) -> None:
        asyncio.run(self.execute((MIGRATIONS / filename).read_text(encoding="utf-8")))

    def report(self):
        return asyncio.run(inspect_database(self.database_url))

    def migration(self, filename: str):
        return self.report()["manual_migration_signals"][filename]

    async def fingerprint(self):
        engine = create_async_engine(async_url(self.database_url))
        try:
            async with engine.connect() as connection:

                def collect(sync_connection):
                    inspector = inspect(sync_connection)
                    return {
                        table: {
                            "columns": inspector.get_columns(table),
                            "constraints": inspector.get_pk_constraint(table),
                            "unique": inspector.get_unique_constraints(table),
                            "foreign_keys": inspector.get_foreign_keys(table),
                            "indexes": inspector.get_indexes(table),
                        }
                        for table in sorted(inspector.get_table_names())
                    }

                raw = await connection.run_sync(collect)
                return json.loads(json.dumps(raw, default=str))
        finally:
            await engine.dispose()

    def assert_reason(self, migration, object_id, reason_code):
        check = next(
            item for item in migration["checks"] if item["object_id"] == object_id
        )
        self.assertEqual(check["reason_code"], reason_code)

    def test_no_manual_script_applied(self):
        report = self.report()
        statuses = {
            name: value["status"]
            for name, value in report["manual_migration_signals"].items()
        }
        self.assertEqual(
            statuses,
            {QUOTA: "not_applied", MAGIC: "not_applied", STRIPE: "not_applied"},
        )
        self.assertFalse(report["alembic"]["version_table_present"])

    def test_each_complete_script_is_complete(self):
        for filename in (QUOTA, MAGIC, STRIPE):
            with self.subTest(filename=filename):
                asyncio.run(self.execute(PRE_MIGRATION_SCHEMA))
                self.apply(filename)
                migration = self.migration(filename)
                self.assertEqual(migration["status"], "complete")
                self.assertEqual(migration["summary"]["missing"], 0)
                self.assertEqual(migration["summary"]["divergent"], 0)

    def test_table_created_but_index_missing_is_partial(self):
        self.apply(MAGIC)
        asyncio.run(self.execute("DROP INDEX ix_magic_link_tokens_expires_at"))
        migration = self.migration(MAGIC)
        self.assertEqual(migration["status"], "partial")
        self.assert_reason(
            migration, "ix_magic_link_tokens_expires_at", "INDEX_MISSING"
        )

    def test_column_created_but_unique_constraint_missing_is_partial(self):
        self.apply(MAGIC)
        asyncio.run(
            self.execute(
                "ALTER TABLE magic_link_tokens DROP CONSTRAINT magic_link_tokens_token_hash_key"
            )
        )
        migration = self.migration(MAGIC)
        self.assertEqual(migration["status"], "partial")
        self.assert_reason(
            migration, "magic_link_tokens.token_hash.unique", "CONSTRAINT_MISSING"
        )

    def test_index_with_wrong_columns_is_inconsistent(self):
        self.apply(STRIPE)
        asyncio.run(self.execute("""
            DROP INDEX ix_workspaces_stripe_customer_id;
            CREATE INDEX ix_workspaces_stripe_customer_id
              ON workspaces (stripe_subscription_id)
              WHERE stripe_customer_id IS NOT NULL
        """))
        migration = self.migration(STRIPE)
        self.assertEqual(migration["status"], "inconsistent")
        self.assert_reason(
            migration, "ix_workspaces_stripe_customer_id", "INDEX_COLUMNS_DIVERGENT"
        )

    def test_partial_index_with_wrong_predicate_is_inconsistent(self):
        self.apply(STRIPE)
        asyncio.run(self.execute("""
            DROP INDEX ix_workspaces_stripe_subscription_id;
            CREATE INDEX ix_workspaces_stripe_subscription_id
              ON workspaces (stripe_subscription_id)
              WHERE stripe_customer_id IS NOT NULL
        """))
        migration = self.migration(STRIPE)
        self.assertEqual(migration["status"], "inconsistent")
        self.assert_reason(
            migration,
            "ix_workspaces_stripe_subscription_id",
            "INDEX_PREDICATE_DIVERGENT",
        )

    def test_partial_application_of_each_migration(self):
        partial_sql = {
            QUOTA: "ALTER TABLE usage_counters ALTER COLUMN month TYPE varchar(16)",
            MAGIC: """
                CREATE TABLE magic_link_tokens (
                  id uuid PRIMARY KEY,
                  token_hash varchar(64) NOT NULL UNIQUE,
                  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                  role varchar(20) NOT NULL,
                  expires_at timestamptz NOT NULL,
                  used_at timestamptz,
                  created_at timestamptz NOT NULL DEFAULT now()
                )
            """,
            STRIPE: "ALTER TABLE workspaces ADD COLUMN stripe_subscription_status varchar(50)",
        }
        for filename, sql in partial_sql.items():
            with self.subTest(filename=filename):
                asyncio.run(self.execute(PRE_MIGRATION_SCHEMA))
                asyncio.run(self.execute(sql))
                migration = self.migration(filename)
                self.assertEqual(migration["status"], "partial")
                self.assertGreater(migration["summary"]["missing"], 0)

    def test_alembic_state_absent_and_present(self):
        self.assertFalse(self.report()["alembic"]["version_table_present"])
        asyncio.run(self.execute("""
            CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('20260727_0001')
        """))
        state = self.report()["alembic"]
        self.assertTrue(state["version_table_present"])
        self.assertEqual(state["versions"], ["20260727_0001"])

    def test_inspector_does_not_change_schema(self):
        self.apply(QUOTA)
        before = asyncio.run(self.fingerprint())
        self.report()
        after = asyncio.run(self.fingerprint())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
