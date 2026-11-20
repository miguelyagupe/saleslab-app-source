"""Versioned audit contracts. Models cannot select the final approval status."""

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.metrics.trace import TRACE_NAMESPACE

AUDITOR_VERSION = "financial-audit-v1"
MAX_REVISIONS = 2
CRITIC_CHECKS = frozenset(
    (
        "claim_support",
        "causality",
        "counterevidence",
        "scope",
        "language",
        "recommendation",
    )
)
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)
]


class CriticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    check_type: Literal[
        "claim_support",
        "causality",
        "counterevidence",
        "scope",
        "language",
        "recommendation",
    ]
    outcome: Literal["pass", "warning", "revise", "block"]
    message: Text
    remediation: Text | None


class FindingReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    finding_id: str
    checks: Annotated[list[CriticCheck], Field(min_length=6, max_length=6)]


class CriticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1"]
    language: Literal["pt", "en", "es", "fr"]
    findings: Annotated[list[FindingReview], Field(min_length=1, max_length=20)]


class InvalidCriticOutput(ValueError):
    pass


class CriticUnavailable(RuntimeError):
    pass


class IndependentCritic(Protocol):
    @property
    def configuration(self) -> dict: ...
    async def generate(self, context: dict) -> dict: ...


@dataclass(frozen=True)
class AuditResult:
    run_id: UUID
    payload: dict
    replayed: bool


def audit_check(
    audit_id,
    check_type,
    outcome,
    code,
    *,
    refs=(),
    message=None,
    remediation=None,
    deterministic=True,
):
    return {
        "check_id": str(
            uuid5(
                TRACE_NAMESPACE,
                f"audit-check:{audit_id}:{check_type}:{code}:{semantic_hash(list(refs))}",
            )
        ),
        "check_type": check_type,
        "status": outcome,
        "severity": {
            "pass": "info",
            "warning": "warning",
            "revise": "critical",
            "block": "blocking",
        }[outcome],
        "reason_code": code,
        "message": message or code,
        "related_object_ids": list(refs),
        "remediation": remediation,
        "deterministic": deterministic,
    }


def bind_critique(raw, draft, *, audit_id, language):
    try:
        if len(json.dumps(raw, allow_nan=False)) > 160_000:
            raise ValueError("critic response too large")
        response = CriticResponse.model_validate(raw)
        ids = {f["finding_id"] for f in draft["findings"]}
        if (
            response.language != language
            or len(response.findings) != len(ids)
            or {f.finding_id for f in response.findings} != ids
        ):
            raise ValueError(
                "critic must review each finding exactly once in the run language"
            )
        checks = []
        for finding in response.findings:
            if {c.check_type for c in finding.checks} != CRITIC_CHECKS:
                raise ValueError("critic must cover all semantic check families")
            for check in finding.checks:
                if check.outcome in {"revise", "block"} and check.remediation is None:
                    raise ValueError(
                        "failed semantic checks require actionable remediation"
                    )
                checks.append(
                    audit_check(
                        audit_id,
                        check.check_type,
                        check.outcome,
                        "critic." + check.check_type,
                        refs=(finding.finding_id,),
                        message=check.message,
                        remediation=check.remediation,
                        deterministic=False,
                    )
                )
        return checks, response.model_dump()
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidCriticOutput(
            "critic failed schema, coverage or reference validation"
        ) from exc


def assemble_result(
    *,
    audit_id,
    run_id,
    draft_stage_id,
    draft_hash,
    checkpoint_hash,
    checks,
    finding_ids,
    configuration,
    audited_at,
    revision_number,
    retryable=False,
):
    checks = deepcopy(checks)
    blocked = any(c["status"] == "block" for c in checks)
    revise = any(c["status"] == "revise" for c in checks)
    if revise and not blocked and revision_number >= MAX_REVISIONS:
        checks.append(
            audit_check(
                audit_id,
                "revision",
                "block",
                "audit.revision_budget_exhausted",
                refs=(str(draft_stage_id),),
                remediation="Provide corrected evidence in a new run or request human review.",
            )
        )
        blocked = True
    status = (
        "blocked"
        if blocked
        else (
            "revision_required"
            if revise
            else (
                "approved_with_warnings"
                if any(c["status"] == "warning" for c in checks)
                else "approved"
            )
        )
    )
    rejected = {
        fid
        for c in checks
        if c["status"] in {"revise", "block"}
        for fid in c["related_object_ids"]
        if fid in finding_ids
    }
    if blocked:
        rejected = set(finding_ids)
    return {
        "audit_id": str(audit_id),
        "run_id": str(run_id),
        "auditor_version": AUDITOR_VERSION,
        "draft_stage_id": str(draft_stage_id),
        "draft_hash": draft_hash,
        "checkpoint_hash": checkpoint_hash,
        "status": status,
        "checks": checks,
        "required_revisions": [
            {
                "check_id": c["check_id"],
                "finding_ids": [i for i in c["related_object_ids"] if i in finding_ids],
                "reason_code": c["reason_code"],
                "remediation": c["remediation"],
            }
            for c in checks
            if c["status"] in {"revise", "block"}
        ],
        "approved_object_ids": sorted(set(finding_ids) - rejected),
        "rejected_object_ids": sorted(rejected),
        "critic_configuration": deepcopy(configuration),
        "audited_at": audited_at.isoformat(),
        "revision_number": revision_number,
        "max_revisions": MAX_REVISIONS,
        "retryable": retryable,
        "report_published": False,
    }
