"""ReportResult is the sole publication source, never a second model response."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

COMPILER_VERSION = "financial-report-v1"
RENDER_VERSION = "financial-render-v3"
REPORT_LOCALES = {"pt": "pt-PT", "en": "en-GB", "es": "es-ES", "fr": "fr-FR"}


class InvalidReport(ValueError):
    pass


class ReportParityError(InvalidReport):
    pass


class RenderedArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    html: str = Field(max_length=4_000_000)
    pdf_base64: str = Field(max_length=10_666_668)
    html_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parity: dict


class ExecutiveSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    headline_key: Literal["financial_report"]
    verified_fact_ids: list[str]
    material_finding_ids: list[str]
    limitation_ids: list[str]
    next_action_ids: list[str]


class ReportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    report_id: str
    run_id: str
    workspace_id: str
    schema_version: Literal["1"]
    module: Literal["financial"]
    generated_at: str
    compiler_version: Literal["financial-report-v1"]
    report_version: Literal["1"]
    render_version: Literal[
        "financial-render-v1",
        "financial-render-v2",
        "financial-render-v3",
        "financial-render-v4",
    ]
    language: Literal["pt", "en", "es", "fr"]
    locale: Literal["pt-PT", "en-GB", "es-ES", "fr-FR"]
    company_profile: dict | None
    analysis_periods: list[dict]
    comparison_basis: list[str]
    executive_summary: ExecutiveSummary
    coverage_assessment: dict
    capability_decisions: list[dict]
    data_quality_issues: list[dict]
    metric_observations: list[dict]
    evidence: list[dict]
    findings: list[dict]
    forecasts: list[dict]
    recommendations: list[dict]
    reconciliations: list[dict]
    audit_result: dict
    missing_data_requests: list[dict]
    limitations: list[dict]
    methodology: dict
    source_index: list[dict]
    approval_binding: dict
    display_preferences: dict


@dataclass(frozen=True)
class CompiledReport:
    run_id: UUID
    payload: dict
    artifacts: dict
    replayed: bool
