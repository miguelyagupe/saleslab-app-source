"""Plan a cash bridge only from explicit, unique and temporally anchored inputs."""

from decimal import Decimal

from saleslab_v2.pipeline.contracts import InputRef, MetricStep, ReconciliationStep
from saleslab_v2.reconciliation.contracts import TolerancePolicy

LABELS = {
    "opening_cash": (
        "Caixa inicial",
        "Opening cash",
        "Efectivo inicial",
        "Trésorerie initiale",
    ),
    "closing_cash": (
        "Caixa final",
        "Closing cash",
        "Efectivo final",
        "Trésorerie finale",
    ),
    "cfo": (
        "Fluxo operacional",
        "Operating cash flow",
        "Flujo operativo",
        "Flux opérationnel",
    ),
    "cfi": (
        "Fluxo de investimento",
        "Investing cash flow",
        "Flujo de inversión",
        "Flux d’investissement",
    ),
    "cff": (
        "Fluxo de financiamento",
        "Financing cash flow",
        "Flujo de financiación",
        "Flux de financement",
    ),
    "fx_other": (
        "Efeitos cambiais e outros movimentos",
        "FX and other movements",
        "Cambio y otros movimientos",
        "Change et autres mouvements",
    ),
}


def plan_cash_reconciliation(observations, target, choices, ref):
    def usable(o):
        return (
            o.normalization_status == "normalized"
            and not o.issues
            and o.value is not None
        )

    def current(concept):
        return [
            o
            for o in observations
            if o.concept_id == concept
            and o.period
            and o.period.label == target
            and o.period.comparison_basis == "current"
        ]

    rows = {
        r: current(c)
        for r, c in (
            ("cfo", "cfo"),
            ("cfi", "cfi"),
            ("cff", "cff"),
            ("fx_other", "cash_fx_other"),
        )
    }
    metrics = []
    fx = rows["fx_other"]
    if len(fx) == 1 and usable(fx[0]):
        metrics.append(
            MetricStep(
                "cash_fx_other",
                "cash_fx_other",
                (("reported_cash_fx_other", ref(fx[0])),),
            )
        )
    # A unique, valid operating flow supplies the actual interval; labels alone do not.
    anchor = (
        rows["cfo"][0].period
        if len(rows["cfo"]) == 1 and usable(rows["cfo"][0])
        else None
    )
    stocks = [
        o
        for o in observations
        if o.concept_id == "cash_and_equivalents"
        and o.period
        and o.period.comparison_basis == "current"
    ]
    for role in ("opening_cash", "closing_cash"):
        rows[role] = []
        if anchor is not None:
            for o in stocks:
                p = o.period
                if (p.granularity, p.comparison_basis, p.fiscal_year_end_month) != (
                    anchor.granularity,
                    anchor.comparison_basis,
                    anchor.fiscal_year_end_month,
                ):
                    continue
                if role == "opening_cash":
                    matches = (
                        p.end_date.toordinal() + 1 == anchor.start_date.toordinal()
                    )
                    if anchor.fiscal_year is not None:
                        matches = matches and p.fiscal_year == anchor.fiscal_year - 1
                else:
                    matches = (
                        p.end_date == anchor.end_date
                        and p.fiscal_year == anchor.fiscal_year
                    )
                if matches:
                    rows[role].append(o)
    availability = {
        "rule_id": "REC-CF-001",
        "status": "not_planned",
        "requirements": [],
        "reason_codes": [],
    }
    if not target:
        availability.update(
            status="period_required", reason_codes=["upload.target_period_required"]
        )
    elif anchor is None:
        availability["reason_codes"] = ["upload.cash_flow_period_required"]
    index = ("pt", "en", "es", "fr").index(choices.report_language)
    for role in LABELS:
        candidates = rows[role]
        if len(candidates) == 1 and usable(candidates[0]):
            continue
        availability["requirements"].append(
            {
                "role": role,
                "label": LABELS[role][index],
                "status": (
                    "ambiguous"
                    if len(candidates) > 1
                    else "needs_confirmation" if candidates else "missing"
                ),
                "source_references": sorted({o.source_reference for o in candidates}),
                "reason_codes": sorted(
                    {issue.code for o in candidates for issue in o.issues}
                ),
            }
        )
    if availability["requirements"] or not target or anchor is None:
        return metrics, None, availability
    tolerance = Decimal(choices.cash_rounding_tolerance or "0")
    policy = TolerancePolicy(
        "upload-cash-explicit",
        "1",
        "monetary",
        rows["cfo"][0].currency,
        "raw",
        tolerance,
        tolerance,
        tolerance,
        Decimal(0),
        Decimal(0),
        "User-confirmed absolute cash rounding allowance in base currency units; larger differences block",
    )
    inputs = tuple(
        (role, InputRef("cash_fx_other") if role == "fx_other" else ref(rows[role][0]))
        for role in LABELS
    )
    return metrics, ReconciliationStep("REC-CF-001", inputs, policy), availability
