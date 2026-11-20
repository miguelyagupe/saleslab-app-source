"""ROIC plans use only uniquely confirmed monetary operands for the target period."""

from saleslab_v2.i18n.financial_v9 import ROIC_LABELS
from saleslab_v2.pipeline.contracts import MetricStep

ROLES = tuple(ROIC_LABELS)
LABELS = (
    "ROIC do período · não anualizado",
    "Period ROIC · not annualized",
    "ROIC del período · no anualizado",
    "ROIC de la période · non annualisé",
)


def plan_roic(candidates, ref):
    values = [candidates(c) for c in ROLES]
    if all(
        len(v) == len(candidates(c, include_invalid=True)) == 1
        for c, v in zip(ROLES, values)
    ):
        return [
            MetricStep(
                "roic",
                "roic",
                tuple((role, ref(value[0])) for role, value in zip(ROLES, values)),
                version="2",
            )
        ]
    return []


def roic_availability(choices, calculated, candidates):
    result = next((m for m in calculated if m["key"] == "roic"), None)
    requirements = []
    index = ("pt", "en", "es", "fr").index(choices.report_language)
    for concept in ROLES:
        valid, all_rows = candidates(concept), candidates(concept, include_invalid=True)
        if len(valid) == len(all_rows) == 1:
            continue
        requirements.append(
            {
                "role": concept,
                "concept_id": concept,
                "label": ROIC_LABELS[concept][index],
                "status": (
                    "ambiguous"
                    if len(all_rows) > 1
                    else "needs_confirmation" if all_rows else "missing"
                ),
                "source_references": sorted(o.source_reference for o in all_rows),
                "reason_codes": sorted({i.code for o in all_rows for i in o.issues}),
            }
        )
    return {
        "key": "roic",
        "metric_id": "roic",
        "label": LABELS[index],
        "status": (
            ("calculated" if result["status"] == "calculated" else "blocked")
            if result
            else "not_planned"
        ),
        "requirements": [] if result else requirements,
        "reason_codes": (
            result["reason_codes"]
            if result
            else sorted({c for r in requirements for c in r["reason_codes"]})
        ),
    }
