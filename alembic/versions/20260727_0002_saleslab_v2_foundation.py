"""Add the isolated SalesLab V2 run, stage and source tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_runs_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("workflow_status", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("pipeline_version", sa.String(64), nullable=False),
        sa.Column("language_context", postgresql.JSONB(), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), nullable=False),
        sa.Column("input_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_purpose", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failure", postgresql.JSONB()),
        sa.Column("legacy_comparison_id", postgresql.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_analysis_runs_v2_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_analysis_runs_v2_created_by"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_runs_v2"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_purpose",
            "idempotency_key",
            name="uq_analysis_runs_v2_workspace_purpose_key",
        ),
    )
    op.create_index(
        "ix_analysis_runs_v2_workspace_created",
        "analysis_runs_v2",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_analysis_runs_v2_workspace_status",
        "analysis_runs_v2",
        ["workspace_id", "workflow_status"],
    )
    op.create_table(
        "source_documents_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_format", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("trust_level", sa.String(16), nullable=False),
        sa.Column("language_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_source_id", postgresql.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_source_documents_v2_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_source_id"],
            ["source_documents_v2.id"],
            name="fk_source_documents_v2_supersedes",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_documents_v2"),
    )
    op.create_index(
        "ix_source_documents_v2_workspace_hash",
        "source_documents_v2",
        ["workspace_id", "content_hash"],
    )
    op.create_index(
        "ix_source_documents_v2_workspace_created",
        "source_documents_v2",
        ["workspace_id", "created_at"],
    )
    op.create_table(
        "stage_results_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_name", sa.String(100), nullable=False),
        sa.Column("stage_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("output_schema_version", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", postgresql.JSONB()),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_stage_results_v2_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stage_results_v2"),
        sa.UniqueConstraint(
            "run_id",
            "stage_name",
            "stage_version",
            "input_hash",
            name="uq_stage_results_v2_replay",
        ),
    )
    op.create_index(
        "ix_stage_results_v2_run_created", "stage_results_v2", ["run_id", "created_at"]
    )


def downgrade():
    op.drop_index("ix_stage_results_v2_run_created", table_name="stage_results_v2")
    op.drop_table("stage_results_v2")
    op.drop_index(
        "ix_source_documents_v2_workspace_created", table_name="source_documents_v2"
    )
    op.drop_index(
        "ix_source_documents_v2_workspace_hash", table_name="source_documents_v2"
    )
    op.drop_table("source_documents_v2")
    op.drop_index("ix_analysis_runs_v2_workspace_status", table_name="analysis_runs_v2")
    op.drop_index(
        "ix_analysis_runs_v2_workspace_created", table_name="analysis_runs_v2"
    )
    op.drop_table("analysis_runs_v2")
