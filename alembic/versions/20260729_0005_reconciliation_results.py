"""Add F-04 deterministic reconciliation results."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260729_0005"
down_revision = "20260729_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reconciliation_results_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_results_v2"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_reconciliation_results_v2_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_reconciliation_results_v2_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "run_id",
            "rule_id",
            "rule_version",
            name="uq_reconciliation_results_v2_execution",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ck_reconciliation_results_v2_input_hash",
        ),
        sa.CheckConstraint(
            "length(result_hash) = 64", name="ck_reconciliation_results_v2_result_hash"
        ),
        sa.CheckConstraint(
            "status IN ('pass','rounding_pass','warning','critical','blocking')",
            name="ck_reconciliation_results_v2_status",
        ),
    )
    op.create_index(
        "ix_reconciliation_results_v2_workspace_run",
        "reconciliation_results_v2",
        ["workspace_id", "run_id"],
    )


def downgrade():
    op.drop_index(
        "ix_reconciliation_results_v2_workspace_run",
        table_name="reconciliation_results_v2",
    )
    op.drop_table("reconciliation_results_v2")
