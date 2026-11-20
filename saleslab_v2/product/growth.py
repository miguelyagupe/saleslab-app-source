"""Plan distinct monetary growth bases and a signed contribution bridge."""

from decimal import Decimal

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.pipeline.contracts import InputRef, MetricStep, ReconciliationStep
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .growth_source import BASES, CONTRIBUTIONS
from .ingestion import terms_for_plan
from .preview import LABELS

SPECS = (
    (
        "revenue_growth_cc_pct",
        BASES[0],
        ("constant_currency_current", "constant_currency_prior"),
    ),
    (
        "revenue_growth_lfl_pct",
        BASES[1],
        ("like_for_like_current", "like_for_like_prior"),
    ),
    ("revenue_change_reported", "revenue", ("revenue_current", "revenue_prior")),
)


def select_growth(observations, target):
    def select(concept, prior=False, invalid=False):
        current = [
            o
            for o in observations
            if o.concept_id == concept
            and o.period
            and o.period.label == target
            and o.period.comparison_basis == "current"
        ]
        values = [
            o
            for o in observations
            if o.concept_id == concept
            and o.period
            and (
                o.period.comparison_basis == "comparative"
                and len(current) == 1
                and o.period.start_date.year == current[0].period.start_date.year - 1
                and o.period.start_date.month == current[0].period.start_date.month
                and o.period.granularity == current[0].period.granularity
                if prior
                else o.period.label == target and o.period.comparison_basis == "current"
            )
        ]
        return (
            values
            if invalid
            else [
                o
                for o in values
                if o.normalization_status == "normalized"
                and not o.issues
                and o.value is not None
            ]
        )

    return select


def plan_growth(choices, select, ref):
    metrics, checks = [], []
    for key, concept, roles in SPECS:
        current, prior = select(concept), select(concept, True)
        if (
            len(current)
            == len(prior)
            == len(select(concept, invalid=True))
            == len(select(concept, True, True))
            == 1
        ):
            metrics.append(
                MetricStep(
                    key, key, ((roles[0], ref(current[0])), (roles[1], ref(prior[0])))
                )
            )
    for concept in CONTRIBUTIONS:
        values = select(concept)
        if len(values) == len(select(concept, invalid=True)) == 1:
            metrics.append(
                MetricStep(concept, concept, (("reported_" + concept, ref(values[0])),))
            )
    selected = {m.key for m in metrics}
    if {*CONTRIBUTIONS, "revenue_change_reported"} <= selected:
        amount = Decimal(choices.growth_rounding_tolerance or "0")
        policy = TolerancePolicy(
            "upload-growth-monetary-explicit",
            "1",
            "monetary",
            select("revenue")[0].currency,
            "raw",
            amount,
            amount,
            amount,
            Decimal(0),
            Decimal(0),
            "User-confirmed absolute rounding allowance in base currency units for the reported monetary revenue bridge",
        )
        checks.append(
            ReconciliationStep(
                "REC-GROWTH-001",
                tuple(
                    (role, InputRef(key))
                    for role, key in zip(
                        ("lfl", "expansion_acquisition", "fx", "other", "reported"),
                        (*CONTRIBUTIONS, "revenue_change_reported"),
                    )
                ),
                policy,
                version="2",
            )
        )
    return metrics, checks


def growth_availability(choices, target, calculated, validation, select):
    results = {m["key"]: m for m in calculated}
    index = ("pt", "en", "es", "fr").index(choices.report_language)
    terms = terms_for_plan(choices.planning_version)
    rows = []
    for key, concept, _ in (*SPECS, *((c, c, None) for c in CONTRIBUTIONS)):
        requirements = []
        for prior in ((False, True) if key not in CONTRIBUTIONS else (False,)):
            candidates = select(concept, prior, True)
            valid = select(concept, prior)
            if len(candidates) == len(valid) == 1:
                continue
            requirements.append(
                {
                    "role": concept + ("_prior" if prior else "_current"),
                    "concept_id": concept,
                    "label": terms[concept].labels[
                        LanguageCode(choices.report_language)
                    ]
                    + (
                        (
                            " · comparativo",
                            " · prior",
                            " · comparativo",
                            " · antérieur",
                        )[index]
                        if prior
                        else (" · atual", " · current", " · actual", " · actuel")[index]
                    ),
                    "status": (
                        "ambiguous"
                        if len(candidates) > 1
                        else "needs_confirmation" if candidates else "missing"
                    ),
                    "source_references": sorted(o.source_reference for o in candidates),
                    "reason_codes": sorted(
                        {i.code for o in candidates for i in o.issues}
                    ),
                }
            )
        result = results.get(key)
        rows.append(
            {
                "key": key,
                "metric_id": key,
                "label": LABELS[key][index],
                "status": (
                    ("calculated" if result["status"] == "calculated" else "blocked")
                    if result
                    else "not_planned" if target else "period_required"
                ),
                "requirements": requirements if not result else [],
                "reason_codes": result["reason_codes"] if result else [],
            }
        )
    actual = next(
        (r for r in validation["reconciliations"] if r["rule_id"] == "REC-GROWTH-001"),
        None,
    )
    gap = {
        "rule_id": "REC-GROWTH-001",
        "status": actual["status"] if actual else "not_planned",
        "requirements": [
            r
            for m in rows
            if m["key"] in {*CONTRIBUTIONS, "revenue_change_reported"}
            for r in m["requirements"]
        ],
        "reason_codes": actual["reason_codes"] if actual else [],
    }
    return rows, [gap]
