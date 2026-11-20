"""Side-effect-free parsing of V2 feature flags."""

from collections.abc import Mapping
from dataclasses import dataclass


def _boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} must be 'true' or 'false'")
    return normalized == "true"


@dataclass(frozen=True)
class V2Settings:
    enabled: bool = False
    persist_stages: bool = False
    internal_workspaces: frozenset[str] = frozenset()
    strict_i18n: bool = True
    financial_analyst_enabled: bool = False
    financial_audit_enabled: bool = False
    financial_report_enabled: bool = False
    financial_shadow_enabled: bool = False
    financial_month_end_enabled: bool = False
    financial_product_enabled: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "V2Settings":
        internal = values.get("SALESLAB_V2_INTERNAL_WORKSPACES", "")
        return cls(
            enabled=_boolean(
                values.get("SALESLAB_V2_ENABLED", "false"), "SALESLAB_V2_ENABLED"
            ),
            persist_stages=_boolean(
                values.get("SALESLAB_V2_PERSIST_STAGES", "false"),
                "SALESLAB_V2_PERSIST_STAGES",
            ),
            internal_workspaces=frozenset(
                item.strip() for item in internal.split(",") if item.strip()
            ),
            strict_i18n=_boolean(
                values.get("SALESLAB_V2_STRICT_I18N", "true"),
                "SALESLAB_V2_STRICT_I18N",
            ),
            financial_analyst_enabled=_boolean(
                values.get("SALESLAB_V2_FINANCIAL_ANALYST_ENABLED", "false"),
                "SALESLAB_V2_FINANCIAL_ANALYST_ENABLED",
            ),
            financial_audit_enabled=_boolean(
                values.get("SALESLAB_V2_FINANCIAL_AUDIT_ENABLED", "false"),
                "SALESLAB_V2_FINANCIAL_AUDIT_ENABLED",
            ),
            financial_report_enabled=_boolean(
                values.get("SALESLAB_V2_FINANCIAL_REPORT_ENABLED", "false"),
                "SALESLAB_V2_FINANCIAL_REPORT_ENABLED",
            ),
            financial_shadow_enabled=_boolean(
                values.get("SALESLAB_V2_FINANCIAL_SHADOW_ENABLED", "false"),
                "SALESLAB_V2_FINANCIAL_SHADOW_ENABLED",
            ),
            financial_month_end_enabled=_boolean(
                values.get("SALESLAB_V2_FINANCIAL_MONTH_END_ENABLED", "false"),
                "SALESLAB_V2_FINANCIAL_MONTH_END_ENABLED",
            ),
            financial_product_enabled=_boolean(
                values.get("SALESLAB_V2_FINANCIAL_PRODUCT_ENABLED", "false"),
                "SALESLAB_V2_FINANCIAL_PRODUCT_ENABLED",
            ),
        )
