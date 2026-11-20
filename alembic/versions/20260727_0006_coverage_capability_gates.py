"""Add F-05 coverage and capability gate persistence."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_0006"
down_revision = "20260729_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_quality_issues_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("message_key", sa.String(120), nullable=False),
        sa.Column("affected_refs", postgresql.JSONB(), nullable=False),
        sa.Column("expected", sa.String(1000), nullable=True),
        sa.Column("observed", sa.String(1000), nullable=True),
        sa.Column("remediation_key", sa.String(120), nullable=True),
        sa.Column("blocking_capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_data_quality_issues_v2"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_data_quality_issues_v2_workspace", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs_v2.id"], name="fk_data_quality_issues_v2_run", ondelete="CASCADE"),
        sa.CheckConstraint("severity IN ('info','warning','critical','blocking')", name="ck_data_quality_issues_v2_severity"),
    )
    op.create_index("ix_data_quality_issues_v2_workspace_run", "data_quality_issues_v2", ["workspace_id", "run_id"])
    op.create_table(
        "coverage_assessments_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("required_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("present_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("missing_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("ambiguous_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("quality_level", sa.String(16), nullable=False),
        sa.Column("score_components", postgresql.JSONB(), nullable=False),
        sa.Column("assessment_rule_version", sa.String(64), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_coverage_assessments_v2"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_coverage_assessments_v2_workspace", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs_v2.id"], name="fk_coverage_assessments_v2_run", ondelete="CASCADE"),
        sa.CheckConstraint("quality_level IN ('complete','sufficient','partial','insufficient')", name="ck_coverage_assessments_v2_quality"),
        sa.UniqueConstraint("workspace_id", "run_id", "module", "assessment_rule_version", name="uq_coverage_assessments_v2_execution"),
    )
    op.create_index("ix_coverage_assessments_v2_workspace_run", "coverage_assessments_v2", ["workspace_id", "run_id"])
    op.create_table(
        "capability_decisions_v2",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("decision_status", sa.String(16), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("supporting_issue_ids", postgresql.JSONB(), nullable=False),
        sa.Column("requirements_to_unlock", postgresql.JSONB(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_capability_decisions_v2"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_capability_decisions_v2_workspace", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs_v2.id"], name="fk_capability_decisions_v2_run", ondelete="CASCADE"),
        sa.CheckConstraint("decision_status IN ('allowed','conditional','blocked')", name="ck_capability_decisions_v2_status"),
        sa.UniqueConstraint("workspace_id", "run_id", "capability", "rule_version", name="uq_capability_decisions_v2_execution"),
    )
    op.create_index("ix_capability_decisions_v2_workspace_run", "capability_decisions_v2", ["workspace_id", "run_id"])


def downgrade():
    op.drop_index("ix_capability_decisions_v2_workspace_run", table_name="capability_decisions_v2")
    op.drop_table("capability_decisions_v2")
    op.drop_index("ix_coverage_assessments_v2_workspace_run", table_name="coverage_assessments_v2")
    op.drop_table("coverage_assessments_v2")
    op.drop_index("ix_data_quality_issues_v2_workspace_run", table_name="data_quality_issues_v2")
    op.drop_table("data_quality_issues_v2")
