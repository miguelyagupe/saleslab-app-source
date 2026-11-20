from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .enums import ModuleType, StageStatus, WorkflowStatus
from .idempotency import canonicalize, semantic_hash
from .language import LanguageContext


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _sha256(value: str | None, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be lowercase SHA-256 hex")


TRANSITIONS = {
    WorkflowStatus.RECEIVED: {WorkflowStatus.PARSING, WorkflowStatus.FAILED},
    WorkflowStatus.PARSING: {
        WorkflowStatus.MAPPING_REQUIRED,
        WorkflowStatus.VALIDATING,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.MAPPING_REQUIRED: {WorkflowStatus.VALIDATING, WorkflowStatus.FAILED},
    WorkflowStatus.VALIDATING: {
        WorkflowStatus.BLOCKED,
        WorkflowStatus.CALCULATING,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.BLOCKED: {WorkflowStatus.VALIDATING, WorkflowStatus.FAILED},
    WorkflowStatus.CALCULATING: {WorkflowStatus.ANALYZING, WorkflowStatus.FAILED},
    WorkflowStatus.ANALYZING: {WorkflowStatus.AUDITING, WorkflowStatus.FAILED},
    WorkflowStatus.AUDITING: {
        WorkflowStatus.ANALYZING,
        WorkflowStatus.COMPILING,
        WorkflowStatus.BLOCKED,
        WorkflowStatus.FAILED,
    },
    WorkflowStatus.COMPILING: {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED},
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.FAILED: set(),  # F-01 retries create a new run; no quota-reservation fiction.
}


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisRun:
    workspace_id: UUID
    created_by_user_id: UUID
    module: ModuleType
    schema_version: str
    pipeline_version: str
    language_context: LanguageContext
    source_ids: tuple[UUID, ...]
    input_hashes: tuple[str, ...]
    idempotency_purpose: str
    idempotency_key: str
    run_id: UUID = field(default_factory=uuid4)
    status: WorkflowStatus = WorkflowStatus.RECEIVED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure: Failure | None = None
    legacy_comparison_id: UUID | None = None
    allow_empty_sources: bool = False

    def __post_init__(self):
        if not self.schema_version or not self.pipeline_version:
            raise ValueError("schema and pipeline versions are required")
        if not self.idempotency_purpose or not self.idempotency_key:
            raise ValueError("idempotency purpose and key are required")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source IDs must not contain duplicates")
        if not self.source_ids and not self.allow_empty_sources:
            raise ValueError("source-less runs require explicit internal opt-in")
        for value in self.input_hashes:
            _sha256(value, "input hash")
        _aware(self.created_at, "created_at")
        for name in ("started_at", "completed_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)
        terminal = self.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}
        if terminal != (self.completed_at is not None):
            raise ValueError(
                "terminal states require completed_at; non-terminal states forbid it"
            )
        if self.status is WorkflowStatus.FAILED and self.failure is None:
            raise ValueError("failed runs require structured failure")
        if self.status is not WorkflowStatus.FAILED and self.failure is not None:
            raise ValueError("failure is only valid for failed runs")

    def idempotency_payload(self) -> dict[str, object]:
        """Return every semantically relevant input for run idempotency.

        ``format_locale`` is deliberately absent because it controls presentation,
        not analysis semantics. Operational/output fields such as IDs, user, status,
        timestamps, failure and legacy comparison are likewise not request inputs.
        """
        return {
            "module": self.module.value,
            "schema_version": self.schema_version,
            "pipeline_version": self.pipeline_version,
            "source_ids": self.source_ids,
            "input_hashes": self.input_hashes,
            **self.language_context.semantic_dict(),
        }

    @property
    def request_fingerprint(self) -> str:
        """SHA-256 of the canonical semantic request payload."""
        return semantic_hash(self.idempotency_payload())

    def transition(
        self, status: WorkflowStatus, failure: Failure | None = None
    ) -> "AnalysisRun":
        if status not in TRANSITIONS[self.status]:
            raise ValueError(
                f"invalid transition: {self.status.value} -> {status.value}"
            )
        if status is WorkflowStatus.FAILED and failure is None:
            raise ValueError("failed runs require structured failure")
        now = datetime.now(timezone.utc)
        return replace(
            self,
            status=status,
            started_at=self.started_at or now,
            completed_at=(
                now
                if status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}
                else None
            ),
            failure=failure,
        )


@dataclass(frozen=True)
class StageResult:
    run_id: UUID
    stage_name: str
    stage_version: str
    status: StageStatus
    input_hash: str
    output_hash: str | None
    output_schema_version: str
    payload: dict[str, Any]
    stage_result_id: UUID = field(default_factory=uuid4)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    error: Failure | None = None

    def __post_init__(self):
        if (
            not self.stage_name
            or not self.stage_version
            or not self.output_schema_version
        ):
            raise ValueError(
                "stage name, stage version and output schema version are required"
            )
        _sha256(self.input_hash, "input hash")
        _sha256(self.output_hash, "output hash", optional=True)
        _aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _aware(self.completed_at, "completed_at")
        canonicalize(self.payload)
        if self.status is StageStatus.RUNNING and self.completed_at is not None:
            raise ValueError("running stage cannot have completed_at")
        if self.status is not StageStatus.RUNNING and self.completed_at is None:
            raise ValueError("completed and failed stages require completed_at")
        if self.status is StageStatus.COMPLETED and self.output_hash is None:
            raise ValueError("completed stage requires output hash")
        if self.status is StageStatus.FAILED and self.error is None:
            raise ValueError("failed stage requires structured error")
        if self.status is not StageStatus.FAILED and self.error is not None:
            raise ValueError("error is only valid for failed stages")
