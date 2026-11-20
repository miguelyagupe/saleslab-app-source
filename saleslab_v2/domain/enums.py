from enum import Enum


class CanonicalEnum(str, Enum):
    """A stable string enum suitable for JSON and database persistence."""


class ModuleType(CanonicalEnum):
    FINANCIAL = "financial"
    SALES = "sales"
    COST = "cost"
    EFFICIENCY = "efficiency"


class SourceFormat(CanonicalEnum):
    CSV = "csv"
    XLSX = "xlsx"
    XLS = "xls"
    PDF = "pdf"
    MANUAL = "manual"


def _enum(name: str, values: str):
    return Enum(
        name, {value.upper(): value for value in values.split()}, type=CanonicalEnum
    )


ProvenanceType = _enum(
    "ProvenanceType", "published user_provided extracted calculated inferred estimated"
)
ClaimType = _enum(
    "ClaimType", "fact calculation association hypothesis scenario recommendation"
)
QualityLevel = _enum("QualityLevel", "complete sufficient partial insufficient")
Severity = _enum("Severity", "info warning critical blocking")
ConfidenceBand = _enum("ConfidenceBand", "low medium high")
AuditStatus = _enum(
    "AuditStatus", "approved approved_with_warnings revision_required blocked"
)
DecisionStatus = _enum("DecisionStatus", "allowed conditional blocked")
Capability = _enum(
    "Capability",
    "summarize calculate compare diagnose infer_cause forecast quantify_impact "
    "recommend_reversible recommend_material",
)
NullReason = _enum("NullReason", "missing unknown not_applicable redacted parse_error")
MappingStatus = _enum(
    "MappingStatus", "auto_confirmed needs_confirmation user_confirmed rejected"
)
TrustLevel = _enum("TrustLevel", "declared verified untrusted")
LanguageCode = _enum("LanguageCode", "pt en es fr")
TranslationStatus = _enum(
    "TranslationStatus", "approved machine_draft needs_review missing rejected"
)


class WorkflowStatus(CanonicalEnum):
    RECEIVED = "received"
    PARSING = "parsing"
    MAPPING_REQUIRED = "mapping_required"
    VALIDATING = "validating"
    BLOCKED = "blocked"
    CALCULATING = "calculating"
    ANALYZING = "analyzing"
    AUDITING = "auditing"
    COMPILING = "compiling"
    COMPLETED = "completed"
    FAILED = "failed"


class StageStatus(CanonicalEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
