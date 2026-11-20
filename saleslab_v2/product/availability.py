"""Explain existing planner omissions; never select operands or change a gate."""

from saleslab_v2.domain.enums import LanguageCode

from .ingestion import terms_for_plan
from .preview import LABELS

METRICS = (
    ("cash_fx_other", 6, (("reported_cash_fx_other", "cash_fx_other"),)),
    ("revenue", 1, (("reported_revenue", "revenue"),)),
    ("net_margin_pct", 1, (("net_income", "net_income"), ("revenue", "revenue"))),
    ("cfo", 1, (("reported_cfo", "cfo"),)),
    (
        "revenue_growth_reported_pct",
        1,
        (("revenue_current", "revenue"), ("revenue_prior", "revenue")),
    ),
    ("ebit_margin_pct", 2, (("confirmed_ebit", "ebit"), ("revenue", "revenue"))),
    (
        "free_cash_flow",
        3,
        (("cfo", "cfo"), ("reported_capex_cash_outflow_negative", "capex")),
    ),
    ("cfi", 4, (("reported_cfi", "cfi"),)),
    ("cff", 4, (("reported_cff", "cff"),)),
    (
        "current_ratio",
        5,
        (
            ("current_assets", "current_assets"),
            ("current_liabilities", "current_liabilities"),
        ),
    ),
)
PRIOR_LABELS = (
    "Receita do período homólogo",
    "Prior-year revenue",
    "Ingresos del período homólogo",
    "Chiffre d’affaires de la période homologue",
)


def metric_availability(choices, target, calculated, candidates, prior_revenue):
    terms = terms_for_plan(choices.planning_version)
    language = choices.report_language
    index = ("pt", "en", "es", "fr").index(language)
    results = {m["metric_id"]: m for m in calculated}
    output = []
    for metric_id, minimum, roles in METRICS:
        if int(choices.planning_version) < minimum:
            continue
        result = results.get(metric_id)
        row = {
            "metric_id": metric_id,
            "label": LABELS[metric_id][index],
            "status": "not_planned",
            "requirements": [],
            "reason_codes": [],
        }
        if result:
            row.update(
                status="calculated" if result["status"] == "calculated" else "blocked",
                reason_codes=list(result["reason_codes"]),
            )
        elif not target:
            row.update(
                status="period_required", reason_codes=["upload.target_period_required"]
            )
        else:
            for role, concept in roles:
                prior = role == "revenue_prior"
                requirement = {
                    "role": role,
                    "concept_id": concept,
                    "label": (
                        PRIOR_LABELS[index]
                        if prior
                        else terms[concept].labels[LanguageCode(language)]
                    ),
                    "status": "missing",
                    "source_references": [],
                    "reason_codes": [],
                }
                if prior and len(candidates("revenue")) != 1:
                    requirement.update(
                        status="current_revenue_required",
                        reason_codes=["upload.current_revenue_required"],
                    )
                else:
                    valid = prior_revenue() if prior else candidates(concept)
                    if len(valid) == 1:
                        continue
                    original = (
                        prior_revenue(include_invalid=True)
                        if prior
                        else candidates(concept, include_invalid=True)
                    )
                    requirement.update(
                        status=(
                            "ambiguous"
                            if len(original) > 1
                            else "needs_confirmation" if original else "missing"
                        ),
                        source_references=sorted(
                            {o.source_reference for o in original}
                        ),
                        reason_codes=sorted(
                            {issue.code for o in original for issue in o.issues}
                        ),
                    )
                row["requirements"].append(requirement)
        output.append(row)
    return output
