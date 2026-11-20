from types import MappingProxyType

from saleslab_v2.domain.enums import Capability

RULE_VERSION = "financial-quality-v1"
HEDGE_RULE_VERSION = "financial-quality-hedge-v1"
DEFAULT_PROFILE = "financial_v1"
HEDGE_PROFILE = "financial_hedge_v1"
PROFILE_RULE_VERSIONS = MappingProxyType(
    {
        DEFAULT_PROFILE: RULE_VERSION,
        HEDGE_PROFILE: HEDGE_RULE_VERSION,
    }
)
HEDGE_CAPABILITIES = frozenset(
    {Capability.QUANTIFY_IMPACT, Capability.RECOMMEND_MATERIAL}
)
F0_BLOCKED = set(Capability)
PARTIAL_BLOCKED = {
    Capability.INFER_CAUSE,
    Capability.FORECAST,
    Capability.QUANTIFY_IMPACT,
    Capability.RECOMMEND_MATERIAL,
}
PARTIAL_CONDITIONAL = {
    Capability.SUMMARIZE,
    Capability.CALCULATE,
    Capability.COMPARE,
    Capability.DIAGNOSE,
    Capability.RECOMMEND_REVERSIBLE,
}
REQUIREMENTS = {
    Capability.SUMMARIZE: (
        "validated_statement",
        "identified_period",
        "currency",
        "scale",
        "scope",
    ),
    Capability.CALCULATE: (
        "metric_definition",
        "mapped_inputs",
        "currency",
        "scale",
        "non_zero_denominator",
    ),
    Capability.COMPARE: (
        "two_comparable_periods",
        "consistent_granularity",
        "consistent_scope",
    ),
    Capability.DIAGNOSE: ("comparison", "driver_bridge", "relevant_components"),
    Capability.INFER_CAUSE: ("reconciled_driver", "counterevidence_review"),
    Capability.FORECAST: ("adequate_time_series", "stable_scope", "method", "backtest"),
    Capability.QUANTIFY_IMPACT: ("baseline", "driver", "cost", "sensitivity"),
    Capability.RECOMMEND_REVERSIBLE: ("audited_finding", "risk", "owner"),
    Capability.RECOMMEND_MATERIAL: (
        "complete_coverage",
        "debt_schedule",
        "lease_schedule",
        "capex_schedule",
        "enhanced_audit",
    ),
}
HEDGE_REQUIREMENTS = (
    "debt_fixed_variable_mix",
    "debt_currency",
    "debt_maturity",
    "hedging_coverage",
)
REMEDIATIONS = {
    "provide_core_financials": {
        "pt": "Forneça demonstrações financeiras validadas com período, moeda, escala e âmbito.",
        "en": "Provide validated financial statements with period, currency, scale and scope.",
        "es": "Proporcione estados financieros validados con período, moneda, escala y alcance.",
        "fr": "Fournissez des états financiers validés avec période, devise, échelle et périmètre.",
    },
    "provide_hedge_schedule": {
        "pt": "Forneça dívida fixa/variável, moeda, maturidade e cobertura de hedging.",
        "en": "Provide fixed/variable debt mix, currency, maturity and hedging coverage.",
        "es": "Proporcione deuda fija/variable, moneda, vencimiento y cobertura de hedging.",
        "fr": "Fournissez dette fixe/variable, devise, échéance et couverture de hedging.",
    },
}
