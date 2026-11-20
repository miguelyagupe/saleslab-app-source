"""Regression matrix for hedge authorization and transient evidence identity."""

from dataclasses import replace
from uuid import uuid4

import pytest

from saleslab_v2.domain.enums import Capability, DecisionStatus, Severity
from saleslab_v2.quality.capabilities import decide_capabilities
from saleslab_v2.quality.coverage import assess_coverage, validate_coverage
from saleslab_v2.quality.issues import blocking_issue
from saleslab_v2.quality.policies.financial_v1 import HEDGE_REQUIREMENTS, REQUIREMENTS
from saleslab_v2.quality.requirements import requirement_for

HEDGE_PROFILE = "financial_hedge_v1"
HEDGE_CAPABILITIES = (Capability.QUANTIFY_IMPACT, Capability.RECOMMEND_MATERIAL)


def test_hedge_impact_cannot_use_generic_prerequisites_without_a_manual_blocker():
    run_id = uuid4()
    required = REQUIREMENTS[Capability.QUANTIFY_IMPACT]
    generic = assess_coverage(run_id, required, required)
    hedge = assess_coverage(run_id, required, required, profile=HEDGE_PROFILE)
    normal = decide_capabilities(
        run_id, generic, capabilities=(Capability.QUANTIFY_IMPACT,)
    )[0]
    decision = decide_capabilities(
        run_id, hedge, capabilities=(Capability.QUANTIFY_IMPACT,)
    )[0]
    assert normal.allowed
    assert decision.decision_status == DecisionStatus.BLOCKED
    assert set(decision.requirements_to_unlock) == set(HEDGE_REQUIREMENTS)
    assert decision.supporting_issue_ids == ()
    assert decision.rule_version == hedge.assessment_rule_version
    assert decision.rule_version != normal.rule_version


@pytest.mark.parametrize("capability", HEDGE_CAPABILITIES)
@pytest.mark.parametrize("missing", HEDGE_REQUIREMENTS)
@pytest.mark.parametrize("ambiguous", (False, True))
def test_each_hedge_prerequisite_is_mandatory(capability, missing, ambiguous):
    requirement = requirement_for(capability, HEDGE_PROFILE)
    assert set(REQUIREMENTS[capability]) | set(HEDGE_REQUIREMENTS) == set(
        requirement.required_inputs
    )
    present = tuple(x for x in requirement.required_inputs if x != missing)
    coverage = assess_coverage(
        uuid4(),
        requirement.required_inputs,
        requirement.required_inputs if ambiguous else present,
        (missing,) if ambiguous else (),
        profile=HEDGE_PROFILE,
    )
    decision = decide_capabilities(
        coverage.run_id, coverage, capabilities=(capability,)
    )[0]
    assert decision.decision_status == DecisionStatus.BLOCKED
    assert missing in decision.requirements_to_unlock
    assert (
        requirement.rule_version
        == coverage.assessment_rule_version
        == decision.rule_version
    )
    assert validate_coverage(coverage) == coverage


@pytest.mark.parametrize("capability", HEDGE_CAPABILITIES)
def test_complete_hedge_profile_unlocks_applicable_capabilities(capability):
    required = requirement_for(capability, HEDGE_PROFILE).required_inputs
    coverage = assess_coverage(uuid4(), required, required, profile=HEDGE_PROFILE)
    assert decide_capabilities(coverage.run_id, coverage, capabilities=(capability,))[
        0
    ].allowed


@pytest.mark.parametrize("capability", tuple(Capability))
def test_hedge_policy_preserves_unrelated_capability_requirements(capability):
    required = requirement_for(capability, HEDGE_PROFILE).required_inputs
    expected = set(REQUIREMENTS[capability])
    if capability in HEDGE_CAPABILITIES:
        expected.update(HEDGE_REQUIREMENTS)
    assert set(required) == expected
    if capability not in HEDGE_CAPABILITIES:
        coverage = assess_coverage(uuid4(), required, required, profile=HEDGE_PROFILE)
        assert decide_capabilities(
            coverage.run_id, coverage, capabilities=(capability,)
        )[0].allowed


def test_unknown_profiles_cannot_silently_select_generic_financial_rules():
    with pytest.raises(ValueError, match="unsupported financial profile"):
        requirement_for(Capability.QUANTIFY_IMPACT, "unknown")
    with pytest.raises(ValueError, match="unsupported financial profile"):
        assess_coverage(uuid4(), ("a",), ("a",), profile="unknown")


@pytest.mark.parametrize(
    "changes",
    (
        {"code": "FIN-OTHER"},
        {"severity": Severity.CRITICAL},
        {"category": "other"},
        {"message_key": "other"},
        {"affected_refs": ("other",)},
        {"expected": "expected"},
        {"observed": "observed"},
        {"remediation_key": "other"},
        {"blocking_capabilities": (Capability.FORECAST,)},
    ),
)
def test_reused_transient_id_cannot_identify_different_issue_semantics(changes):
    coverage = assess_coverage(uuid4(), ("a",), ("a",))
    issue = blocking_issue("FIN-FIRST", "financial", "first", (Capability.SUMMARIZE,))
    with pytest.raises(ValueError, match="conflicting.*issue_id"):
        decide_capabilities(
            coverage.run_id, coverage, (issue, replace(issue, **changes))
        )


def test_reused_transient_id_allows_only_semantically_equivalent_duplicates():
    coverage = assess_coverage(uuid4(), ("a",), ("a",))
    issue = blocking_issue(
        "FIN-FIRST",
        "financial",
        "first",
        HEDGE_CAPABILITIES,
        affected_refs=("a", "b"),
    )
    duplicate = replace(
        issue,
        blocking_capabilities=tuple(reversed(HEDGE_CAPABILITIES)) + HEDGE_CAPABILITIES,
        affected_refs=("b", "a", "b"),
    )
    decisions = decide_capabilities(coverage.run_id, coverage, (issue, duplicate))
    for decision in decisions:
        assert decision.supporting_issue_ids == (
            (issue.issue_id,) if decision.capability in HEDGE_CAPABILITIES else ()
        )
