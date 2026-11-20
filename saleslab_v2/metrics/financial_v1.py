"""Financial catalog v1; variants are intentionally separate identities."""

from .definitions import MetricDefinition, MetricRegistry, OperandContract


def c(
    role,
    unit="monetary",
    *,
    ids=None,
    variants=(None,),
    dimensions=(),
    value_constraint="any"
):
    return OperandContract(
        role,
        tuple(ids or (role,)),
        unit,
        ("confirmed", "calculated"),
        tuple(variants),
        tuple(dimensions),
        value_constraint,
    )


def d(
    metric_id,
    formula,
    inputs,
    operation,
    unit="monetary",
    rounding=None,
    semantics="",
    contracts=None,
    version="1",
):
    return MetricDefinition(
        metric_id,
        version,
        semantics or metric_id,
        formula,
        tuple(inputs),
        operation,
        unit,
        rounding_policy=rounding,
        operand_contracts=tuple(contracts or (c(role) for role in inputs)),
    )


FINANCIAL_V1 = MetricRegistry(
    (
        d(
            "roic",
            "nopat/average_invested_capital*100",
            ["nopat_with_explicit_tax_assumptions", "average_invested_capital"],
            "percent",
            "percentage",
            "percentage_2dp",
            semantics="Period return on explicit positive average invested capital using reported NOPAT and user-confirmed source tax assumptions; not annualized and no inferred tax rate.",
            contracts=(
                c("nopat_with_explicit_tax_assumptions"),
                c("average_invested_capital", value_constraint="positive"),
            ),
            version="2",
        ),
        d(
            "revenue_growth_cc_pct",
            "(constant_currency_current-constant_currency_prior)/abs(constant_currency_prior)*100",
            ["constant_currency_current", "constant_currency_prior"],
            "growth",
            "percentage",
            "percentage_2dp",
            semantics="Calculated constant-currency revenue growth from two explicitly defined monetary bases; never inferred FX or organic growth.",
            contracts=(
                c("constant_currency_current", ids=("revenue_constant_currency",)),
                c("constant_currency_prior", ids=("revenue_constant_currency",)),
            ),
        ),
        d(
            "revenue_growth_lfl_pct",
            "(like_for_like_current-like_for_like_prior)/abs(like_for_like_prior)*100",
            ["like_for_like_current", "like_for_like_prior"],
            "growth",
            "percentage",
            "percentage_2dp",
            semantics="Calculated like-for-like growth from a confirmed comparable perimeter; not an organic-growth classification.",
            contracts=(
                c("like_for_like_current", ids=("revenue_like_for_like",)),
                c("like_for_like_prior", ids=("revenue_like_for_like",)),
            ),
        ),
        d(
            "revenue_change_reported",
            "revenue_current-revenue_prior",
            ["revenue_current", "revenue_prior"],
            "growth_delta",
            semantics="Exact monetary change between structurally comparable reported revenue periods.",
            contracts=(c("revenue_current"), c("revenue_prior")),
        ),
        *(
            d(
                key,
                "reported_" + key,
                ["reported_" + key],
                "identity",
                semantics="Signed monetary contribution to the reported revenue change under one confirmed source bridge definition.",
                contracts=(c("reported_" + key, ids=(key,)),),
            )
            for key in (
                "growth_lfl_contribution",
                "growth_expansion_acquisition_contribution",
                "growth_fx_contribution",
                "growth_other_contribution",
            )
        ),
        d(
            "cash_and_cash_equivalents",
            "reported_cash_and_cash_equivalents",
            ["reported_cash_and_cash_equivalents"],
            "identity",
            semantics="Explicit source eligible_unrestricted_cash; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_cash_and_cash_equivalents",
                    ids=("eligible_unrestricted_cash",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "current_operating_assets",
            "reported_current_operating_assets",
            ["reported_current_operating_assets"],
            "identity",
            semantics="Explicit source current_operating_assets; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_current_operating_assets",
                    ids=("current_operating_assets",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "current_operating_liabilities",
            "reported_current_operating_liabilities",
            ["reported_current_operating_liabilities"],
            "identity",
            semantics="Explicit source current_operating_liabilities; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_current_operating_liabilities",
                    ids=("current_operating_liabilities",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "average_trade_receivables",
            "reported_average_trade_receivables",
            ["reported_average_trade_receivables"],
            "identity",
            semantics="Explicit source average_trade_receivables; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_average_trade_receivables",
                    ids=("average_trade_receivables",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "average_inventory",
            "reported_average_inventory",
            ["reported_average_inventory"],
            "identity",
            semantics="Explicit source average_inventory; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_average_inventory",
                    ids=("average_inventory",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "average_trade_payables",
            "reported_average_trade_payables",
            ["reported_average_trade_payables"],
            "identity",
            semantics="Explicit source average_trade_payables; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_average_trade_payables",
                    ids=("average_trade_payables",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "purchases",
            "reported_purchases",
            ["reported_purchases"],
            "identity",
            semantics="Explicit source purchases; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_purchases",
                    ids=("purchases",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "interest_expense",
            "reported_interest_expense",
            ["reported_interest_expense"],
            "identity",
            semantics="Explicit source interest_expense; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_interest_expense",
                    ids=("interest_expense",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "lease_liabilities",
            "reported_lease_liabilities",
            ["reported_lease_liabilities"],
            "identity",
            semantics="Explicit source lease_liabilities; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_lease_liabilities",
                    ids=("lease_liabilities",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "debt_drawdowns",
            "reported_debt_drawdowns",
            ["reported_debt_drawdowns"],
            "identity",
            semantics="Explicit source debt_drawdowns; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_debt_drawdowns",
                    ids=("debt_drawdowns",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "debt_repayments",
            "reported_debt_repayments",
            ["reported_debt_repayments"],
            "identity",
            semantics="Explicit source debt_repayments; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_debt_repayments",
                    ids=("debt_repayments",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "debt_fx_other",
            "reported_debt_fx_other",
            ["reported_debt_fx_other"],
            "identity",
            semantics="Explicit source debt_fx_other; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_debt_fx_other",
                    ids=("debt_fx_other",),
                    value_constraint="any",
                ),
            ),
        ),
        d(
            "lease_additions",
            "reported_lease_additions",
            ["reported_lease_additions"],
            "identity",
            semantics="Explicit source lease_additions; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_lease_additions",
                    ids=("lease_additions",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "lease_modifications",
            "reported_lease_modifications",
            ["reported_lease_modifications"],
            "identity",
            semantics="Explicit source lease_modifications; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_lease_modifications",
                    ids=("lease_modifications",),
                    value_constraint="any",
                ),
            ),
        ),
        d(
            "lease_interest",
            "reported_lease_interest",
            ["reported_lease_interest"],
            "identity",
            semantics="Explicit source lease_interest; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_lease_interest",
                    ids=("lease_interest",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "lease_payments",
            "reported_lease_payments",
            ["reported_lease_payments"],
            "identity",
            semantics="Explicit source lease_payments; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_lease_payments",
                    ids=("lease_payments",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "lease_fx_other",
            "reported_lease_fx_other",
            ["reported_lease_fx_other"],
            "identity",
            semantics="Explicit source lease_fx_other; no inferred components or sign conversion.",
            contracts=(
                c(
                    "reported_lease_fx_other",
                    ids=("lease_fx_other",),
                    value_constraint="any",
                ),
            ),
        ),
        d(
            "gross_debt",
            "debt_excluding_leases",
            ["debt_excluding_leases"],
            "identity",
            version="2",
            semantics="Explicit nonnegative gross debt excluding leases; required classification for upload debt bridge.",
            contracts=(c("debt_excluding_leases", value_constraint="non_negative"),),
        ),
        d(
            "quick_assets",
            "eligible_cash+eligible_trade_receivables+other_permitted_liquid_assets",
            [
                "eligible_cash",
                "eligible_trade_receivables",
                "other_permitted_liquid_assets",
            ],
            "sum",
            contracts=(
                c(
                    "eligible_cash",
                    ids=("cash_and_cash_equivalents",),
                    value_constraint="non_negative",
                ),
                c("eligible_trade_receivables", value_constraint="non_negative"),
                c("other_permitted_liquid_assets", value_constraint="non_negative"),
            ),
        ),
        d(
            "days_in_period",
            "inclusive_calendar_days(period_anchor.start_date,period_anchor.end_date)",
            ["period_anchor"],
            "calendar_days",
            "days",
            semantics="Inclusive actual calendar day count from the traced source period; never a 30/360 assumption.",
            contracts=(
                c("period_anchor", ids=("revenue", "cost_of_sales", "purchases")),
            ),
        ),
        d(
            "current_ratio",
            "current_assets/current_liabilities",
            ["current_assets", "current_liabilities"],
            "ratio",
            "ratio",
            "ratio_2dp",
            version="2",
            contracts=(
                c("current_assets", value_constraint="non_negative"),
                c("current_liabilities", value_constraint="positive"),
            ),
        ),
        d(
            "quick_ratio",
            "quick_assets/current_liabilities",
            ["quick_assets", "current_liabilities"],
            "ratio",
            "ratio",
            "ratio_2dp",
            version="2",
            contracts=(
                c("quick_assets", value_constraint="non_negative"),
                c("current_liabilities", value_constraint="positive"),
            ),
        ),
        d(
            "dso",
            "average_trade_receivables/revenue*days_in_period",
            ["average_trade_receivables", "revenue", "days_in_period"],
            "days",
            "days",
            "days_1dp",
            version="2",
            contracts=(
                c("average_trade_receivables", value_constraint="non_negative"),
                c("revenue", value_constraint="positive"),
                c("days_in_period", "days", value_constraint="positive"),
            ),
        ),
        d(
            "dio",
            "average_inventory/cost_of_sales*days_in_period",
            ["average_inventory", "cost_of_sales", "days_in_period"],
            "days",
            "days",
            "days_1dp",
            version="2",
            contracts=(
                c("average_inventory", value_constraint="non_negative"),
                c("cost_of_sales", value_constraint="positive"),
                c("days_in_period", "days", value_constraint="positive"),
            ),
        ),
        d(
            "dpo",
            "average_trade_payables/purchases*days_in_period",
            ["average_trade_payables", "purchases", "days_in_period"],
            "days",
            "days",
            "days_1dp",
            version="2",
            contracts=(
                c("average_trade_payables", value_constraint="non_negative"),
                c("purchases", value_constraint="positive"),
                c("days_in_period", "days", value_constraint="positive"),
            ),
        ),
        d(
            "net_debt_to_ebitda",
            "net_debt_variant/ebitda_for_leverage",
            ["net_debt_variant", "ebitda_for_leverage"],
            "ratio",
            "ratio",
            "ratio_2dp",
            version="2",
            semantics="Net debt over positive EBITDA of the same explicit period; no annualization or risk benchmark.",
            contracts=(
                c(
                    "net_debt_variant",
                    ids=("net_debt", "net_debt_incl_leases"),
                    variants=("excluding_leases", "including_leases"),
                ),
                c(
                    "ebitda_for_leverage",
                    ids=("ebitda_statutory", "ebitda_adjusted"),
                    variants=("statutory", "adjusted"),
                    value_constraint="positive",
                ),
            ),
        ),
        d(
            "cost_of_sales",
            "reported_cost_of_sales",
            ["reported_cost_of_sales"],
            "identity",
            semantics="Explicit reported cost_of_sales; source sign and scope are preserved.",
            contracts=(
                c(
                    "reported_cost_of_sales",
                    ids=("cost_of_sales",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "operating_costs",
            "reported_operating_costs",
            ["reported_operating_costs"],
            "identity",
            semantics="Explicit reported operating_costs; source sign and scope are preserved.",
            contracts=(
                c(
                    "reported_operating_costs",
                    ids=("operating_costs",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "ebit_confirmed",
            "reported_ebit_confirmed",
            ["reported_ebit_confirmed"],
            "identity",
            semantics="Explicit reported ebit_confirmed; source sign and scope are preserved.",
            contracts=(c("reported_ebit_confirmed", ids=("ebit",)),),
        ),
        d(
            "depreciation_and_amortization",
            "reported_depreciation_and_amortization",
            ["reported_depreciation_and_amortization"],
            "identity",
            semantics="Explicit reported depreciation_and_amortization; source sign and scope are preserved.",
            contracts=(
                c(
                    "reported_depreciation_and_amortization",
                    ids=("depreciation_and_amortization",),
                    value_constraint="non_negative",
                ),
            ),
        ),
        d(
            "ebitda_adjustments",
            "reported_ebitda_adjustments",
            ["reported_ebitda_adjustments"],
            "identity",
            semantics="Explicit reported ebitda_adjustments; source sign and scope are preserved.",
            contracts=(
                c(
                    "reported_ebitda_adjustments",
                    ids=("ebitda_adjustments",),
                    variants=("adjustment_bridge",),
                ),
            ),
        ),
        d(
            "other_operating_income_expense",
            "reported_other_operating_income_expense",
            ["reported_other_operating_income_expense"],
            "identity",
            semantics="Explicit reported other_operating_income_expense; source sign and scope are preserved.",
            contracts=(
                c(
                    "reported_other_operating_income_expense",
                    ids=("other_operating_income_expense",),
                ),
            ),
        ),
        d(
            "net_finance_cost",
            "reported_net_finance_cost",
            ["reported_net_finance_cost"],
            "identity",
            semantics="Explicit reported net_finance_cost; source sign and scope are preserved.",
            contracts=(c("reported_net_finance_cost", ids=("net_finance_cost",)),),
        ),
        d(
            "income_tax",
            "reported_income_tax",
            ["reported_income_tax"],
            "identity",
            semantics="Explicit reported income_tax; source sign and scope are preserved.",
            contracts=(c("reported_income_tax", ids=("income_tax",)),),
        ),
        d(
            "ebitda_statutory",
            "reported_statutory_ebitda",
            ["reported_statutory_ebitda"],
            "identity",
            version="2",
            semantics="Explicit independently reported ebitda_statutory; not the calculated bridge output. Source definition must be confirmed by the selected concept.",
            contracts=(
                c("reported_statutory_ebitda", ids=("reported_statutory_ebitda",)),
            ),
        ),
        d(
            "ebitda_adjusted",
            "reported_adjusted_ebitda",
            ["reported_adjusted_ebitda"],
            "identity",
            version="2",
            semantics="Explicit independently reported ebitda_adjusted; not the calculated bridge output. Source definition must be confirmed by the selected concept.",
            contracts=(
                c("reported_adjusted_ebitda", ids=("reported_adjusted_ebitda",)),
            ),
        ),
        d("revenue", "reported_revenue", ["reported_revenue"], "identity"),
        d(
            "revenue_growth_reported_pct",
            "(revenue_current-revenue_prior)/abs(revenue_prior)*100",
            ["revenue_current", "revenue_prior"],
            "growth",
            "percentage",
            "percentage_2dp",
            contracts=(c("revenue_current"), c("revenue_prior")),
        ),
        d(
            "gross_profit",
            "revenue-cost_of_sales",
            ["revenue", "cost_of_sales"],
            "subtract",
        ),
        d(
            "gross_margin_pct",
            "gross_profit/revenue*100",
            ["gross_profit", "revenue"],
            "percent",
            "percentage",
            "percentage_2dp",
        ),
        d(
            "ebitda_statutory",
            "confirmed_ebit+compatible_depreciation_and_amortization",
            ["confirmed_ebit", "depreciation_and_amortization"],
            "add",
            semantics="Statutory EBITDA; never inferred from reported/unverified EBITDA or unconfirmed operating profit",
            contracts=(
                c("confirmed_ebit", ids=("ebit_confirmed",)),
                c(
                    "depreciation_and_amortization",
                    ids=("depreciation_and_amortization",),
                ),
            ),
        ),
        d(
            "ebitda_adjusted",
            "ebitda_statutory+explicit_ebitda_adjustments",
            ["ebitda_statutory", "explicit_ebitda_adjustments"],
            "add",
            semantics="Adjusted EBITDA derived from statutory EBITDA and an explicit adjustment bridge",
            contracts=(
                c(
                    "ebitda_statutory",
                    ids=("ebitda_statutory",),
                    variants=("statutory",),
                ),
                c(
                    "explicit_ebitda_adjustments",
                    ids=("ebitda_adjustments",),
                    variants=("adjustment_bridge",),
                ),
            ),
        ),
        d(
            "ebitda_margin_pct",
            "ebitda_for_margin/revenue*100",
            ["ebitda_for_margin", "revenue"],
            "percent",
            "percentage",
            "percentage_2dp",
            contracts=(
                c(
                    "ebitda_for_margin",
                    ids=("ebitda_statutory", "ebitda_adjusted"),
                    variants=("statutory", "adjusted"),
                ),
                c("revenue"),
            ),
        ),
        d(
            "ebit_margin_pct",
            "confirmed_ebit/revenue*100",
            ["confirmed_ebit", "revenue"],
            "percent",
            "percentage",
            "percentage_2dp",
            contracts=(c("confirmed_ebit", ids=("ebit_confirmed",)), c("revenue")),
        ),
        d(
            "net_margin_pct",
            "net_income/revenue*100",
            ["net_income", "revenue"],
            "percent",
            "percentage",
            "percentage_2dp",
            contracts=(c("net_income"), c("revenue")),
        ),
        d("cfo", "reported_cfo", ["reported_cfo"], "identity"),
        d(
            "cfi",
            "reported_cfi",
            ["reported_cfi"],
            "identity",
            semantics="Reported signed investing cash flow; not capex, free cash flow or a reconciled change in cash.",
            contracts=(c("reported_cfi", ids=("cfi",)),),
        ),
        d(
            "cff",
            "reported_cff",
            ["reported_cff"],
            "identity",
            semantics="Reported signed financing cash flow; does not identify borrowing, distributions or their causes without a source breakdown.",
            contracts=(c("reported_cff", ids=("cff",)),),
        ),
        d(
            "cash_fx_other",
            "reported_cash_fx_other",
            ["reported_cash_fx_other"],
            "identity",
            semantics="Explicit reported aggregate of cash FX and other movements; never inferred as a balancing residual.",
            contracts=(c("reported_cash_fx_other", ids=("cash_fx_other",)),),
        ),
        d(
            "capex",
            "capex_outflow_positive",
            ["capex_outflow_positive"],
            "identity",
            semantics="Cash outflow represented as a positive magnitude",
        ),
        d("free_cash_flow", "cfo-capex_outflow_positive", ["cfo", "capex"], "subtract"),
        d(
            "free_cash_flow",
            "cfo+reported_capex_cash_outflow_negative",
            ["cfo", "reported_capex_cash_outflow_negative"],
            "add",
            version="2",
            semantics="Basic CFO after reported purchases of fixed assets; the purchase cash flow is nonpositive. Does not imply all possible capex or free-cash-flow definitions are covered.",
            contracts=(
                c("cfo"),
                c(
                    "reported_capex_cash_outflow_negative",
                    ids=("capex",),
                    value_constraint="non_positive",
                ),
            ),
        ),
        d(
            "cash_conversion_pct",
            "cfo/ebitda_for_cash_conversion*100",
            ["cfo", "ebitda_for_cash_conversion"],
            "percent",
            "percentage",
            "percentage_2dp",
            contracts=(
                c("cfo"),
                c(
                    "ebitda_for_cash_conversion",
                    ids=("ebitda_statutory", "ebitda_adjusted"),
                    variants=("statutory", "adjusted"),
                ),
            ),
        ),
        d(
            "net_working_capital",
            "current_operating_assets-current_operating_liabilities",
            ["current_operating_assets", "current_operating_liabilities"],
            "subtract",
        ),
        d(
            "dso",
            "average_trade_receivables/revenue*days_in_period",
            ["average_trade_receivables", "revenue", "days_in_period"],
            "days",
            "days",
            "days_1dp",
            contracts=(
                c("average_trade_receivables"),
                c("revenue"),
                c("days_in_period", "days"),
            ),
        ),
        d(
            "dio",
            "average_inventory/cost_of_sales*days_in_period",
            ["average_inventory", "cost_of_sales", "days_in_period"],
            "days",
            "days",
            "days_1dp",
            contracts=(
                c("average_inventory"),
                c("cost_of_sales"),
                c("days_in_period", "days"),
            ),
        ),
        d(
            "dpo",
            "average_trade_payables/purchases*days_in_period",
            ["average_trade_payables", "purchases", "days_in_period"],
            "days",
            "days",
            "days_1dp",
            contracts=(
                c("average_trade_payables"),
                c("purchases"),
                c("days_in_period", "days"),
            ),
        ),
        d(
            "cash_conversion_cycle",
            "dso+dio-dpo",
            ["dso", "dio", "dpo"],
            "add_subtract",
            "days",
            "days_1dp",
            contracts=(c("dso", "days"), c("dio", "days"), c("dpo", "days")),
        ),
        d(
            "roic",
            "nopat/average_invested_capital*100",
            ["nopat_with_explicit_tax_assumptions", "average_invested_capital"],
            "percent",
            "percentage",
            "percentage_2dp",
            contracts=(
                c("nopat_with_explicit_tax_assumptions"),
                c("average_invested_capital"),
            ),
        ),
        d(
            "current_ratio",
            "current_assets/current_liabilities",
            ["current_assets", "current_liabilities"],
            "ratio",
            "ratio",
            "ratio_2dp",
            contracts=(c("current_assets"), c("current_liabilities")),
        ),
        d(
            "quick_ratio",
            "quick_assets/current_liabilities",
            ["quick_assets", "current_liabilities"],
            "ratio",
            "ratio",
            "ratio_2dp",
            contracts=(c("quick_assets"), c("current_liabilities")),
        ),
        d("gross_debt", "debt_excluding_leases", ["debt_excluding_leases"], "identity"),
        d(
            "net_debt",
            "gross_debt-cash_and_cash_equivalents",
            ["gross_debt", "cash_and_cash_equivalents"],
            "subtract",
        ),
        d(
            "net_debt_incl_leases",
            "gross_debt+lease_liabilities-cash_and_cash_equivalents",
            ["gross_debt", "lease_liabilities", "cash_and_cash_equivalents"],
            "add_subtract",
        ),
        d(
            "net_debt_to_ebitda",
            "net_debt_variant/ebitda_for_leverage",
            ["net_debt_variant", "ebitda_for_leverage"],
            "ratio",
            "ratio",
            "ratio_2dp",
            contracts=(
                c(
                    "net_debt_variant",
                    ids=("net_debt", "net_debt_incl_leases"),
                    variants=("excluding_leases", "including_leases"),
                ),
                c(
                    "ebitda_for_leverage",
                    ids=("ebitda_statutory", "ebitda_adjusted"),
                    variants=("statutory", "adjusted"),
                ),
            ),
        ),
        d(
            "interest_coverage",
            "ebit_or_ebitda_for_coverage/interest_expense",
            ["ebit_or_ebitda_for_coverage", "interest_expense"],
            "ratio",
            "ratio",
            "ratio_2dp",
            contracts=(
                c(
                    "ebit_or_ebitda_for_coverage",
                    ids=("ebit_confirmed", "ebitda_statutory", "ebitda_adjusted"),
                    variants=("ebit", "ebitda_statutory", "ebitda_adjusted"),
                ),
                c("interest_expense"),
            ),
        ),
        d(
            "fixed_variable_mix",
            "fixed_rate_principal/eligible_debt_principal*100",
            ["fixed_rate_principal", "eligible_debt_principal"],
            "percent",
            "percentage",
            "percentage_2dp",
            semantics="Fixed-rate share of eligible debt principal from DebtSchedule",
            contracts=(
                c(
                    "fixed_rate_principal",
                    ids=("debt_schedule_principal",),
                    variants=("fixed_rate",),
                    dimensions=(("rate_type", "fixed"),),
                ),
                c(
                    "eligible_debt_principal",
                    ids=("debt_schedule_eligible_principal",),
                    variants=("eligible_debt",),
                ),
            ),
        ),
        d(
            "maturity_concentration",
            "debt_due_in_explicit_window/gross_debt*100",
            ["debt_due_in_explicit_window", "gross_debt"],
            "percent",
            "percentage",
            "percentage_2dp",
        ),
        d(
            "adjustments_to_statutory_ebitda_pct",
            "explicit_ebitda_adjustments/ebitda_statutory*100",
            ["explicit_ebitda_adjustments", "ebitda_statutory"],
            "percent",
            "percentage",
            "percentage_2dp",
            semantics="Explicit EBITDA bridge adjustments as a percentage of statutory EBITDA",
            contracts=(
                c(
                    "explicit_ebitda_adjustments",
                    ids=("ebitda_adjustments",),
                    variants=("adjustment_bridge",),
                ),
                c("ebitda_statutory", variants=("statutory",)),
            ),
        ),
        d(
            "adjustments_to_reference_amount_pct",
            "explicit_ebitda_adjustments/explicit_reference_amount*100",
            ["explicit_ebitda_adjustments", "explicit_reference_amount"],
            "percent",
            "percentage",
            "percentage_2dp",
            semantics="Explicit EBITDA bridge adjustments compared with an explicitly identified reference amount",
            contracts=(
                c(
                    "explicit_ebitda_adjustments",
                    ids=("ebitda_adjustments",),
                    variants=("adjustment_bridge",),
                ),
                c(
                    "explicit_reference_amount",
                    ids=("explicit_reference_amount",),
                    variants=("jm_fin_002_reference",),
                ),
            ),
        ),
    )
)

# Aggregate source principal partitions. Earlier versions retain their semantics.
FINANCIAL_V1 = MetricRegistry(
    (
        *FINANCIAL_V1.definitions,
        *(
            d(
                metric_id,
                "principal_component/eligible_principal*100",
                ["principal_component", "eligible_principal"],
                "percent",
                "percentage",
                "percentage_2dp",
                version=version,
                semantics=semantics,
                contracts=(
                    c(
                        "principal_component",
                        ids=(concept,),
                        value_constraint="non_negative",
                    ),
                    c(
                        "eligible_principal",
                        ids=("debt_eligible_principal",),
                        value_constraint="positive",
                    ),
                ),
            )
            for metric_id, version, concept, semantics in (
                (
                    "fixed_variable_mix",
                    "2",
                    "debt_fixed_principal",
                    "Fixed-rate share of explicitly eligible principal excluding leases, contractual classification before hedging",
                ),
                (
                    "variable_rate_share_pct",
                    "1",
                    "debt_variable_principal",
                    "Variable-rate share of explicitly eligible principal excluding leases, contractual classification before hedging",
                ),
                (
                    "maturity_concentration",
                    "2",
                    "debt_due_within_12_months",
                    "Share of eligible debt principal excluding leases contractually due within twelve months after the reporting date",
                ),
            )
        ),
    )
)

# Confirmed cash capex and commitments retain distinct immutable identities.
FINANCIAL_V1 = MetricRegistry(
    (
        *FINANCIAL_V1.definitions,
        *(
            d(
                concept,
                "reported_" + concept,
                ["reported_" + concept],
                "identity",
                semantics=semantics,
                contracts=(
                    c(
                        "reported_" + concept,
                        ids=(concept,),
                        value_constraint="non_negative",
                    ),
                ),
            )
            for concept, semantics in (
                (
                    "capex_cash_total",
                    "Explicit total cash paid for fixed asset purchases under a confirmed classification; not outstanding commitments or inferred investing cash flow",
                ),
                (
                    "capex_cash_maintenance",
                    "Explicit maintenance cash paid for fixed asset purchases under a confirmed source definition; not a recurring-cost estimate",
                ),
                (
                    "capex_cash_growth",
                    "Explicit growth cash paid for fixed asset purchases under a confirmed source definition; not a forecast, return or recommendation",
                ),
                (
                    "capex_commitments_outstanding",
                    "Explicit outstanding fixed-asset purchase commitments at the reporting date; not cash already paid, a payment schedule or a forecast",
                ),
            )
        ),
        *(
            d(
                key,
                "cash_component/cash_total*100",
                ["cash_component", "cash_total"],
                "percent",
                "percentage",
                "percentage_2dp",
                semantics=semantics,
                contracts=(
                    c(
                        "cash_component",
                        ids=(concept,),
                        value_constraint="non_negative",
                    ),
                    c(
                        "cash_total",
                        ids=("capex_cash_total",),
                        value_constraint="positive",
                    ),
                ),
            )
            for key, concept, semantics in (
                (
                    "capex_maintenance_share_pct",
                    "capex_cash_maintenance",
                    "Maintenance share of confirmed cash fixed-asset purchases, requiring a complete source-defined maintenance/growth partition",
                ),
                (
                    "capex_growth_share_pct",
                    "capex_cash_growth",
                    "Growth share of confirmed cash fixed-asset purchases, requiring a complete source-defined maintenance/growth partition",
                ),
            )
        ),
    )
)


FINANCIAL_V1 = MetricRegistry(
    (
        *FINANCIAL_V1.definitions,
        *(
            d(
                key,
                "reported_" + key,
                ["reported_" + key],
                "identity",
                semantics="Explicit source operating working capital disclosure; not cash flow or a diagnosis of CFO.",
                contracts=(c("reported_" + key, ids=(key,)),),
            )
            for key in ("reported_operating_nwc", "reported_operating_nwc_change")
        ),
        MetricDefinition(
            "operating_nwc_change",
            "1",
            "Exact closing minus adjacent opening operating working capital balance; includes noncash changes and does not measure cash absorption or explain CFO.",
            "closing_operating_nwc-opening_operating_nwc",
            ("closing_operating_nwc", "opening_operating_nwc"),
            "subtract",
            "monetary",
            operand_contracts=(
                c("closing_operating_nwc", ids=("reported_operating_nwc",)),
                c("opening_operating_nwc", ids=("reported_operating_nwc",)),
            ),
            period_rule="opening_stock",
        ),
    )
)
