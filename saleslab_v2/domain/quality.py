from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import UUID, uuid4

from saleslab_v2.domain.enums import Capability, DecisionStatus, ModuleType, QualityLevel, Severity

def _utcnow():
    return datetime.now(timezone.utc)

@dataclass(frozen=True)
class RequiredInputSet:
    profile: str
    capability: Capability
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...] = ()
    rule_version: str = "financial-quality-v1"

    def __post_init__(self):
        if not self.profile or not self.required_inputs:
            raise ValueError("required input set needs profile and inputs")
        if len(set(self.required_inputs)) != len(self.required_inputs):
            raise ValueError("required inputs must be unique")

@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    severity: Severity
    category: str
    message_key: str
    affected_refs: tuple[str, ...] = ()
    expected: str | None = None
    observed: str | None = None
    remediation_key: str | None = None
    blocking_capabilities: tuple[Capability, ...] = ()
    issue_id: UUID = field(default_factory=uuid4)

    @property
    def blocks_execution(self) -> bool:
        return self.severity == Severity.BLOCKING or bool(self.blocking_capabilities)

@dataclass(frozen=True)
class CoverageAssessment:
    run_id: UUID
    module: ModuleType
    required_inputs: tuple[str, ...]
    present_inputs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    ambiguous_inputs: tuple[str, ...]
    quality_level: QualityLevel
    score_components: MappingProxyType
    assessment_rule_version: str
    assessed_at: datetime = field(default_factory=_utcnow)
    assessment_id: UUID = field(default_factory=uuid4)

    def to_dict(self):
        return {
            "assessment_id": str(self.assessment_id), "run_id": str(self.run_id),
            "module": self.module.value, "required_inputs": list(self.required_inputs),
            "present_inputs": list(self.present_inputs), "missing_inputs": list(self.missing_inputs),
            "ambiguous_inputs": list(self.ambiguous_inputs), "quality_level": self.quality_level.value,
            "score_components": dict(self.score_components),
            "assessment_rule_version": self.assessment_rule_version,
            "assessed_at": self.assessed_at.isoformat(),
        }

@dataclass(frozen=True)
class AnalysisCapabilityDecision:
    run_id: UUID
    capability: Capability
    decision_status: DecisionStatus
    reason_codes: tuple[str, ...]
    supporting_issue_ids: tuple[UUID, ...]
    requirements_to_unlock: tuple[str, ...]
    rule_version: str
    decision_id: UUID = field(default_factory=uuid4)

    @property
    def allowed(self) -> bool:
        return self.decision_status == DecisionStatus.ALLOWED
