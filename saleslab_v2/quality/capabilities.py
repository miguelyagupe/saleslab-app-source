from saleslab_v2.domain.enums import Capability, DecisionStatus, QualityLevel
from saleslab_v2.domain.quality import AnalysisCapabilityDecision

from .coverage import validate_coverage
from .issues import validate_issue_identities
from .policies.financial_v1 import (
    F0_BLOCKED,
    PARTIAL_BLOCKED,
    PARTIAL_CONDITIONAL,
)
from .requirements import profile_for_rule_version, requirement_for


def _missing_capability_requirements(coverage, capability):
    """Return hard prerequisites that are not unambiguously present.

    Capability requirements are authoritative even when the caller built the
    module-level coverage assessment from a narrower input profile. This keeps
    a COMPLETE aggregate score from bypassing forecast, impact, or material
    recommendation prerequisites.
    """
    present = set(coverage.present_inputs)
    return tuple(
        input_name
        for input_name in requirement_for(
            capability, profile_for_rule_version(coverage.assessment_rule_version)
        ).required_inputs
        if input_name not in present
    )


def decide_capabilities(
    run_id,
    coverage,
    issues=(),
    capabilities=tuple(Capability),
):
    if run_id != coverage.run_id:
        raise ValueError("quality gate coverage run mismatch")
    coverage = validate_coverage(coverage)
    issues = tuple(issues)
    validate_issue_identities(issues)
    decisions = []
    for capability in capabilities:
        capability_issues = tuple(
            issue
            for issue in issues
            if capability in issue.blocking_capabilities
            or (issue.severity.value == "blocking" and not issue.blocking_capabilities)
        )
        reasons = []
        unlock = []
        missing_requirements = _missing_capability_requirements(
            coverage,
            capability,
        )

        if capability_issues:
            status = DecisionStatus.BLOCKED
            reasons.append("blocking_issue_precedence")
            unlock.extend(
                sorted(
                    {
                        issue.remediation_key
                        for issue in capability_issues
                        if issue.remediation_key
                    }
                )
            )
        elif (
            coverage.quality_level == QualityLevel.INSUFFICIENT
            and capability in F0_BLOCKED
        ):
            status = DecisionStatus.BLOCKED
            reasons.append("f0_fragmentary")
            unlock.append("provide_core_financials")
        elif (
            coverage.quality_level == QualityLevel.PARTIAL
            and capability in PARTIAL_BLOCKED
        ):
            status = DecisionStatus.BLOCKED
            reasons.append("partial_blocks_material_or_forecast")
            unlock.extend(missing_requirements)
        elif (
            coverage.quality_level == QualityLevel.PARTIAL
            and capability in PARTIAL_CONDITIONAL
        ):
            status = DecisionStatus.CONDITIONAL
            reasons.append("partial_allows_limited_local_work")
            unlock.extend(missing_requirements)
        elif missing_requirements:
            status = DecisionStatus.BLOCKED
            reasons.append("capability_requirements_missing")
            unlock.extend(missing_requirements)
        elif (
            coverage.quality_level == QualityLevel.SUFFICIENT
            and capability == Capability.RECOMMEND_MATERIAL
        ):
            status = DecisionStatus.CONDITIONAL
            reasons.append("material_commitment_requires_complete_coverage")
            unlock.append("complete_coverage")
        else:
            status = DecisionStatus.ALLOWED
            reasons.append("coverage_sufficient")

        decisions.append(
            AnalysisCapabilityDecision(
                run_id,
                capability,
                status,
                tuple(dict.fromkeys(reasons)),
                tuple(
                    sorted(
                        {issue.issue_id for issue in capability_issues},
                        key=str,
                    )
                ),
                tuple(dict.fromkeys(str(item) for item in unlock)),
                coverage.assessment_rule_version,
            )
        )
    return tuple(decisions)
