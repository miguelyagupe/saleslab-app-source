"""Contract properties independent of individual automated review findings."""

from dataclasses import replace
from itertools import permutations, product
from types import MappingProxyType
from uuid import uuid4

import pytest

from saleslab_v2.domain.enums import Capability, ModuleType, QualityLevel
from saleslab_v2.quality.capabilities import decide_capabilities
from saleslab_v2.quality.coverage import assess_coverage, validate_coverage
from saleslab_v2.quality.issues import blocking_issue
from saleslab_v2.quality.policies.financial_v1 import REQUIREMENTS


def semantic_payload(coverage):
    result = coverage.to_dict()
    for name in ("assessment_id", "assessed_at"):
        result.pop(name)
    return result


def decision_payload(decisions):
    return tuple(
        (d.capability, d.decision_status, d.reason_codes, d.requirements_to_unlock)
        for d in decisions
    )


def test_all_input_permutations_and_duplicates_are_semantically_identical():
    run_id = uuid4()
    required = ("a", "b", "c", "d")
    expected = assess_coverage(run_id, required, ("b", "c", "d"), ("c", "d"))
    for order in permutations(required):
        for present in permutations(("b", "c", "d")):
            for ambiguous in permutations(("c", "d")):
                coverage = assess_coverage(
                    run_id, order + order, present + present, ambiguous + ambiguous
                )
                assert semantic_payload(coverage) == semantic_payload(expected)
                assert decision_payload(decide_capabilities(run_id, coverage)) == (
                    decision_payload(decide_capabilities(run_id, expected))
                )


def test_coverage_partition_and_score_invariants_for_every_input_state():
    required = ("a", "b", "c", "d")
    # absent / present / ambiguous / present-and-ambiguous for each input.
    for states in product(range(4), repeat=len(required)):
        present = tuple(x for x, state in zip(required, states) if state in (1, 3))
        ambiguous = tuple(x for x, state in zip(required, states) if state in (2, 3))
        coverage = assess_coverage(uuid4(), required, present, ambiguous)
        assert set(coverage.present_inputs).isdisjoint(coverage.missing_inputs)
        assert set(coverage.present_inputs).isdisjoint(coverage.ambiguous_inputs)
        assert set(coverage.present_inputs) | set(coverage.missing_inputs) == set(
            required
        )
        assert set(coverage.ambiguous_inputs) <= set(coverage.missing_inputs)
        assert coverage.score_components["present"] == len(coverage.present_inputs)
        assert coverage.score_components["missing"] == len(coverage.missing_inputs)
        assert (coverage.quality_level == QualityLevel.COMPLETE) == (
            not coverage.missing_inputs and not coverage.ambiguous_inputs
        )
        assert semantic_payload(validate_coverage(coverage)) == semantic_payload(
            coverage
        )


@pytest.mark.parametrize("capability", tuple(Capability))
@pytest.mark.parametrize(
    "changes",
    (
        {"quality_level": QualityLevel.COMPLETE},
        {"missing_inputs": ()},
        {"present_inputs": ("a", "b", "c")},
        {"ambiguous_inputs": ("outside_profile",)},
        {"score_components": MappingProxyType({"coverage_ratio": "1.0"})},
        {"required_inputs": ("a", "b")},
    ),
)
def test_every_capability_rejects_contradictory_coverage(capability, changes):
    coverage = assess_coverage(uuid4(), ("a", "b", "c"), ("a",))
    forged = replace(coverage, **changes)
    with pytest.raises(ValueError, match="inconsistent coverage"):
        decide_capabilities(coverage.run_id, forged, capabilities=(capability,))


def test_present_prerequisites_cannot_hide_stale_missing_and_score_fields():
    coverage = assess_coverage(uuid4(), ("a", "b", "c"), ("a",))
    prerequisites = tuple(
        sorted({x for values in REQUIREMENTS.values() for x in values})
    )
    forged = replace(
        coverage, present_inputs=prerequisites, quality_level=QualityLevel.COMPLETE
    )
    with pytest.raises(ValueError, match="inconsistent coverage"):
        decide_capabilities(coverage.run_id, forged)


def test_issue_iterators_have_the_same_blocking_semantics_as_tuples():
    prerequisites = tuple(
        sorted({x for values in REQUIREMENTS.values() for x in values})
    )
    coverage = assess_coverage(uuid4(), prerequisites, prerequisites)
    issue = blocking_issue("FIN-GLOBAL", "financial", "global", ())
    assert decision_payload(
        decide_capabilities(coverage.run_id, coverage, iter((issue,)))
    ) == (decision_payload(decide_capabilities(coverage.run_id, coverage, (issue,))))


@pytest.mark.parametrize(
    "score_key", ("required", "present", "missing", "ambiguous", "coverage_ratio")
)
def test_each_score_component_is_recomputed(score_key):
    coverage = assess_coverage(uuid4(), ("a", "b"), ("a",))
    scores = dict(coverage.score_components)
    scores[score_key] = "999" if score_key == "coverage_ratio" else 999
    with pytest.raises(ValueError, match="inconsistent coverage score"):
        validate_coverage(replace(coverage, score_components=MappingProxyType(scores)))


def test_validator_canonicalizes_transport_collections_without_changing_identity():
    coverage = assess_coverage(uuid4(), ("a", "b", "c"), ("a",), ("b", "c"))
    transport = replace(
        coverage,
        required_inputs=("c", "b", "a", "a"),
        present_inputs=("a", "a"),
        missing_inputs=("c", "b", "b"),
        ambiguous_inputs=("c", "b", "c"),
    )
    canonical = validate_coverage(transport)
    assert canonical == coverage
    assert canonical.assessed_at == coverage.assessed_at
    assert canonical.assessment_id == coverage.assessment_id


def test_ambiguous_inputs_cannot_be_counted_as_present():
    coverage = assess_coverage(uuid4(), ("a", "b"), ("a", "b"), ("b",))
    with pytest.raises(ValueError, match="inconsistent coverage"):
        validate_coverage(replace(coverage, present_inputs=("a", "b")))


@pytest.mark.parametrize(
    "module", (ModuleType.SALES, ModuleType.COST, ModuleType.EFFICIENCY)
)
def test_financial_authorization_rejects_unsupported_modules(module):
    coverage = assess_coverage(uuid4(), ("a",), ("a",), module=module)
    with pytest.raises(ValueError, match="policy is not supported"):
        decide_capabilities(coverage.run_id, coverage)


def test_authorization_rejects_run_and_policy_version_mismatches():
    coverage = assess_coverage(uuid4(), ("a",), ("a",))
    with pytest.raises(ValueError, match="coverage run mismatch"):
        decide_capabilities(uuid4(), coverage)
    with pytest.raises(ValueError, match="policy is not supported"):
        decide_capabilities(
            coverage.run_id, replace(coverage, assessment_rule_version="unknown-v99")
        )


@pytest.mark.parametrize("values", ("abc", ("",), (None,), (1,)))
def test_inputs_require_nonempty_identifiers(values):
    with pytest.raises(ValueError, match="nonempty strings"):
        assess_coverage(uuid4(), values)
