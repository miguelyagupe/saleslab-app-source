from saleslab_v2.domain.enums import Capability
from saleslab_v2.domain.quality import RequiredInputSet
from .policies.financial_v1 import (
    DEFAULT_PROFILE,
    HEDGE_CAPABILITIES,
    HEDGE_PROFILE,
    HEDGE_REQUIREMENTS,
    PROFILE_RULE_VERSIONS,
    REQUIREMENTS,
)


def rule_version_for(profile):
    try:
        return PROFILE_RULE_VERSIONS[profile]
    except KeyError as exc:
        raise ValueError("unsupported financial profile") from exc


def profile_for_rule_version(rule_version):
    for profile, version in PROFILE_RULE_VERSIONS.items():
        if version == rule_version:
            return profile
    raise ValueError(
        "quality gate policy is not supported for this module or rule version"
    )


def requirement_for(
    capability: Capability, profile: str = DEFAULT_PROFILE
) -> RequiredInputSet:
    rule_version = rule_version_for(profile)
    required = REQUIREMENTS[capability]
    if profile == HEDGE_PROFILE and capability in HEDGE_CAPABILITIES:
        required = tuple(dict.fromkeys((*required, *HEDGE_REQUIREMENTS)))
    return RequiredInputSet(profile, capability, required, rule_version=rule_version)
