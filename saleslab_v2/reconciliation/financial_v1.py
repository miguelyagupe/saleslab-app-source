"""Rules aligned with the F-02 terminology and F-03 metric catalog v1."""

from dataclasses import replace

from .contracts import (
    Equation,
    ReconciliationRegistry,
    ReconciliationRule,
)
from .contracts import (
    OperandRequirement as O,
)
from .contracts import (
    TemporalRole as T,
)


def o(
    role,
    ids,
    variants=(None,),
    *,
    unit="monetary",
    temporal=T.SAME_PERIOD,
    statuses=("confirmed", "calculated")
):
    # Multiple IDs in this v1 tuple are explicit compatibility aliases for the
    # immutable F-02 terminology-v1 and F-03 financial metric catalog v1 only.
    return O(
        role=role,
        identities=tuple(ids),
        variants=tuple(variants),
        statuses=tuple(statuses),
        units=(unit,),
        currency_applicable=unit == "monetary",
        scale_applicable=unit == "monetary",
        temporal_role=temporal,
    )


def rule(rule_id, description, operands, equations, capability=None):
    return ReconciliationRule(
        rule_id,
        "1",
        description,
        tuple(operands),
        tuple(equations),
        capability,
        "1" if capability else None,
    )


FINANCIAL_RULES_V1 = ReconciliationRegistry(
    (
        rule(
            "REC-BS-001",
            "assets = liabilities + equity",
            (
                o(
                    "liabilities",
                    ("total_liabilities",),
                    statuses=("normalized", "confirmed"),
                ),
                o("equity", ("equity",), statuses=("normalized", "confirmed")),
                o("assets", ("total_assets",), statuses=("normalized", "confirmed")),
            ),
            (Equation((("liabilities", 1), ("equity", 1)), "assets", "balance_sheet"),),
        ),
        rule(
            "REC-CF-001",
            "cash movement bridge",
            (
                o(
                    "opening_cash",
                    ("cash_and_equivalents",),
                    statuses=("normalized", "confirmed", "calculated"),
                    temporal=T.STOCK_OPENING,
                ),
                o(
                    "cfo",
                    ("cfo",),
                    statuses=("normalized", "confirmed", "calculated"),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "cfi",
                    ("cfi",),
                    statuses=("normalized", "confirmed", "calculated"),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "cff",
                    ("cff",),
                    statuses=("normalized", "confirmed", "calculated"),
                    temporal=T.FLOW_INTERVAL,
                ),
                o("fx_other", ("cash_fx_other",), temporal=T.FLOW_INTERVAL),
                o(
                    "closing_cash",
                    ("cash_and_equivalents",),
                    statuses=("normalized", "confirmed", "calculated"),
                    temporal=T.STOCK_CLOSING,
                ),
            ),
            (
                Equation(
                    (
                        ("opening_cash", 1),
                        ("cfo", 1),
                        ("cfi", 1),
                        ("cff", 1),
                        ("fx_other", 1),
                    ),
                    "closing_cash",
                    "cash_roll_forward",
                ),
            ),
        ),
        rule(
            "REC-PL-001",
            "profit and loss bridges",
            (
                o(
                    "revenue",
                    ("revenue",),
                    statuses=("normalized", "confirmed", "calculated"),
                ),
                o(
                    "costs",
                    ("operating_costs",),
                    statuses=("normalized", "confirmed", "calculated"),
                ),
                o("other_operating", ("other_operating_income_expense",)),
                o("ebit", ("ebit_confirmed",), statuses=("confirmed", "calculated")),
                o("finance", ("net_finance_cost",)),
                o("tax", ("income_tax",)),
                o(
                    "net_income",
                    ("net_income",),
                    statuses=("normalized", "confirmed", "calculated"),
                ),
            ),
            (
                Equation(
                    (("revenue", 1), ("costs", -1), ("other_operating", 1)),
                    "ebit",
                    "operating_profit",
                ),
                Equation(
                    (("ebit", 1), ("finance", -1), ("tax", -1)),
                    "net_income",
                    "net_income",
                ),
            ),
        ),
        rule(
            "REC-EBITDA-001",
            "statutory and adjusted EBITDA bridges",
            (
                o("ebit", ("ebit_confirmed",), (None, "statutory")),
                o("da", ("depreciation_and_amortization",), (None, "statutory")),
                o("statutory_ebitda", ("ebitda_statutory",), ("statutory",)),
                o("adjustments", ("ebitda_adjustments",), ("adjustment_bridge",)),
                o("adjusted_ebitda", ("ebitda_adjusted",), ("adjusted",)),
            ),
            (
                Equation(
                    (("ebit", 1), ("da", 1)), "statutory_ebitda", "statutory_ebitda"
                ),
                Equation(
                    (("statutory_ebitda", 1), ("adjustments", 1)),
                    "adjusted_ebitda",
                    "adjusted_ebitda",
                ),
            ),
        ),
        rule(
            "REC-DEBT-001",
            "debt movement bridge",
            (
                o(
                    "opening",
                    ("gross_debt",),
                    ("excluding_leases",),
                    temporal=T.STOCK_OPENING,
                ),
                o(
                    "drawdowns",
                    ("debt_drawdowns",),
                    ("debt_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "repayments",
                    ("debt_repayments",),
                    ("debt_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "fx_other",
                    ("debt_fx_other",),
                    ("debt_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "closing",
                    ("gross_debt",),
                    ("excluding_leases",),
                    temporal=T.STOCK_CLOSING,
                ),
            ),
            (
                Equation(
                    (
                        ("opening", 1),
                        ("drawdowns", 1),
                        ("repayments", -1),
                        ("fx_other", 1),
                    ),
                    "closing",
                    "debt_roll_forward",
                ),
            ),
        ),
        rule(
            "REC-LEASE-001",
            "lease movement bridge",
            (
                o(
                    "opening",
                    ("lease_liabilities",),
                    ("lease_bridge",),
                    temporal=T.STOCK_OPENING,
                ),
                o(
                    "additions",
                    ("lease_additions",),
                    ("lease_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "modifications",
                    ("lease_modifications",),
                    ("lease_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "interest",
                    ("lease_interest",),
                    ("lease_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "payments",
                    ("lease_payments",),
                    ("lease_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "fx_other",
                    ("lease_fx_other",),
                    ("lease_bridge",),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "closing",
                    ("lease_liabilities",),
                    ("lease_bridge",),
                    temporal=T.STOCK_CLOSING,
                ),
            ),
            (
                Equation(
                    (
                        ("opening", 1),
                        ("additions", 1),
                        ("modifications", 1),
                        ("interest", 1),
                        ("payments", -1),
                        ("fx_other", 1),
                    ),
                    "closing",
                    "lease_roll_forward",
                ),
            ),
            "attribute_leasing_to_expansion",
        ),
        rule(
            "REC-GROWTH-001",
            "reported growth driver bridge",
            (
                o("lfl", ("growth_lfl_pct",), ("growth_driver",), unit="percentage"),
                o(
                    "expansion_acquisition",
                    ("growth_expansion_acquisition_pct",),
                    ("growth_driver",),
                    unit="percentage",
                ),
                o("fx", ("growth_fx_pct",), ("growth_driver",), unit="percentage"),
                o(
                    "other",
                    ("growth_other_pct",),
                    ("growth_driver",),
                    unit="percentage",
                ),
                o(
                    "reported",
                    ("revenue_growth_reported_pct",),
                    ("reported",),
                    unit="percentage",
                ),
            ),
            (
                Equation(
                    (("lfl", 1), ("expansion_acquisition", 1), ("fx", 1), ("other", 1)),
                    "reported",
                    "growth_bridge",
                ),
            ),
            "call_growth_organic",
        ),
    )
)

# Explicit v2 debt/lease rules accept canonical stock snapshots by adjacent/end
# date, including within-year fiscal quarters. The original v1 rules are unchanged.
FINANCIAL_RULES_V1 = ReconciliationRegistry(
    (
        *FINANCIAL_RULES_V1.rules,
        *(
            replace(
                rule,
                version="2",
                description=rule.description
                + "; date-anchored canonical stock snapshots",
            )
            for rule in FINANCIAL_RULES_V1.rules
            if rule.rule_id in {"REC-DEBT-001", "REC-LEASE-001"}
        ),
    )
)

# Raw bridge movements are not derived metrics.  This immutable v1 catalog is the
# sole documented exception to F-02/F-03 emitted identities.
RAW_BRIDGE_IDENTITIES_V1 = frozenset(
    {
        "cash_fx_other",
        "debt_drawdowns",
        "debt_fx_other",
        "debt_repayments",
        "growth_expansion_acquisition_pct",
        "growth_fx_pct",
        "growth_lfl_pct",
        "growth_other_pct",
        "income_tax",
        "lease_additions",
        "lease_fx_other",
        "lease_interest",
        "lease_modifications",
        "lease_payments",
        "net_finance_cost",
        "other_operating_income_expense",
    }
)

# Additive monetary bridge. V1 remains the frozen percentage-point contract.
FINANCIAL_RULES_V1 = ReconciliationRegistry(
    (
        *FINANCIAL_RULES_V1.rules,
        ReconciliationRule(
            "REC-GROWTH-001",
            "2",
            "Signed monetary contributions reconcile to comparable reported revenue change; no rate subtraction or inferred organic classification",
            tuple(
                o(role, (identity,))
                for role, identity in (
                    ("lfl", "growth_lfl_contribution"),
                    (
                        "expansion_acquisition",
                        "growth_expansion_acquisition_contribution",
                    ),
                    ("fx", "growth_fx_contribution"),
                    ("other", "growth_other_contribution"),
                    ("reported", "revenue_change_reported"),
                )
            ),
            (
                Equation(
                    (("lfl", 1), ("expansion_acquisition", 1), ("fx", 1), ("other", 1)),
                    "reported",
                    "monetary_revenue_growth_bridge",
                ),
            ),
            "call_growth_organic",
            "1",
        ),
    )
)

# Exhaustive source principal partitions; no inference of missing components.
FINANCIAL_RULES_V1 = ReconciliationRegistry(
    (
        *FINANCIAL_RULES_V1.rules,
        *(
            rule(
                rule_id,
                description,
                tuple(
                    o(c, (c,), statuses=("normalized", "confirmed"))
                    for c in (*components, "debt_eligible_principal")
                ),
                (
                    Equation(
                        tuple((c, 1) for c in components),
                        "debt_eligible_principal",
                        "debt_principal_partition",
                    ),
                ),
            )
            for rule_id, description, components in (
                (
                    "REC-DEBT-RATE-001",
                    "Contractual fixed plus variable principal equals eligible debt principal excluding leases, before hedging",
                    ("debt_fixed_principal", "debt_variable_principal"),
                ),
                (
                    "REC-DEBT-MATURITY-001",
                    "Principal due within twelve months plus principal due thereafter equals eligible principal excluding leases at the reporting date",
                    ("debt_due_within_12_months", "debt_due_after_12_months"),
                ),
            )
        ),
    )
)

FINANCIAL_RULES_V1 = ReconciliationRegistry(
    (
        *FINANCIAL_RULES_V1.rules,
        rule(
            "REC-CAPEX-001",
            "Confirmed maintenance plus growth cash purchases equal the source-defined cash capex total; commitments are excluded",
            tuple(
                o(c, (c,), statuses=("normalized", "confirmed"))
                for c in (
                    "capex_cash_maintenance",
                    "capex_cash_growth",
                    "capex_cash_total",
                )
            ),
            (
                Equation(
                    (("capex_cash_maintenance", 1), ("capex_cash_growth", 1)),
                    "capex_cash_total",
                    "cash_capex_partition",
                ),
            ),
        ),
        rule(
            "REC-CAPEX-002",
            "Confirmed positive cash capex total equals the magnitude of the independently reported signed fixed-asset purchase cash outflow",
            tuple(
                o(c, (c,), statuses=("normalized", "confirmed"))
                for c in ("capex", "capex_cash_total")
            ),
            (
                Equation(
                    (("capex", -1),),
                    "capex_cash_total",
                    "independent_cash_capex_tie_out",
                ),
            ),
        ),
    )
)


FINANCIAL_RULES_V1 = ReconciliationRegistry(
    (
        *FINANCIAL_RULES_V1.rules,
        *(
            rule(
                rule_id,
                description,
                (
                    o(
                        "assets",
                        ("current_operating_assets",),
                        statuses=("normalized", "confirmed"),
                    ),
                    o(
                        "liabilities",
                        ("current_operating_liabilities",),
                        statuses=("normalized", "confirmed"),
                    ),
                    o(
                        "reported",
                        ("reported_operating_nwc",),
                        statuses=("normalized", "confirmed"),
                    ),
                ),
                (
                    Equation(
                        (("assets", 1), ("liabilities", -1)),
                        "reported",
                        "operating_nwc_tie_out",
                    ),
                ),
            )
            for rule_id, description in (
                (
                    "REC-WC-CLOSING-001",
                    "Closing operating assets minus operating liabilities equals the independently reported operating NWC under one source-confirmed perimeter",
                ),
                (
                    "REC-WC-OPENING-001",
                    "Opening operating assets minus operating liabilities equals the independently reported opening operating NWC under the same source-confirmed perimeter",
                ),
            )
        ),
        rule(
            "REC-WC-CHANGE-001",
            "Opening operating NWC plus reported balance change equals closing operating NWC; this is not a cash flow bridge",
            (
                o(
                    "opening",
                    ("reported_operating_nwc",),
                    statuses=("normalized", "confirmed"),
                    temporal=T.STOCK_OPENING,
                ),
                o(
                    "change",
                    ("reported_operating_nwc_change",),
                    statuses=("normalized", "confirmed"),
                    temporal=T.FLOW_INTERVAL,
                ),
                o(
                    "closing",
                    ("reported_operating_nwc",),
                    statuses=("normalized", "confirmed"),
                    temporal=T.STOCK_CLOSING,
                ),
            ),
            (
                Equation(
                    (("opening", 1), ("change", 1)),
                    "closing",
                    "reported_operating_nwc_roll_forward",
                ),
            ),
        ),
    )
)
