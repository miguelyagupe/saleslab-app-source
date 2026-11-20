import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def utcnow():
    return datetime.now(timezone.utc)


class AnalysisRunModel(Base):
    __tablename__ = "analysis_runs_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_analysis_runs_v2"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_analysis_runs_v2_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_analysis_runs_v2_created_by"
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_purpose",
            "idempotency_key",
            name="uq_analysis_runs_v2_workspace_purpose_key",
        ),
        Index("ix_analysis_runs_v2_workspace_created", "workspace_id", "created_at"),
        Index(
            "ix_analysis_runs_v2_workspace_status", "workspace_id", "workflow_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    module: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    language_context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    input_hashes: Mapped[list] = mapped_column(JSONB, nullable=False)
    idempotency_purpose: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure: Mapped[dict | None] = mapped_column(JSONB)
    legacy_comparison_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class SourceDocumentModel(Base):
    __tablename__ = "source_documents_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_source_documents_v2"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_source_documents_v2_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["supersedes_source_id"],
            ["source_documents_v2.id"],
            name="fk_source_documents_v2_supersedes",
        ),
        Index("ix_source_documents_v2_workspace_hash", "workspace_id", "content_hash"),
        Index("ix_source_documents_v2_workspace_created", "workspace_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False)
    language_metadata: Mapped[list] = mapped_column(JSONB, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    supersedes_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class StageResultModel(Base):
    __tablename__ = "stage_results_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_stage_results_v2"),
        ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_stage_results_v2_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "run_id",
            "stage_name",
            "stage_version",
            "input_hash",
            name="uq_stage_results_v2_replay",
        ),
        Index("ix_stage_results_v2_run_created", "run_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(100), nullable=False)
    stage_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    output_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSONB)


class CalculationTraceModel(Base):
    __tablename__ = "calculation_traces_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_calculation_traces_v2"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_calculation_traces_v2_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_calculation_traces_v2_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "trace_hash",
            name="uq_calculation_traces_v2_scope_hash",
        ),
        UniqueConstraint(
            "id", "workspace_id", "run_id", name="uq_calculation_traces_v2_scoped_id"
        ),
        CheckConstraint(
            "length(trace_hash) = 64", name="ck_calculation_traces_v2_hash_length"
        ),
        Index("ix_calculation_traces_v2_workspace_run", "workspace_id", "run_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    metric_id: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_definition_version: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_expression: Mapped[str] = mapped_column(String(1000), nullable=False)
    inputs: Mapped[list] = mapped_column(JSONB, nullable=False)
    exact_output: Mapped[str] = mapped_column(String(1000), nullable=False)
    rounded_output: Mapped[str | None] = mapped_column(String(1000))
    rounding_policy: Mapped[str | None] = mapped_column(String(64))
    rounding_version: Mapped[str | None] = mapped_column(String(32))
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class MetricObservationModel(Base):
    __tablename__ = "metric_observations_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_metric_observations_v2"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_metric_observations_v2_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_metric_observations_v2_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["calculation_trace_id", "workspace_id", "run_id"],
            [
                "calculation_traces_v2.id",
                "calculation_traces_v2.workspace_id",
                "calculation_traces_v2.run_id",
            ],
            name="fk_metric_observations_v2_trace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "calculation_trace_id",
            name="uq_metric_observations_v2_scope_trace",
        ),
        CheckConstraint(
            "status = 'calculated'", name="ck_metric_observations_v2_status"
        ),
        Index(
            "ix_metric_observations_v2_workspace_run_metric",
            "workspace_id",
            "run_id",
            "metric_id",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    metric_id: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_definition_version: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    rounded_value: Mapped[Decimal | None] = mapped_column(Numeric())
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    period: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    dimensions: Mapped[list] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[list] = mapped_column(JSONB, nullable=False)
    source_references: Mapped[list] = mapped_column(JSONB, nullable=False)
    calculation_trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    calculation_trace_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ReconciliationResultModel(Base):
    __tablename__ = "reconciliation_results_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_reconciliation_results_v2"),
        ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_reconciliation_results_v2_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs_v2.id"],
            name="fk_reconciliation_results_v2_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "run_id",
            "rule_id",
            "rule_version",
            name="uq_reconciliation_results_v2_execution",
        ),
        CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ck_reconciliation_results_v2_input_hash",
        ),
        CheckConstraint(
            "length(result_hash) = 64", name="ck_reconciliation_results_v2_result_hash"
        ),
        CheckConstraint(
            "status IN ('pass','rounding_pass','warning','critical','blocking')",
            name="ck_reconciliation_results_v2_status",
        ),
        Index("ix_reconciliation_results_v2_workspace_run", "workspace_id", "run_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DataQualityIssueModel(Base):
    __tablename__ = "data_quality_issues_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_data_quality_issues_v2"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_data_quality_issues_v2_workspace", ondelete="CASCADE"),
        ForeignKeyConstraint(["run_id"], ["analysis_runs_v2.id"], name="fk_data_quality_issues_v2_run", ondelete="CASCADE"),
        CheckConstraint("severity IN ('info','warning','critical','blocking')", name="ck_data_quality_issues_v2_severity"),
        Index("ix_data_quality_issues_v2_workspace_run", "workspace_id", "run_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message_key: Mapped[str] = mapped_column(String(120), nullable=False)
    affected_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    expected: Mapped[str | None] = mapped_column(String(1000))
    observed: Mapped[str | None] = mapped_column(String(1000))
    remediation_key: Mapped[str | None] = mapped_column(String(120))
    blocking_capabilities: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CoverageAssessmentModel(Base):
    __tablename__ = "coverage_assessments_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_coverage_assessments_v2"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_coverage_assessments_v2_workspace", ondelete="CASCADE"),
        ForeignKeyConstraint(["run_id"], ["analysis_runs_v2.id"], name="fk_coverage_assessments_v2_run", ondelete="CASCADE"),
        UniqueConstraint("workspace_id", "run_id", "module", "assessment_rule_version", name="uq_coverage_assessments_v2_execution"),
        CheckConstraint("quality_level IN ('complete','sufficient','partial','insufficient')", name="ck_coverage_assessments_v2_quality"),
        Index("ix_coverage_assessments_v2_workspace_run", "workspace_id", "run_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    module: Mapped[str] = mapped_column(String(32), nullable=False)
    required_inputs: Mapped[list] = mapped_column(JSONB, nullable=False)
    present_inputs: Mapped[list] = mapped_column(JSONB, nullable=False)
    missing_inputs: Mapped[list] = mapped_column(JSONB, nullable=False)
    ambiguous_inputs: Mapped[list] = mapped_column(JSONB, nullable=False)
    quality_level: Mapped[str] = mapped_column(String(16), nullable=False)
    score_components: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assessment_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CapabilityDecisionModel(Base):
    __tablename__ = "capability_decisions_v2"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_capability_decisions_v2"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], name="fk_capability_decisions_v2_workspace", ondelete="CASCADE"),
        ForeignKeyConstraint(["run_id"], ["analysis_runs_v2.id"], name="fk_capability_decisions_v2_run", ondelete="CASCADE"),
        UniqueConstraint("workspace_id", "run_id", "capability", "rule_version", name="uq_capability_decisions_v2_execution"),
        CheckConstraint("decision_status IN ('allowed','conditional','blocked')", name="ck_capability_decisions_v2_status"),
        Index("ix_capability_decisions_v2_workspace_run", "workspace_id", "run_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    supporting_issue_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    requirements_to_unlock: Mapped[list] = mapped_column(JSONB, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
