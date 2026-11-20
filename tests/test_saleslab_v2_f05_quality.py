import os
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://unused/f05_test")

from saleslab_v2.domain.enums import Capability, DecisionStatus, QualityLevel
from saleslab_v2.quality.capabilities import decide_capabilities
from saleslab_v2.quality.coverage import assess_coverage
from saleslab_v2.quality.issues import blocking_issue, localized_remediation
from saleslab_v2.quality.policies.financial_v1 import HEDGE_REQUIREMENTS, REQUIREMENTS
from saleslab_v2.quality.requirements import requirement_for
from saleslab_v2.persistence.repositories import _quality_issue_id


def by_cap(decisions, capability):
    return next(d for d in decisions if d.capability == capability)


def test_blocking_issue_takes_precedence_over_complete_score():
    run_id = uuid4()
    coverage = assess_coverage(run_id, ["revenue"], ["revenue"])
    issue = blocking_issue(
        "FIN-SCOPE-UNKNOWN",
        "financial",
        "scope_unknown",
        (Capability.SUMMARIZE,),
    )

    decision = by_cap(decide_capabilities(run_id, coverage, (issue,)), Capability.SUMMARIZE)

    assert coverage.quality_level == QualityLevel.COMPLETE
    assert decision.decision_status == DecisionStatus.BLOCKED
    assert decision.reason_codes == ("blocking_issue_precedence",)
    assert decision.supporting_issue_ids == (issue.issue_id,)


def test_f0_fragmentary_blocks_every_analysis_capability():
    run_id = uuid4()
    coverage = assess_coverage(run_id, ["validated_statement"], [])
    decisions = decide_capabilities(run_id, coverage)

    assert coverage.quality_level == QualityLevel.INSUFFICIENT
    assert {decision.capability for decision in decisions} == set(Capability)
    assert all(
        decision.decision_status == DecisionStatus.BLOCKED
        for decision in decisions
    )
    assert by_cap(decisions, Capability.CALCULATE).reason_codes == (
        "f0_fragmentary",
    )
    assert by_cap(decisions, Capability.COMPARE).reason_codes == (
        "f0_fragmentary",
    )

def test_partial_allows_summary_and_local_calculation_but_blocks_material_commitments():
    run_id = uuid4()
    coverage = assess_coverage(run_id, ["a", "b", "c", "d"], ["a", "b"])
    decisions = decide_capabilities(run_id, coverage)

    assert coverage.quality_level == QualityLevel.PARTIAL
    assert by_cap(decisions, Capability.SUMMARIZE).decision_status == DecisionStatus.CONDITIONAL
    assert by_cap(decisions, Capability.CALCULATE).decision_status == DecisionStatus.CONDITIONAL
    assert by_cap(decisions, Capability.FORECAST).decision_status == DecisionStatus.BLOCKED
    assert by_cap(decisions, Capability.RECOMMEND_MATERIAL).decision_status == DecisionStatus.BLOCKED


def test_hedge_capability_requires_fixed_variable_currency_maturity_and_coverage():
    run_id = uuid4()
    coverage = assess_coverage(run_id, HEDGE_REQUIREMENTS, ["debt_currency", "debt_maturity"])
    issue = blocking_issue(
        "FIN-HEDGE-SCHEDULE-INCOMPLETE",
        "financial",
        "hedge_schedule_incomplete",
        (Capability.QUANTIFY_IMPACT, Capability.RECOMMEND_MATERIAL),
        remediation_key="provide_hedge_schedule",
        affected_refs=("debt_schedule",),
    )

    decisions = decide_capabilities(run_id, coverage, (issue,), (Capability.QUANTIFY_IMPACT, Capability.RECOMMEND_MATERIAL))

    assert all(d.decision_status == DecisionStatus.BLOCKED for d in decisions)
    assert all(d.requirements_to_unlock == ("provide_hedge_schedule",) for d in decisions)


def test_remediation_requests_are_localized_but_reason_codes_are_stable():
    translations = {lang: localized_remediation("provide_core_financials", lang) for lang in ("pt", "en", "es", "fr")}
    assert len(set(translations.values())) == 4

    run_id = uuid4()
    coverage = assess_coverage(run_id, ["validated_statement"], [])
    decision = by_cap(decide_capabilities(run_id, coverage), Capability.FORECAST)
    assert decision.reason_codes == ("f0_fragmentary",)



def test_scoped_blocking_issue_does_not_block_unrelated_capabilities():
    run_id = uuid4()
    calculate_inputs = REQUIREMENTS[Capability.CALCULATE]
    coverage = assess_coverage(run_id, calculate_inputs, calculate_inputs)
    issue = blocking_issue(
        "FIN-SCOPE-UNKNOWN",
        "financial",
        "scope_unknown",
        (Capability.SUMMARIZE,),
    )

    decisions = decide_capabilities(run_id, coverage, (issue,))

    assert by_cap(decisions, Capability.SUMMARIZE).decision_status == DecisionStatus.BLOCKED
    assert by_cap(decisions, Capability.CALCULATE).decision_status == DecisionStatus.ALLOWED
    assert by_cap(decisions, Capability.CALCULATE).supporting_issue_ids == ()


def test_unscoped_blocking_issue_remains_global():
    run_id = uuid4()
    coverage = assess_coverage(run_id, ["revenue"], ["revenue"])
    issue = blocking_issue(
        "FIN-GLOBAL-BLOCK",
        "financial",
        "global_block",
        (),
    )

    decisions = decide_capabilities(run_id, coverage, (issue,))

    assert all(decision.decision_status == DecisionStatus.BLOCKED for decision in decisions)
    assert all(decision.supporting_issue_ids == (issue.issue_id,) for decision in decisions)


def test_fresh_quality_issue_replay_uses_stable_scoped_identity():
    workspace_id, run_id = uuid4(), uuid4()
    first = blocking_issue(
        "FIN-SCOPE-UNKNOWN",
        "financial",
        "scope_unknown",
        (Capability.SUMMARIZE,),
        remediation_key="provide_scope",
        affected_refs=("statement",),
    )
    replay = blocking_issue(
        "FIN-SCOPE-UNKNOWN",
        "financial",
        "scope_unknown",
        (Capability.SUMMARIZE,),
        remediation_key="provide_scope",
        affected_refs=("statement",),
    )

    assert first.issue_id != replay.issue_id
    stable_id = _quality_issue_id(workspace_id, run_id, first)
    assert _quality_issue_id(workspace_id, run_id, replay) == stable_id
    assert _quality_issue_id(workspace_id, uuid4(), replay) != stable_id
    assert _quality_issue_id(uuid4(), run_id, replay) != stable_id


def test_complete_coverage_requires_every_required_input():
    run_id = uuid4()
    required = tuple(f"input_{index}" for index in range(20))
    coverage = assess_coverage(run_id, required, required[:-1])

    assert coverage.quality_level == QualityLevel.SUFFICIENT
    assert coverage.missing_inputs == ("input_19",)


def test_partial_coverage_blocks_causal_inference():
    run_id = uuid4()
    coverage = assess_coverage(
        run_id,
        ("reconciled_driver", "counterevidence_review"),
        ("reconciled_driver",),
    )

    decision = by_cap(
        decide_capabilities(run_id, coverage),
        Capability.INFER_CAUSE,
    )

    assert coverage.quality_level == QualityLevel.PARTIAL
    assert decision.decision_status == DecisionStatus.BLOCKED
    assert decision.reason_codes == ("partial_blocks_material_or_forecast",)
    assert decision.requirements_to_unlock == ("counterevidence_review",)



@pytest.mark.parametrize("capability", tuple(Capability))
def test_capability_matrix_blocks_each_missing_hard_prerequisite(capability):
    run_id = uuid4()
    requirement = requirement_for(capability)
    required = requirement.required_inputs
    coverage = assess_coverage(run_id, required, required[:-1])

    decision = by_cap(
        decide_capabilities(run_id, coverage, capabilities=(capability,)),
        capability,
    )

    if coverage.quality_level == QualityLevel.PARTIAL and capability in {
        Capability.SUMMARIZE,
        Capability.CALCULATE,
        Capability.COMPARE,
        Capability.DIAGNOSE,
        Capability.RECOMMEND_REVERSIBLE,
    }:
        assert decision.decision_status == DecisionStatus.CONDITIONAL
        assert required[-1] in decision.requirements_to_unlock
    else:
        assert decision.decision_status == DecisionStatus.BLOCKED
        assert decision.reason_codes in {
            ("capability_requirements_missing",),
            ("partial_blocks_material_or_forecast",),
        }
        assert required[-1] in decision.requirements_to_unlock


@pytest.mark.parametrize(
    ("capability", "missing_requirement"),
    (
        (Capability.FORECAST, "backtest"),
        (Capability.QUANTIFY_IMPACT, "sensitivity"),
    ),
)
def test_sufficient_coverage_does_not_override_specific_prerequisites(
    capability,
    missing_requirement,
):
    run_id = uuid4()
    required = REQUIREMENTS[capability]
    assert required[-1] == missing_requirement
    coverage = assess_coverage(run_id, required, required[:-1])

    decision = by_cap(
        decide_capabilities(run_id, coverage, capabilities=(capability,)),
        capability,
    )

    assert coverage.quality_level == QualityLevel.SUFFICIENT
    assert decision.decision_status == DecisionStatus.BLOCKED
    assert decision.reason_codes == ("capability_requirements_missing",)
    assert decision.requirements_to_unlock == (missing_requirement,)


@pytest.mark.parametrize("capability", tuple(Capability))
def test_complete_capability_requirements_allow_non_material_work(capability):
    run_id = uuid4()
    required = REQUIREMENTS[capability]
    coverage = assess_coverage(run_id, required, required)

    decision = by_cap(
        decide_capabilities(run_id, coverage, capabilities=(capability,)),
        capability,
    )

    assert coverage.quality_level == QualityLevel.COMPLETE
    assert decision.decision_status == DecisionStatus.ALLOWED
    assert decision.requirements_to_unlock == ()


@pytest.mark.parametrize("capability", tuple(Capability))
def test_global_blocking_issue_precedes_complete_capability_matrix(capability):
    run_id = uuid4()
    required = REQUIREMENTS[capability]
    coverage = assess_coverage(run_id, required, required)
    issue = blocking_issue(
        "FIN-GLOBAL-BLOCK",
        "financial",
        "global_block",
        (),
    )

    decision = by_cap(
        decide_capabilities(
            run_id,
            coverage,
            issues=(issue,),
            capabilities=(capability,),
        ),
        capability,
    )

    assert decision.decision_status == DecisionStatus.BLOCKED
    assert decision.reason_codes == ("blocking_issue_precedence",)
    assert decision.supporting_issue_ids == (issue.issue_id,)


@pytest.mark.parametrize(
    ("capability", "expected_missing"),
    (
        (
            Capability.FORECAST,
            ("adequate_time_series", "stable_scope", "method", "backtest"),
        ),
        (
            Capability.QUANTIFY_IMPACT,
            ("baseline", "driver", "cost", "sensitivity"),
        ),
        (
            Capability.RECOMMEND_MATERIAL,
            (
                "complete_coverage",
                "debt_schedule",
                "lease_schedule",
                "capex_schedule",
                "enhanced_audit",
            ),
        ),
    ),
)
def test_complete_narrow_coverage_cannot_bypass_capability_requirements(
    capability,
    expected_missing,
):
    run_id = uuid4()
    coverage = assess_coverage(
        run_id,
        ("validated_statement", "identified_period"),
        ("validated_statement", "identified_period"),
    )

    decision = by_cap(
        decide_capabilities(run_id, coverage, capabilities=(capability,)),
        capability,
    )

    assert coverage.quality_level == QualityLevel.COMPLETE
    assert decision.decision_status == DecisionStatus.BLOCKED
    assert decision.reason_codes == ("capability_requirements_missing",)
    assert decision.requirements_to_unlock == expected_missing


def test_quality_issue_identity_canonicalizes_blocking_capability_set():
    workspace_id, run_id = uuid4(), uuid4()
    first = blocking_issue(
        "FIN-HEDGE-SCHEDULE-INCOMPLETE",
        "financial",
        "hedge_schedule_incomplete",
        (Capability.QUANTIFY_IMPACT, Capability.RECOMMEND_MATERIAL),
        remediation_key="provide_hedge_schedule",
    )
    reordered_with_duplicate = blocking_issue(
        "FIN-HEDGE-SCHEDULE-INCOMPLETE",
        "financial",
        "hedge_schedule_incomplete",
        (
            Capability.RECOMMEND_MATERIAL,
            Capability.QUANTIFY_IMPACT,
            Capability.RECOMMEND_MATERIAL,
        ),
        remediation_key="provide_hedge_schedule",
    )

    assert _quality_issue_id(workspace_id, run_id, first) == _quality_issue_id(
        workspace_id,
        run_id,
        reordered_with_duplicate,
    )



def test_quality_issue_identity_canonicalizes_affected_reference_set():
    workspace_id, run_id = uuid4(), uuid4()
    first = blocking_issue(
        "FIN-AFFECTED-REFS",
        "financial",
        "affected_refs",
        (),
        affected_refs=("statement:2", "statement:1"),
    )
    reordered_with_duplicate = blocking_issue(
        "FIN-AFFECTED-REFS",
        "financial",
        "affected_refs",
        (),
        affected_refs=("statement:1", "statement:2", "statement:1"),
    )

    assert _quality_issue_id(workspace_id, run_id, first) == _quality_issue_id(
        workspace_id,
        run_id,
        reordered_with_duplicate,
    )


def test_capability_decisions_canonicalize_reordered_issue_evidence():
    run_id = uuid4()
    required = REQUIREMENTS[Capability.QUANTIFY_IMPACT]
    coverage = assess_coverage(run_id, required, required)
    first = blocking_issue(
        "FIN-HEDGE-SCHEDULE-INCOMPLETE",
        "financial",
        "hedge_schedule_incomplete",
        (Capability.QUANTIFY_IMPACT,),
        remediation_key="provide_hedge_schedule",
    )
    second = blocking_issue(
        "FIN-SENSITIVITY-MISSING",
        "financial",
        "sensitivity_missing",
        (Capability.QUANTIFY_IMPACT,),
        remediation_key="provide_sensitivity",
    )

    forward = by_cap(
        decide_capabilities(
            run_id,
            coverage,
            issues=(first, second, first),
            capabilities=(Capability.QUANTIFY_IMPACT,),
        ),
        Capability.QUANTIFY_IMPACT,
    )
    reverse = by_cap(
        decide_capabilities(
            run_id,
            coverage,
            issues=(second, first),
            capabilities=(Capability.QUANTIFY_IMPACT,),
        ),
        Capability.QUANTIFY_IMPACT,
    )

    assert forward.supporting_issue_ids == reverse.supporting_issue_ids
    assert forward.supporting_issue_ids == tuple(
        sorted({first.issue_id, second.issue_id}, key=str)
    )
    assert forward.requirements_to_unlock == reverse.requirements_to_unlock
    assert forward.requirements_to_unlock == (
        "provide_hedge_schedule",
        "provide_sensitivity",
    )
