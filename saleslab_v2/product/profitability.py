"""Plan the complete profitability bundle using explicit source and metric dependencies."""

from decimal import Decimal

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.pipeline.contracts import InputRef, MetricStep, ReconciliationStep
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .ingestion import terms_for_plan
from .preview import LABELS

# (key, metric ID, definition version, (role, source/metric, reference) bindings)
SOURCE_STEPS = (
    ("cost_of_sales", "cost_of_sales"),
    ("operating_costs", "operating_costs"),
    ("ebit_confirmed", "ebit"),
    ("depreciation_and_amortization", "depreciation_and_amortization"),
    ("ebitda_adjustments", "ebitda_adjustments"),
    ("other_operating_income_expense", "other_operating_income_expense"),
    ("net_finance_cost", "net_finance_cost"),
    ("income_tax", "income_tax"),
)
SPECS = tuple(
    (key, key, "1", (("reported_" + key, "source", concept),))
    for key, concept in SOURCE_STEPS
) + (
    (
        "reported_ebitda_statutory",
        "ebitda_statutory",
        "2",
        (("reported_statutory_ebitda", "source", "reported_statutory_ebitda"),),
    ),
    (
        "reported_ebitda_adjusted",
        "ebitda_adjusted",
        "2",
        (("reported_adjusted_ebitda", "source", "reported_adjusted_ebitda"),),
    ),
    (
        "gross_profit",
        "gross_profit",
        "1",
        (
            ("revenue", "source", "revenue"),
            ("cost_of_sales", "metric", "cost_of_sales"),
        ),
    ),
    (
        "gross_margin_pct",
        "gross_margin_pct",
        "1",
        (("gross_profit", "metric", "gross_profit"), ("revenue", "source", "revenue")),
    ),
    (
        "ebitda_statutory",
        "ebitda_statutory",
        "1",
        (
            ("confirmed_ebit", "metric", "ebit_confirmed"),
            (
                "depreciation_and_amortization",
                "metric",
                "depreciation_and_amortization",
            ),
        ),
    ),
    (
        "ebitda_adjusted",
        "ebitda_adjusted",
        "1",
        (
            ("ebitda_statutory", "metric", "ebitda_statutory"),
            ("explicit_ebitda_adjustments", "metric", "ebitda_adjustments"),
        ),
    ),
    (
        "ebitda_margin_statutory_pct",
        "ebitda_margin_pct",
        "1",
        (
            ("ebitda_for_margin", "metric", "ebitda_statutory"),
            ("revenue", "source", "revenue"),
        ),
    ),
    (
        "ebitda_margin_adjusted_pct",
        "ebitda_margin_pct",
        "1",
        (
            ("ebitda_for_margin", "metric", "ebitda_adjusted"),
            ("revenue", "source", "revenue"),
        ),
    ),
    (
        "cash_conversion_statutory_pct",
        "cash_conversion_pct",
        "1",
        (
            ("cfo", "source", "cfo"),
            ("ebitda_for_cash_conversion", "metric", "ebitda_statutory"),
        ),
    ),
    (
        "cash_conversion_adjusted_pct",
        "cash_conversion_pct",
        "1",
        (
            ("cfo", "source", "cfo"),
            ("ebitda_for_cash_conversion", "metric", "ebitda_adjusted"),
        ),
    ),
    (
        "adjustments_to_statutory_ebitda_pct",
        "adjustments_to_statutory_ebitda_pct",
        "1",
        (
            ("explicit_ebitda_adjustments", "metric", "ebitda_adjustments"),
            ("ebitda_statutory", "metric", "ebitda_statutory"),
        ),
    ),
)
BRIDGES = (
    (
        "REC-PL-001",
        (
            ("revenue", "source", "revenue"),
            ("costs", "metric", "operating_costs"),
            ("other_operating", "metric", "other_operating_income_expense"),
            ("ebit", "metric", "ebit_confirmed"),
            ("finance", "metric", "net_finance_cost"),
            ("tax", "metric", "income_tax"),
            ("net_income", "source", "net_income"),
        ),
    ),
    (
        "REC-EBITDA-001",
        (
            ("ebit", "metric", "ebit_confirmed"),
            ("da", "metric", "depreciation_and_amortization"),
            ("statutory_ebitda", "metric", "reported_ebitda_statutory"),
            ("adjustments", "metric", "ebitda_adjustments"),
            ("adjusted_ebitda", "metric", "reported_ebitda_adjusted"),
        ),
    ),
)


def source_concepts(bindings):
    definitions = {s[0]: s[3] for s in SPECS}
    concepts = set()
    for _, kind, name in bindings:
        concepts.update(
            {name} if kind == "source" else source_concepts(definitions[name])
        )
    return sorted(concepts)


def requirements(bindings, candidates, language):
    terms = terms_for_plan("7")
    output = []
    for concept in source_concepts(bindings):
        original = candidates(concept, include_invalid=True)
        valid = candidates(concept)
        if len(original) == len(valid) == 1:
            continue
        output.append(
            {
                "role": concept,
                "concept_id": concept,
                "label": terms[concept].labels[LanguageCode(language)],
                "status": (
                    "ambiguous"
                    if len(original) > 1
                    else "needs_confirmation" if original else "missing"
                ),
                "source_references": sorted({o.source_reference for o in original}),
                "reason_codes": sorted({i.code for o in original for i in o.issues}),
            }
        )
    return output


def plan_profitability(choices, candidates, ref):
    metrics, reconciliations, selected = [], [], set()

    def resolve(bindings):
        refs = []
        for role, kind, name in bindings:
            if kind == "metric":
                if name not in selected:
                    return None
                value = InputRef(name)
            else:
                valid = candidates(name)
                original = candidates(name, include_invalid=True)
                if len(valid) != 1 or len(original) != 1:
                    return None
                value = ref(valid[0])
            refs.append((role, value))
        return tuple(refs)

    for key, metric_id, version, bindings in SPECS:
        inputs = resolve(bindings)
        if inputs is not None:
            metrics.append(MetricStep(key, metric_id, inputs, version=version))
            selected.add(key)
    for rule_id, bindings in BRIDGES:
        inputs = resolve(bindings)
        if inputs is None:
            continue
        # Both bridges contain the confirmed EBIT, whose source supplies currency.
        currency = candidates("ebit")[0].currency
        tolerance = Decimal(choices.profitability_rounding_tolerance or "0")
        policy = TolerancePolicy(
            "upload-profitability-explicit",
            "1",
            "monetary",
            currency,
            "raw",
            tolerance,
            tolerance,
            tolerance,
            Decimal(0),
            Decimal(0),
            "User-confirmed absolute profitability rounding allowance in base currency units; larger differences block",
        )
        reconciliations.append(ReconciliationStep(rule_id, inputs, policy))
    return metrics, reconciliations


def profitability_availability(choices, target, calculated, validation, candidates):
    results = {m["key"]: m for m in calculated}
    checks = {r["rule_id"]: r for r in validation["reconciliations"]}
    index = ("pt", "en", "es", "fr").index(choices.report_language)
    metrics, bridges = [], []
    for key, metric_id, _, bindings in SPECS:
        result = results.get(key)
        metrics.append(
            {
                "key": key,
                "metric_id": metric_id,
                "label": LABELS.get(key, LABELS[metric_id])[index],
                "status": (
                    ("calculated" if result["status"] == "calculated" else "blocked")
                    if result
                    else "not_planned" if target else "period_required"
                ),
                "requirements": (
                    []
                    if result
                    else requirements(bindings, candidates, choices.report_language)
                ),
                "reason_codes": (
                    result["reason_codes"]
                    if result
                    else [] if target else ["upload.target_period_required"]
                ),
            }
        )
    for rule_id, bindings in BRIDGES:
        actual = checks.get(rule_id)
        bridges.append(
            {
                "rule_id": rule_id,
                "status": (
                    actual["status"]
                    if actual
                    else "not_planned" if target else "period_required"
                ),
                "requirements": (
                    []
                    if actual
                    else requirements(bindings, candidates, choices.report_language)
                ),
                "reason_codes": (
                    actual["reason_codes"]
                    if actual
                    else [] if target else ["upload.target_period_required"]
                ),
            }
        )
    return metrics, bridges
