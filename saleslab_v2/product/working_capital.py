"""Operating balance/disclosure tie-outs. No inferred cash conversion or drivers."""

from decimal import Decimal

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.pipeline.contracts import MetricStep, ReconciliationStep
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .ingestion import terms_for_plan

NWC = "reported_operating_nwc"
CHANGE = "reported_operating_nwc_change"
from .working_capital_labels import WORKING_CAPITAL_METRIC_LABELS as METRIC_LABELS

CHECKS = (
    (
        "REC-WC-CLOSING-001",
        (
            ("assets", "current_operating_assets"),
            ("liabilities", "current_operating_liabilities"),
            ("reported", NWC),
        ),
    ),
    (
        "REC-WC-OPENING-001",
        (
            ("assets", "opening:current_operating_assets"),
            ("liabilities", "opening:current_operating_liabilities"),
            ("reported", "opening:" + NWC),
        ),
    ),
    (
        "REC-WC-CHANGE-001",
        (("opening", "opening:" + NWC), ("change", CHANGE), ("closing", NWC)),
    ),
)
SPECS = (
    (NWC, NWC, (("reported_" + NWC, NWC),)),
    ("opening_reported_operating_nwc", NWC, (("reported_" + NWC, "opening:" + NWC),)),
    (CHANGE, CHANGE, (("reported_" + CHANGE, CHANGE),)),
    (
        "operating_nwc_change",
        "operating_nwc_change",
        (("closing_operating_nwc", NWC), ("opening_operating_nwc", "opening:" + NWC)),
    ),
)


def working_capital_candidates(observations, target, candidates):
    def select(name, include_invalid=False):
        if not name.startswith("opening:"):
            return candidates(name, include_invalid=include_invalid)
        concept = name.split(":", 1)[1]
        anchors = candidates(NWC, include_invalid=True)
        if len(anchors) != 1 or anchors[0].period is None:
            return []
        anchor = anchors[0].period
        return [
            o
            for o in observations
            if o.concept_id == concept
            and o.period
            and o.period.comparison_basis == "current"
            and o.period.fiscal_year_end_month == anchor.fiscal_year_end_month
            and (o.period.fiscal_year is None) == (anchor.fiscal_year is None)
            and o.period.end_date.toordinal() + 1 == anchor.start_date.toordinal()
            and (
                include_invalid
                or (
                    o.normalization_status == "normalized"
                    and not o.issues
                    and o.value is not None
                )
            )
        ]

    return select


def plan_working_capital(choices, select, ref):
    metrics, checks = [], []

    def resolve(bindings):
        inputs = []
        for role, name in bindings:
            valid, all_rows = select(name), select(name, True)
            if len(valid) != 1 or len(all_rows) != 1:
                return None
            inputs.append((role, ref(valid[0])))
        return tuple(inputs)

    for key, metric, bindings in SPECS:
        inputs = resolve(bindings)
        if inputs is not None:
            metrics.append(MetricStep(key, metric, inputs))
    for rule, bindings in CHECKS:
        inputs = resolve(bindings)
        if inputs is None:
            continue
        amount = Decimal(choices.working_capital_rounding_tolerance or "0")
        currency = select(bindings[0][1])[0].currency
        policy = TolerancePolicy(
            "upload-operating-working-capital-explicit",
            "1",
            "monetary",
            currency,
            "raw",
            amount,
            amount,
            amount,
            Decimal(0),
            Decimal(0),
            "User-confirmed absolute operating NWC disclosure rounding allowance in base currency units; this is not a cash flow reconciliation",
        )
        checks.append(ReconciliationStep(rule, inputs, policy))
    return metrics, checks


def working_capital_availability(choices, calculated, validation, select):
    results = {m["key"]: m for m in calculated}
    actual = {r["rule_id"]: r for r in validation["reconciliations"]}
    index = ("pt", "en", "es", "fr").index(choices.report_language)
    terms = terms_for_plan("13")

    def requirements(bindings):
        out = []
        for role, name in bindings:
            valid, all_rows = select(name), select(name, True)
            if len(valid) == len(all_rows) == 1:
                continue
            concept = name.removeprefix("opening:")
            label = terms[concept].labels[LanguageCode(choices.report_language)]
            if name.startswith("opening:"):
                label = (
                    "Saldo inicial · ",
                    "Opening balance · ",
                    "Saldo inicial · ",
                    "Solde initial · ",
                )[index] + label
            out.append(
                {
                    "role": role,
                    "concept_id": concept,
                    "label": label,
                    "status": (
                        "ambiguous"
                        if len(all_rows) > 1
                        else "needs_confirmation" if all_rows else "missing"
                    ),
                    "source_references": sorted(o.source_reference for o in all_rows),
                    "reason_codes": sorted(
                        {i.code for o in all_rows for i in o.issues}
                    ),
                }
            )
        return out

    metrics = []
    for key, metric, bindings in SPECS:
        result = results.get(key)
        metrics.append(
            {
                "key": key,
                "metric_id": metric,
                "label": METRIC_LABELS[key][index],
                "status": (
                    ("calculated" if result["status"] == "calculated" else "blocked")
                    if result
                    else "not_planned"
                ),
                "requirements": [] if result else requirements(bindings),
                "reason_codes": result["reason_codes"] if result else [],
            }
        )
    gaps = [
        {
            "rule_id": rule,
            "status": actual[rule]["status"] if rule in actual else "not_planned",
            "requirements": [] if rule in actual else requirements(bindings),
            "reason_codes": actual[rule]["reason_codes"] if rule in actual else [],
        }
        for rule, bindings in CHECKS
    ]
    return metrics, gaps
