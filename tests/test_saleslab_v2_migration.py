import ast
import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL", "postgresql://unused:unused@localhost/saleslab_f01_test"
)

import models  # noqa: F401,E402
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    SourceDocumentModel,
    StageResultModel,
)
from saleslab_v2.persistence.metadata import V2_TABLE_NAMES, legacy_tables

ROOT = Path(__file__).resolve().parents[1]


class MigrationContractTests(unittest.TestCase):
    def test_legacy_table_selection_uses_explicit_v2_ownership(self):
        legacy_names = {table.name for table in legacy_tables()}
        self.assertTrue(
            {
                "users",
                "workspaces",
                "workspace_members",
                "analyses",
                "usage_counters",
                "magic_link_tokens",
                "stripe_webhook_events",
            }.issubset(legacy_names)
        )
        self.assertTrue(V2_TABLE_NAMES.isdisjoint(legacy_names))

    def test_only_three_v2_tables_are_registered(self):
        self.assertEqual(
            {
                AnalysisRunModel.__tablename__,
                SourceDocumentModel.__tablename__,
                StageResultModel.__tablename__,
            },
            {"analysis_runs_v2", "source_documents_v2", "stage_results_v2"},
        )

    def test_constraints_indexes_and_workspace_boundaries(self):
        run = AnalysisRunModel.__table__
        self.assertIn(
            "uq_analysis_runs_v2_workspace_purpose_key",
            {item.name for item in run.constraints},
        )
        self.assertEqual(
            {item.name for item in run.indexes},
            {
                "ix_analysis_runs_v2_workspace_created",
                "ix_analysis_runs_v2_workspace_status",
            },
        )
        stage_fk = next(iter(StageResultModel.__table__.foreign_keys))
        self.assertEqual(stage_fk.ondelete, "CASCADE")
        self.assertFalse(
            next(
                i
                for i in SourceDocumentModel.__table__.indexes
                if i.name.endswith("workspace_hash")
            ).unique
        )

    def test_revision_is_additive_and_baseline_unchanged(self):
        revision_0002 = (
            ROOT / "alembic/versions/20260727_0002_saleslab_v2_foundation.py"
        )
        source = revision_0002.read_text()
        tree = ast.parse(source)
        self.assertIn('down_revision = "20260727_0001"', source)
        self.assertEqual(source.count("op.create_table("), 3)
        self.assertNotIn("op.alter_column(", source)
        self.assertNotIn("op.execute(", source)
        self.assertIsInstance(tree, ast.Module)
        self.assertEqual(
            hashlib.sha256(revision_0002.read_bytes()).hexdigest(),
            "7cec35f1e4c8f6d635dce6b5345045d785840c07380d90c83469c2ece89421d1",
        )
        revision_0003 = (
            ROOT / "alembic/versions/20260727_0003_analysis_run_request_fingerprint.py"
        ).read_text()
        self.assertIn('down_revision = "20260727_0002"', revision_0003)
        self.assertIn('sa.Column("request_fingerprint"', revision_0003)
        self.assertIn("nullable=False", revision_0003)
        self.assertIn(
            'op.drop_column("analysis_runs_v2", "request_fingerprint")', revision_0003
        )

    def test_offline_sql_creates_only_v2_tables(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "alembic.ini",
                "upgrade",
                "head",
                "--sql",
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "DATABASE_URL": "postgresql://unused:unused@localhost/saleslab_f01_test",
            },
            text=True,
            capture_output=True,
            check=True,
        )
        sql = result.stdout.lower()
        for table in ("analysis_runs_v2", "source_documents_v2", "stage_results_v2"):
            self.assertIn(f"create table {table}", sql)
        for table in ("users", "workspaces", "analyses"):
            self.assertNotIn(f"create table {table}", sql)
        self.assertIn("add column request_fingerprint varchar(64)", sql)


if __name__ == "__main__":
    unittest.main()
