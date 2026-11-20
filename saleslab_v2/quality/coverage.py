"""Canonical coverage calculation and validation of untrusted assessments."""

from dataclasses import replace
from types import MappingProxyType
from uuid import UUID

from saleslab_v2.domain.enums import ModuleType, QualityLevel
from saleslab_v2.domain.quality import CoverageAssessment

from .policies.financial_v1 import DEFAULT_PROFILE
from .requirements import profile_for_rule_version, rule_version_for


def _input_set(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("coverage inputs must be collections of nonempty strings")
    values = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("coverage inputs must be collections of nonempty strings")
    return tuple(sorted(set(values)))


def assess_coverage(
    run_id: UUID,
    required_inputs,
    present_inputs=(),
    ambiguous_inputs=(),
    *,
    module=ModuleType.FINANCIAL,
    profile=DEFAULT_PROFILE,
):
    rule_version = rule_version_for(profile)
    required = _input_set(required_inputs)
    required_set = set(required)
    ambiguous = tuple(x for x in _input_set(ambiguous_inputs) if x in required_set)
    covered = tuple(
        x
        for x in _input_set(present_inputs)
        if x in required_set and x not in ambiguous
    )
    missing = tuple(x for x in required if x not in covered)
    ratio = len(covered) / len(required) if required else 1
    if not missing and not ambiguous:
        level = QualityLevel.COMPLETE
    elif ratio >= 0.75:
        level = QualityLevel.SUFFICIENT
    elif ratio > 0:
        level = QualityLevel.PARTIAL
    else:
        level = QualityLevel.INSUFFICIENT
    return CoverageAssessment(
        run_id,
        module,
        required,
        covered,
        missing,
        ambiguous,
        level,
        MappingProxyType(
            {
                "required": len(required),
                "present": len(covered),
                "missing": len(missing),
                "ambiguous": len(ambiguous),
                "coverage_ratio": str(ratio),
            }
        ),
        rule_version,
    )


def validate_coverage(coverage: CoverageAssessment) -> CoverageAssessment:
    """Rebuild derived fields before authorization or storage.

    Frozen dataclasses are caller-controlled transport objects. Only ordering
    and multiplicity are ignored; contradictory derived fields, out-of-profile
    inputs and ambiguous inputs counted as present are rejected.
    """
    if coverage.module != ModuleType.FINANCIAL:
        raise ValueError(
            "quality gate policy is not supported for this module or rule version"
        )
    profile = profile_for_rule_version(coverage.assessment_rule_version)
    rebuilt = assess_coverage(
        coverage.run_id,
        coverage.required_inputs,
        coverage.present_inputs,
        coverage.ambiguous_inputs,
        module=coverage.module,
        profile=profile,
    )
    fields = ("required_inputs", "present_inputs", "missing_inputs", "ambiguous_inputs")
    if (
        any(
            _input_set(getattr(coverage, name)) != getattr(rebuilt, name)
            for name in fields
        )
        or coverage.quality_level != rebuilt.quality_level
    ):
        raise ValueError("inconsistent coverage assessment")
    scores = dict(coverage.score_components)
    expected = dict(rebuilt.score_components)
    if scores != expected or any(
        type(scores[key]) is not type(value) for key, value in expected.items()
    ):
        raise ValueError("inconsistent coverage score components")
    return replace(
        rebuilt,
        assessment_id=coverage.assessment_id,
        assessed_at=coverage.assessed_at,
    )
