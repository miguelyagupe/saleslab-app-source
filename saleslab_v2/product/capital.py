"""Explicit liquidity, debt and working-capital plans; no inferred classifications."""

from decimal import Decimal

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.pipeline.contracts import InputRef, MetricStep, ReconciliationStep
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .ingestion import terms_for_plan
from .preview import LABELS
from .profitability import SOURCE_STEPS as PROFIT_SOURCE_STEPS
from .profitability import SPECS as PROFIT_SPECS

SPECS = (
    (
        "cash_and_cash_equivalents",
        "cash_and_cash_equivalents",
        "1",
        (
            (
                "reported_cash_and_cash_equivalents",
                "source",
                "eligible_unrestricted_cash",
            ),
        ),
    ),
    (
        "current_operating_assets",
        "current_operating_assets",
        "1",
        (("reported_current_operating_assets", "source", "current_operating_assets"),),
    ),
    (
        "current_operating_liabilities",
        "current_operating_liabilities",
        "1",
        (
            (
                "reported_current_operating_liabilities",
                "source",
                "current_operating_liabilities",
            ),
        ),
    ),
    (
        "average_trade_receivables",
        "average_trade_receivables",
        "1",
        (
            (
                "reported_average_trade_receivables",
                "source",
                "average_trade_receivables",
            ),
        ),
    ),
    (
        "average_inventory",
        "average_inventory",
        "1",
        (("reported_average_inventory", "source", "average_inventory"),),
    ),
    (
        "average_trade_payables",
        "average_trade_payables",
        "1",
        (("reported_average_trade_payables", "source", "average_trade_payables"),),
    ),
    ("purchases", "purchases", "1", (("reported_purchases", "source", "purchases"),)),
    (
        "interest_expense",
        "interest_expense",
        "1",
        (("reported_interest_expense", "source", "interest_expense"),),
    ),
    (
        "lease_liabilities",
        "lease_liabilities",
        "1",
        (("reported_lease_liabilities", "source", "lease_liabilities"),),
    ),
    (
        "debt_drawdowns",
        "debt_drawdowns",
        "1",
        (("reported_debt_drawdowns", "source", "debt_drawdowns"),),
    ),
    (
        "debt_repayments",
        "debt_repayments",
        "1",
        (("reported_debt_repayments", "source", "debt_repayments"),),
    ),
    (
        "debt_fx_other",
        "debt_fx_other",
        "1",
        (("reported_debt_fx_other", "source", "debt_fx_other"),),
    ),
    (
        "lease_additions",
        "lease_additions",
        "1",
        (("reported_lease_additions", "source", "lease_additions"),),
    ),
    (
        "lease_modifications",
        "lease_modifications",
        "1",
        (("reported_lease_modifications", "source", "lease_modifications"),),
    ),
    (
        "lease_interest",
        "lease_interest",
        "1",
        (("reported_lease_interest", "source", "lease_interest"),),
    ),
    (
        "lease_payments",
        "lease_payments",
        "1",
        (("reported_lease_payments", "source", "lease_payments"),),
    ),
    (
        "lease_fx_other",
        "lease_fx_other",
        "1",
        (("reported_lease_fx_other", "source", "lease_fx_other"),),
    ),
    (
        "gross_debt",
        "gross_debt",
        "2",
        (("debt_excluding_leases", "source", "debt_excluding_leases"),),
    ),
    (
        "opening_gross_debt",
        "gross_debt",
        "2",
        (("debt_excluding_leases", "source", "opening:debt_excluding_leases"),),
    ),
    (
        "opening_lease_liabilities",
        "lease_liabilities",
        "1",
        (("reported_lease_liabilities", "source", "opening:lease_liabilities"),),
    ),
    (
        "quick_assets",
        "quick_assets",
        "1",
        (
            ("eligible_cash", "metric", "cash_and_cash_equivalents"),
            ("eligible_trade_receivables", "source", "eligible_trade_receivables"),
            (
                "other_permitted_liquid_assets",
                "source",
                "other_permitted_liquid_assets",
            ),
        ),
    ),
    (
        "quick_ratio",
        "quick_ratio",
        "2",
        (
            ("quick_assets", "metric", "quick_assets"),
            ("current_liabilities", "source", "current_liabilities"),
        ),
    ),
    (
        "net_working_capital",
        "net_working_capital",
        "1",
        (
            ("current_operating_assets", "metric", "current_operating_assets"),
            (
                "current_operating_liabilities",
                "metric",
                "current_operating_liabilities",
            ),
        ),
    ),
    (
        "net_debt",
        "net_debt",
        "1",
        (
            ("gross_debt", "metric", "gross_debt"),
            ("cash_and_cash_equivalents", "metric", "cash_and_cash_equivalents"),
        ),
    ),
    (
        "net_debt_incl_leases",
        "net_debt_incl_leases",
        "1",
        (
            ("gross_debt", "metric", "gross_debt"),
            ("lease_liabilities", "metric", "lease_liabilities"),
            ("cash_and_cash_equivalents", "metric", "cash_and_cash_equivalents"),
        ),
    ),
    ("days_for_dso", "days_in_period", "1", (("period_anchor", "source", "revenue"),)),
    (
        "dso",
        "dso",
        "2",
        (
            ("average_trade_receivables", "metric", "average_trade_receivables"),
            ("revenue", "source", "revenue"),
            ("days_in_period", "metric", "days_for_dso"),
        ),
    ),
    (
        "days_for_dio",
        "days_in_period",
        "1",
        (("period_anchor", "metric", "cost_of_sales"),),
    ),
    (
        "dio",
        "dio",
        "2",
        (
            ("average_inventory", "metric", "average_inventory"),
            ("cost_of_sales", "metric", "cost_of_sales"),
            ("days_in_period", "metric", "days_for_dio"),
        ),
    ),
    (
        "days_for_dpo",
        "days_in_period",
        "1",
        (("period_anchor", "metric", "purchases"),),
    ),
    (
        "dpo",
        "dpo",
        "2",
        (
            ("average_trade_payables", "metric", "average_trade_payables"),
            ("purchases", "metric", "purchases"),
            ("days_in_period", "metric", "days_for_dpo"),
        ),
    ),
    (
        "cash_conversion_cycle",
        "cash_conversion_cycle",
        "1",
        (("dso", "metric", "dso"), ("dio", "metric", "dio"), ("dpo", "metric", "dpo")),
    ),
    (
        "net_debt_to_ebitda_statutory",
        "net_debt_to_ebitda",
        "2",
        (
            ("net_debt_variant", "metric", "net_debt"),
            ("ebitda_for_leverage", "metric", "ebitda_statutory"),
        ),
    ),
    (
        "net_debt_to_ebitda_adjusted",
        "net_debt_to_ebitda",
        "2",
        (
            ("net_debt_variant", "metric", "net_debt"),
            ("ebitda_for_leverage", "metric", "ebitda_adjusted"),
        ),
    ),
    (
        "net_debt_incl_leases_to_ebitda_statutory",
        "net_debt_to_ebitda",
        "2",
        (
            ("net_debt_variant", "metric", "net_debt_incl_leases"),
            ("ebitda_for_leverage", "metric", "ebitda_statutory"),
        ),
    ),
    (
        "net_debt_incl_leases_to_ebitda_adjusted",
        "net_debt_to_ebitda",
        "2",
        (
            ("net_debt_variant", "metric", "net_debt_incl_leases"),
            ("ebitda_for_leverage", "metric", "ebitda_adjusted"),
        ),
    ),
    (
        "interest_coverage_ebit_confirmed",
        "interest_coverage",
        "1",
        (
            ("ebit_or_ebitda_for_coverage", "metric", "ebit_confirmed"),
            ("interest_expense", "metric", "interest_expense"),
        ),
    ),
    (
        "interest_coverage_ebitda_statutory",
        "interest_coverage",
        "1",
        (
            ("ebit_or_ebitda_for_coverage", "metric", "ebitda_statutory"),
            ("interest_expense", "metric", "interest_expense"),
        ),
    ),
    (
        "interest_coverage_ebitda_adjusted",
        "interest_coverage",
        "1",
        (
            ("ebit_or_ebitda_for_coverage", "metric", "ebitda_adjusted"),
            ("interest_expense", "metric", "interest_expense"),
        ),
    ),
)
BRIDGES = (
    (
        "REC-DEBT-001",
        (
            ("opening", "metric", "opening_gross_debt"),
            ("drawdowns", "metric", "debt_drawdowns"),
            ("repayments", "metric", "debt_repayments"),
            ("fx_other", "metric", "debt_fx_other"),
            ("closing", "metric", "gross_debt"),
        ),
    ),
    (
        "REC-LEASE-001",
        (
            ("opening", "metric", "opening_lease_liabilities"),
            ("additions", "metric", "lease_additions"),
            ("modifications", "metric", "lease_modifications"),
            ("interest", "metric", "lease_interest"),
            ("payments", "metric", "lease_payments"),
            ("fx_other", "metric", "lease_fx_other"),
            ("closing", "metric", "lease_liabilities"),
        ),
    ),
)


def source_concepts(bindings):
    definitions = {s[0]: s[3] for s in (*PROFIT_SPECS, *SPECS)}
    concepts = set()
    for _, kind, name in bindings:
        concepts.update(
            {name} if kind == "source" else source_concepts(definitions[name])
        )
    return sorted(concepts)


def capital_candidates(observations, target, candidates):
    """Opening stocks belong to this analysis and must be truly adjacent, not comparative."""
    anchors = {
        "debt_excluding_leases": "debt_drawdowns",
        "lease_liabilities": "lease_additions",
    }

    def select(name, *, include_invalid=False):
        if not name.startswith("opening:"):
            return candidates(name, include_invalid=include_invalid)
        concept = name.split(":", 1)[1]
        flows = candidates(anchors[concept])
        originals = candidates(anchors[concept], include_invalid=True)
        if not target or len(flows) != 1 or len(originals) != 1:
            return []
        anchor = flows[0].period
        selected = []
        for o in observations:
            p = o.period
            if o.concept_id != concept or p is None:
                continue
            if (p.comparison_basis, p.fiscal_year_end_month) != (
                "current",
                anchor.fiscal_year_end_month,
            ):
                continue
            if p.end_date.toordinal() + 1 != anchor.start_date.toordinal():
                continue
            if include_invalid or (
                o.normalization_status == "normalized"
                and not o.issues
                and o.value is not None
            ):
                selected.append(o)
        return selected

    return select


def requirements(bindings, select, language):
    terms, output = terms_for_plan("8"), []
    index = ("pt", "en", "es", "fr").index(language)
    for name in source_concepts(bindings):
        valid, original = select(name), select(name, include_invalid=True)
        if len(valid) == len(original) == 1:
            continue
        concept = name.removeprefix("opening:")
        label = terms[concept].labels[LanguageCode(language)]
        if name.startswith("opening:"):
            label = (
                "Saldo inicial · ",
                "Opening balance · ",
                "Saldo inicial · ",
                "Solde initial · ",
            )[index] + label
        output.append(
            {
                "role": name,
                "concept_id": concept,
                "label": label,
                "status": (
                    "ambiguous"
                    if len(original) > 1
                    else "needs_confirmation" if original else "missing"
                ),
                "source_references": sorted({o.source_reference for o in original}),
                "reason_codes": sorted(
                    {issue.code for o in original for issue in o.issues}
                ),
            }
        )
    return output


def plan_capital(choices, select, ref, existing_metrics):
    selected = {s.key for s in existing_metrics}
    metrics, reconciliations = [], []

    def resolve(bindings):
        inputs = []
        for role, kind, name in bindings:
            if kind == "metric":
                if name not in selected:
                    return None
                value = InputRef(name)
            else:
                valid, original = select(name), select(name, include_invalid=True)
                if len(valid) != 1 or len(original) != 1:
                    return None
                value = ref(valid[0])
            inputs.append((role, value))
        return tuple(inputs)

    for key, metric, version, bindings in SPECS:
        inputs = resolve(bindings)
        if inputs is not None:
            metrics.append(MetricStep(key, metric, inputs, version=version))
            selected.add(key)
    for rule, bindings in BRIDGES:
        inputs = resolve(bindings)
        if inputs is None:
            continue
        concept = (
            "debt_excluding_leases" if rule == "REC-DEBT-001" else "lease_liabilities"
        )
        currency = select(concept)[0].currency
        tolerance = Decimal(choices.capital_rounding_tolerance or "0")
        policy = TolerancePolicy(
            "upload-capital-explicit",
            "1",
            "monetary",
            currency,
            "raw",
            tolerance,
            tolerance,
            tolerance,
            Decimal(0),
            Decimal(0),
            "User-confirmed absolute debt/lease rounding allowance in base currency units; larger differences block",
        )
        reconciliations.append(ReconciliationStep(rule, inputs, policy, version="2"))
    return metrics, reconciliations


def capital_availability(choices, target, calculated, validation, select):
    results = {m["key"]: m for m in calculated}
    checks = {r["rule_id"]: r for r in validation["reconciliations"]}
    index = ("pt", "en", "es", "fr").index(choices.report_language)
    metrics, bridges = [], []
    for key, metric, _, bindings in SPECS:
        result = results.get(key)
        metrics.append(
            {
                "key": key,
                "metric_id": metric,
                "label": LABELS.get(key, LABELS[metric])[index],
                "status": (
                    ("calculated" if result["status"] == "calculated" else "blocked")
                    if result
                    else "not_planned" if target else "period_required"
                ),
                "requirements": (
                    []
                    if result
                    else requirements(bindings, select, choices.report_language)
                ),
                "reason_codes": (
                    result["reason_codes"]
                    if result
                    else [] if target else ["upload.target_period_required"]
                ),
            }
        )
    for rule, bindings in BRIDGES:
        actual = checks.get(rule)
        bridges.append(
            {
                "rule_id": rule,
                "status": (
                    actual["status"]
                    if actual
                    else "not_planned" if target else "period_required"
                ),
                "requirements": (
                    []
                    if actual
                    else requirements(bindings, select, choices.report_language)
                ),
                "reason_codes": (
                    actual["reason_codes"]
                    if actual
                    else [] if target else ["upload.target_period_required"]
                ),
            }
        )
    return metrics, bridges


# A pinned metric-step selection restricts narrative evidence only. All source
# identities and calendar helpers remain in calculations, reconciliation inputs
# and the complete published metric appendix for independent re-execution.
ANALYST_HELPERS = frozenset(
    {spec[0] for spec in SPECS if spec[3][0][0].startswith("reported_")}
    | {key for key, _ in PROFIT_SOURCE_STEPS}
    | {
        "opening_reported_operating_nwc",
        "opening_gross_debt",
        "opening_lease_liabilities",
        "quick_assets",
        "cash_fx_other",
        "days_for_dso",
        "days_for_dio",
        "days_for_dpo",
    }
) - {"lease_liabilities"}


def analyst_metric_keys(metrics):
    return tuple(sorted(s.key for s in metrics if s.key not in ANALYST_HELPERS))
