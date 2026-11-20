import json
from uuid import uuid5

from saleslab_v2.domain.enums import Capability, Severity
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.domain.quality import DataQualityIssue
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from .policies.financial_v1 import REMEDIATIONS


def canonical_issue_payload(issue):
    """The semantic payload shared by identity validation, hashing and storage."""
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "category": issue.category,
        "message_key": issue.message_key,
        "affected_refs": sorted(set(issue.affected_refs)),
        "expected": issue.expected,
        "observed": issue.observed,
        "remediation_key": issue.remediation_key,
        "blocking_capabilities": sorted({c.value for c in issue.blocking_capabilities}),
    }


def scoped_issue_id(workspace_id, run_id, issue):
    """The canonical identity used by pure pipelines and persistence alike."""
    payload = canonicalize(canonical_issue_payload(issue))
    identity = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return uuid5(
        uuid5(TRACE_NAMESPACE, "quality-issue"), f"{workspace_id}:{run_id}:{identity}"
    )


def validate_issue_identities(issues):
    """Reject a transient ID that would ambiguously identify different evidence."""
    seen = {}
    for issue in issues:
        payload = canonical_issue_payload(issue)
        if issue.issue_id in seen and seen[issue.issue_id] != payload:
            raise ValueError("conflicting semantics for reused issue_id")
        seen[issue.issue_id] = payload


def localized_remediation(key: str, language: str) -> str:
    return REMEDIATIONS[key][language]


def blocking_issue(
    code: str,
    category: str,
    message_key: str,
    capabilities: tuple[Capability, ...],
    remediation_key="provide_core_financials",
    affected_refs=(),
):
    return DataQualityIssue(
        code,
        Severity.BLOCKING,
        category,
        message_key,
        tuple(affected_refs),
        remediation_key=remediation_key,
        blocking_capabilities=capabilities,
    )
