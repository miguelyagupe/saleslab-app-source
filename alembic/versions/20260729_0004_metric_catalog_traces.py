"""Add F-03 calculated observations and deterministic traces."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260729_0004"
down_revision = "20260727_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calculation_traces_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", sa.String(100), nullable=False),
        sa.Column("metric_definition_version", sa.String(32), nullable=False),
        sa.Column("canonical_expression", sa.String(1000), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("exact_output", sa.String(1000), nullable=False),
        sa.Column("rounded_output", sa.String(1000)),
        sa.Column("rounding_policy", sa.String(64)),
        sa.Column("rounding_version", sa.String(32)),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("trace_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_calculation_traces_v2"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_calculation_traces_v2_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_calculation_traces_v2_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "run_id",
            "trace_hash",
            name="uq_calculation_traces_v2_scope_hash",
        ),
        sa.UniqueConstraint(
            "id", "workspace_id", "run_id", name="uq_calculation_traces_v2_scoped_id"
        ),
        sa.CheckConstraint(
            "length(trace_hash) = 64", name="ck_calculation_traces_v2_hash_length"
        ),
    )
    op.create_index(
        "ix_calculation_traces_v2_workspace_run",
        "calculation_traces_v2",
        ["workspace_id", "run_id"],
    )
    op.create_table(
        "metric_observations_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", sa.String(100), nullable=False),
        sa.Column("metric_definition_version", sa.String(32), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("rounded_value", sa.Numeric()),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.Column("period", postgresql.JSONB(), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("source_references", postgresql.JSONB(), nullable=False),
        sa.Column(
            "calculation_trace_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("calculation_trace_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_metric_observations_v2"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_metric_observations_v2_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_metric_observations_v2_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["calculation_trace_id", "workspace_id", "run_id"],
            [
                "calculation_traces_v2.id",
                "calculation_traces_v2.workspace_id",
                "calculation_traces_v2.run_id",
            ],
            name="fk_metric_observations_v2_trace",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "run_id",
            "calculation_trace_id",
            name="uq_metric_observations_v2_scope_trace",
        ),
        sa.CheckConstraint(
            "status = 'calculated'", name="ck_metric_observations_v2_status"
        ),
    )
    op.create_index(
        "ix_metric_observations_v2_workspace_run_metric",
        "metric_observations_v2",
        ["workspace_id", "run_id", "metric_id"],
    )


def downgrade():
    op.drop_index(
        "ix_metric_observations_v2_workspace_run_metric",
        table_name="metric_observations_v2",
    )
    op.drop_table("metric_observations_v2")
    op.drop_index(
        "ix_calculation_traces_v2_workspace_run", table_name="calculation_traces_v2"
    )
    op.drop_table("calculation_traces_v2")
