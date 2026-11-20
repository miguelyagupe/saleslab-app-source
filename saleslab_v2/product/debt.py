"""Aggregate debt composition with complete, reconciled principal partitions."""

from decimal import Decimal

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.pipeline.contracts import MetricStep, ReconciliationStep
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .ingestion import terms_for_plan
from .preview import LABELS

TOTAL = "debt_eligible_principal"
PARTITIONS = (
    ("REC-DEBT-RATE-001", ("debt_fixed_principal", "debt_variable_principal")),
    (
        "REC-DEBT-MATURITY-001",
        ("debt_due_within_12_months", "debt_due_after_12_months"),
    ),
)
SPECS = (
    ("fixed_variable_mix", "2", "debt_fixed_principal", PARTITIONS[0]),
    ("variable_rate_share_pct", "1", "debt_variable_principal", PARTITIONS[0]),
    ("maturity_concentration", "2", "debt_due_within_12_months", PARTITIONS[1]),
)


def plan_debt(choices, select, ref):
    metrics, checks = [], []

    def one(concept):
        valid, all_rows = select(concept), select(concept, invalid=True)
        return valid[0] if len(valid) == len(all_rows) == 1 else None

    total = one(TOTAL)
    if total is None:
        return metrics, checks
    tolerance = Decimal(choices.debt_rounding_tolerance or "0")
    policy = TolerancePolicy(
        "upload-debt-principal-explicit",
        "1",
        "monetary",
        total.currency,
        "raw",
        tolerance,
        tolerance,
        tolerance,
        Decimal(0),
        Decimal(0),
        "User-confirmed absolute rounding allowance in base currency units for debt principal partitions",
    )
    complete = set()
    for rule_id, components in PARTITIONS:
        values = [one(c) for c in components]
        if any(v is None for v in values):
            continue
        checks.append(
            ReconciliationStep(
                rule_id,
                tuple(
                    (c, ref(v)) for c, v in zip((*components, TOTAL), (*values, total))
                ),
                policy,
            )
        )
        complete.add(rule_id)
    for key, version, numerator, (rule_id, _) in SPECS:
        if rule_id in complete:
            metrics.append(
                MetricStep(
                    key,
                    key,
                    (
                        ("principal_component", ref(one(numerator))),
                        ("eligible_principal", ref(total)),
                    ),
                    version=version,
                )
            )
    return metrics, checks


def debt_availability(choices, calculated, validation, select):
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
    for key, _, _, (_, components) in SPECS:
        result = results.get(key)
        rows.append(
            {
                "key": key,
                "metric_id": key,
                "label": LABELS[key][index],
                "status": (
                    "calculated"
                    if result and result["status"] == "calculated"
                    else "blocked" if result else "not_planned"
                ),
                "requirements": [] if result else requirements((*components, TOTAL)),
                "reason_codes": result["reason_codes"] if result else [],
            }
        )
    actual = {c["rule_id"]: c for c in validation["reconciliations"]}
    gaps = [
        {
            "rule_id": rule_id,
            "status": actual[rule_id]["status"] if rule_id in actual else "not_planned",
            "requirements": requirements((*components, TOTAL)),
            "reason_codes": (
                actual[rule_id]["reason_codes"] if rule_id in actual else []
            ),
        }
        for rule_id, components in PARTITIONS
    ]
    return rows, gaps
