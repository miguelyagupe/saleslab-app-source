"""Cash capex classifications; outstanding commitments never become cash flows."""

from decimal import Decimal

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.i18n.financial_v8 import CAPEX_LABELS, CASH_CAPEX_CONCEPTS
from saleslab_v2.pipeline.contracts import MetricStep, ReconciliationStep
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .ingestion import terms_for_plan
from .preview import LABELS

SHARES = {
    "capex_maintenance_share_pct": "capex_cash_maintenance",
    "capex_growth_share_pct": "capex_cash_growth",
}
PARTITIONS = (
    ("REC-CAPEX-001", CASH_CAPEX_CONCEPTS),
    ("REC-CAPEX-002", ("capex_cash_total", "capex")),
)


def plan_capex(choices, select, ref):
    metrics, checks = [], []

    def one(concept):
        valid, all_rows = select(concept), select(concept, invalid=True)
        return valid[0] if len(valid) == len(all_rows) == 1 else None

    for concept in CAPEX_LABELS:
        value = one(concept)
        if value is not None:
            metrics.append(
                MetricStep(concept, concept, (("reported_" + concept, ref(value)),))
            )
    for rule_id, concepts in PARTITIONS:
        values = [one(c) for c in concepts]
        if any(v is None for v in values):
            continue
        amount = Decimal(choices.capex_rounding_tolerance or "0")
        policy = TolerancePolicy(
            "upload-capex-cash-explicit",
            "1",
            "monetary",
            values[0].currency,
            "raw",
            amount,
            amount,
            amount,
            Decimal(0),
            Decimal(0),
            "User-confirmed absolute rounding allowance in base currency units for cash capex classification and independent purchase cash flow",
        )
        checks.append(
            ReconciliationStep(
                rule_id,
                tuple(
                    (concept, ref(value)) for concept, value in zip(concepts, values)
                ),
                policy,
            )
        )
    total = one("capex_cash_total")
    if (
        total is not None
        and total.value > 0
        and all(one(c) is not None for c in CASH_CAPEX_CONCEPTS)
    ):
        for key, concept in SHARES.items():
            metrics.append(
                MetricStep(
                    key,
                    key,
                    (("cash_component", ref(one(concept))), ("cash_total", ref(total))),
                )
            )
    return metrics, checks


def capex_availability(choices, calculated, validation, select):
    results = {m["key"]: m for m in calculated}
    index = ("pt", "en", "es", "fr").index(choices.report_language)
    terms = terms_for_plan(choices.planning_version)

    def requirements(concepts):
        out = []
        for concept in concepts:
            all_rows, valid = select(concept, invalid=True), select(concept)
            if len(all_rows) == len(valid) == 1:
                continue
            out.append(
                {
                    "role": concept,
                    "concept_id": concept,
                    "label": terms[concept].labels[
                        LanguageCode(choices.report_language)
                    ],
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

    rows = []
    for key in (*CAPEX_LABELS, *SHARES):
        result = results.get(key)
        total = select("capex_cash_total")
        zero = key in SHARES and len(total) == 1 and total[0].value == 0
        rows.append(
            {
                "key": key,
                "metric_id": key,
                "label": LABELS[key][index],
                "status": (
                    "calculated"
                    if result and result["status"] == "calculated"
                    else "blocked" if result or zero else "not_planned"
                ),
                "requirements": (
                    []
                    if result
                    else requirements(CASH_CAPEX_CONCEPTS if key in SHARES else (key,))
                ),
                "reason_codes": (
                    result["reason_codes"]
                    if result
                    else ["ZERO_DENOMINATOR"] if zero else []
                ),
            }
        )
    actual = {c["rule_id"]: c for c in validation["reconciliations"]}
    gaps = [
        {
            "rule_id": rule_id,
            "status": actual[rule_id]["status"] if rule_id in actual else "not_planned",
            "requirements": requirements(concepts),
            "reason_codes": (
                actual[rule_id]["reason_codes"] if rule_id in actual else []
            ),
        }
        for rule_id, concepts in PARTITIONS
    ]
    return rows, gaps
