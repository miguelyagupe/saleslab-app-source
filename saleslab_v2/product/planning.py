"""Only supported metrics are planned; ambiguous operands are never selected."""

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid5

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.enums import Capability, LanguageCode
from saleslab_v2.domain.idempotency import canonicalize, semantic_hash
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.normalization.financial import FinancialNormalizer
from saleslab_v2.pipeline.contracts import (
    FinancialRequest,
    InputRef,
    MetricStep,
    ReconciliationStep,
)
from saleslab_v2.pipeline.evaluator import evaluate
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .availability import metric_availability
from .capex import capex_availability, plan_capex
from .capital import (
    analyst_metric_keys,
    capital_availability,
    capital_candidates,
    plan_capital,
)
from .cash_reconciliation import plan_cash_reconciliation
from .contracts import VERSION
from .debt import debt_availability, plan_debt
from .growth import growth_availability, plan_growth, select_growth
from .ingestion import terms_for_plan
from .preview import metric_preview
from .profitability import plan_profitability, profitability_availability
from .roic import plan_roic, roic_availability
from .row_preview import row_preview
from .validation import validation_preview
from .working_capital import (
    plan_working_capital,
    working_capital_availability,
    working_capital_candidates,
)


def prepare_upload(parsed, choices, user_id, attempt_key):
    batch = parsed.batch
    observations = FinancialNormalizer().normalize(list(batch.rows), batch.metadata)
    periods = {
        o.period.label
        for o in observations
        if o.period and o.period.comparison_basis == "current"
    }
    target = choices.target_period or (next(iter(periods)) if len(periods) == 1 else "")
    valid = [
        o
        for o in observations
        if o.normalization_status == "normalized"
        and not o.issues
        and o.value is not None
    ]

    def candidates(concept, *, prior=False, include_invalid=False):
        return [
            o
            for o in (observations if include_invalid else valid)
            if o.concept_id == concept
            and o.period
            and (
                o.period.label == target and o.period.comparison_basis == "current"
                if not prior
                else o.period.comparison_basis == "comparative"
            )
        ]

    def ref(value):
        return InputRef(value.source_reference, batch.document.source_id)

    metrics, reconciliations = [], []
    revenue = candidates("revenue")
    income = candidates("net_income")
    cfo = candidates("cfo")

    def prior_revenue(*, include_invalid=False):
        if len(revenue) != 1:
            return []
        return [
            o
            for o in candidates("revenue", prior=True, include_invalid=include_invalid)
            if o.period.start_date.year == revenue[0].period.start_date.year - 1
            and o.period.start_date.month == revenue[0].period.start_date.month
            and o.period.granularity == revenue[0].period.granularity
        ]

    if len(revenue) == 1:
        metrics.append(
            MetricStep("revenue", "revenue", (("reported_revenue", ref(revenue[0])),))
        )
        if len(income) == 1:
            metrics.append(
                MetricStep(
                    "net_margin",
                    "net_margin_pct",
                    (("net_income", ref(income[0])), ("revenue", ref(revenue[0]))),
                )
            )
        if choices.planning_version in {
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
            "12",
            "13",
        }:
            ebit = candidates("ebit")
            if len(ebit) == 1:
                metrics.append(
                    MetricStep(
                        "ebit_margin",
                        "ebit_margin_pct",
                        (
                            ("confirmed_ebit", ref(ebit[0])),
                            ("revenue", ref(revenue[0])),
                        ),
                    )
                )
        prior = prior_revenue()
        if len(prior) == 1:
            metrics.append(
                MetricStep(
                    "revenue_growth",
                    "revenue_growth_reported_pct",
                    (
                        ("revenue_current", ref(revenue[0])),
                        ("revenue_prior", ref(prior[0])),
                    ),
                )
            )
    if len(cfo) == 1:
        metrics.append(MetricStep("cfo", "cfo", (("reported_cfo", ref(cfo[0])),)))
        if choices.planning_version in {
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
            "12",
            "13",
        }:
            capex = candidates("capex")
            if len(capex) == 1:
                metrics.append(
                    MetricStep(
                        "free_cash_flow",
                        "free_cash_flow",
                        (
                            ("cfo", ref(cfo[0])),
                            ("reported_capex_cash_outflow_negative", ref(capex[0])),
                        ),
                        version="2",
                    )
                )
    if choices.planning_version in {
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
    }:
        for concept in ("cfi", "cff"):
            flows = candidates(concept)
            if len(flows) == 1:
                metrics.append(
                    MetricStep(
                        concept, concept, ((f"reported_{concept}", ref(flows[0])),)
                    )
                )
    if choices.planning_version in {"5", "6", "7", "8", "9", "10", "11", "12", "13"}:
        current_assets = candidates("current_assets")
        current_liabilities = candidates("current_liabilities")
        if len(current_assets) == len(current_liabilities) == 1:
            metrics.append(
                MetricStep(
                    "current_ratio",
                    "current_ratio",
                    (
                        ("current_assets", ref(current_assets[0])),
                        ("current_liabilities", ref(current_liabilities[0])),
                    ),
                    version=(
                        "2"
                        if choices.planning_version
                        in {"8", "9", "10", "11", "12", "13"}
                        else "1"
                    ),
                )
            )
    balance = {
        role: candidates(concept)
        for role, concept in (
            ("assets", "total_assets"),
            ("liabilities", "total_liabilities"),
            ("equity", "equity"),
        )
    }
    if all(len(values) == 1 for values in balance.values()):
        currency = balance["assets"][0].currency
        tolerance = Decimal(choices.rounding_tolerance)
        policy = TolerancePolicy(
            "upload-balance-explicit",
            "1",
            "monetary",
            currency,
            "raw",
            tolerance,
            tolerance,
            tolerance,
            Decimal(0),
            Decimal(0),
            "User-confirmed absolute rounding allowance in base currency units; larger differences block",
        )
        reconciliations.append(
            ReconciliationStep(
                "REC-BS-001", tuple((r, ref(v[0])) for r, v in balance.items()), policy
            )
        )
    cash_availability = None
    if choices.planning_version in {"6", "7", "8", "9", "10", "11", "12", "13"}:
        extra_metrics, cash_step, cash_availability = plan_cash_reconciliation(
            observations, target, choices, ref
        )
        metrics.extend(extra_metrics)
        if cash_step is not None:
            reconciliations.append(cash_step)
    if choices.planning_version in {"7", "8", "9", "10", "11", "12", "13"}:
        profit_metrics, profit_checks = plan_profitability(choices, candidates, ref)
        metrics.extend(profit_metrics)
        reconciliations.extend(profit_checks)
    capital_select = None
    if choices.planning_version in {"8", "9", "10", "11", "12", "13"}:
        capital_select = capital_candidates(observations, target, candidates)
        capital_metrics, capital_checks = plan_capital(
            choices, capital_select, ref, metrics
        )
        metrics.extend(capital_metrics)
        reconciliations.extend(capital_checks)
    growth_select = None
    if choices.planning_version in {"9", "10", "11", "12", "13"}:
        growth_select = select_growth(observations, target)
        growth_metrics, growth_checks = plan_growth(choices, growth_select, ref)
        metrics.extend(growth_metrics)
        reconciliations.extend(growth_checks)
    debt_select = None
    if choices.planning_version in {"10", "11", "12", "13"}:
        debt_select = select_growth(observations, target)
        debt_metrics, debt_checks = plan_debt(choices, debt_select, ref)
        metrics.extend(debt_metrics)
        reconciliations.extend(debt_checks)
    capex_select = None
    if choices.planning_version in {"11", "12", "13"}:
        capex_select = select_growth(observations, target)
        capex_metrics, capex_checks = plan_capex(choices, capex_select, ref)
        metrics.extend(capex_metrics)
        reconciliations.extend(capex_checks)
    if choices.planning_version in {"12", "13"}:
        metrics.extend(plan_roic(candidates, ref))
    wc_select = None
    if choices.planning_version == "13":
        wc_select = working_capital_candidates(observations, target, candidates)
        wc_metrics, wc_checks = plan_working_capital(choices, wc_select, ref)
        metrics.extend(wc_metrics)
        reconciliations.extend(wc_checks)
    workspace_id = batch.document.workspace_id
    request = FinancialRequest(
        workspace_id,
        user_id,
        attempt_key,
        LanguageContext(
            (LanguageCode(choices.source_language),),
            LanguageCode.EN,
            LanguageCode(choices.report_language),
            LocaleTag(choices.format_locale),
        ),
        (batch,),
        tuple(metrics),
        tuple(reconciliations),
        (
            (Capability.CALCULATE, Capability.COMPARE)
            if choices.planning_version in {"9", "10", "11", "12", "13"}
            and any(
                step.metric_id
                in {
                    "revenue_growth_reported_pct",
                    "revenue_growth_cc_pct",
                    "revenue_growth_lfl_pct",
                }
                for step in metrics
            )
            else (Capability.CALCULATE,)
        ),
        analyst_metric_keys=(
            analyst_metric_keys(metrics)
            if choices.planning_version in {"8", "9", "10", "11", "12", "13"}
            else None
        ),
    )
    evaluated = evaluate(
        request,
        uuid5(workspace_id, "preview:" + request.fingerprint),
        datetime.now(UTC),
    )
    summary = {
        "schema": VERSION,
        "planning_version": choices.planning_version,
        "filename": batch.document.filename,
        "source_hash": batch.document.content_hash,
        "target_period": target,
        "periods": sorted(periods),
        "ready": bool(
            target and metrics and evaluated.ready and request.analyst_metric_keys != ()
        ),
        "rows": [
            row_preview(
                o,
                next(
                    r
                    for r in parsed.original_rows
                    if r["location"] == o.source_reference
                ),
                choices.report_language,
            )
            for o in evaluated.normalized
        ],
        "issues": unpack(canonicalize([asdict(i) for i in evaluated.issues])),
        "quality": unpack(canonicalize(evaluated.payloads[3])),
        "planned_metrics": [s.metric_id for s in metrics],
        "calculated_metrics": metric_preview(
            request, evaluated, choices.report_language, parsed.original_rows
        ),
        "validation_summary": validation_preview(
            request, evaluated, choices.report_language, parsed.original_rows
        ),
        "mapping_options": [
            {
                "concept_id": t.concept_id,
                "label": t.labels[LanguageCode(choices.source_language)],
                "statement_type": t.statement_type,
            }
            for t in terms_for_plan(choices.planning_version).values()
        ],
    }
    if parsed.import_manifest is not None:
        summary["source_import"] = parsed.import_manifest
    summary["metric_availability"] = metric_availability(
        choices, target, summary["calculated_metrics"], candidates, prior_revenue
    )
    if cash_availability is not None:
        actual = next(
            (
                r
                for r in summary["validation_summary"]["reconciliations"]
                if r["rule_id"] == "REC-CF-001"
            ),
            None,
        )
        if actual is not None:
            cash_availability["status"] = actual["status"]
            cash_availability["reason_codes"] = actual["reason_codes"]
        summary["cash_reconciliation"] = cash_availability
    if choices.planning_version in {"7", "8", "9", "10", "11", "12", "13"}:
        available, gaps = profitability_availability(
            choices,
            target,
            summary["calculated_metrics"],
            summary["validation_summary"],
            candidates,
        )
        summary["metric_availability"].extend(available)
        summary["profitability_reconciliations"] = gaps
    if capital_select is not None:
        available, gaps = capital_availability(
            choices,
            target,
            summary["calculated_metrics"],
            summary["validation_summary"],
            capital_select,
        )
        summary["metric_availability"].extend(available)
        summary["capital_reconciliations"] = gaps
    if growth_select is not None:
        available, gaps = growth_availability(
            choices,
            target,
            summary["calculated_metrics"],
            summary["validation_summary"],
            growth_select,
        )
        summary["metric_availability"].extend(available)
        summary["growth_reconciliations"] = gaps
    if debt_select is not None:
        available, gaps = debt_availability(
            choices,
            summary["calculated_metrics"],
            summary["validation_summary"],
            debt_select,
        )
        summary["metric_availability"].extend(available)
        summary["debt_reconciliations"] = gaps
    if capex_select is not None:
        available, gaps = capex_availability(
            choices,
            summary["calculated_metrics"],
            summary["validation_summary"],
            capex_select,
        )
        summary["metric_availability"].extend(available)
        summary["capex_reconciliations"] = gaps
    if choices.planning_version in {"12", "13"}:
        summary["metric_availability"].append(
            roic_availability(choices, summary["calculated_metrics"], candidates)
        )
    if wc_select is not None:
        available, gaps = working_capital_availability(
            choices,
            summary["calculated_metrics"],
            summary["validation_summary"],
            wc_select,
        )
        summary["metric_availability"].extend(available)
        summary["working_capital_reconciliations"] = gaps
    # Exclude generated UUIDs and operational timestamps from the preview contract.
    summary["preview_hash"] = semantic_hash(
        {
            "source": batch.document.content_hash,
            "filename": batch.document.filename,
            "choices": {
                **choices.contract_payload(exclude={"preview_hash", "attempt_key"}),
                "target_period": target,
            },
            "request": request.semantic_payload(),
            "version": VERSION,
            "mappings": parsed.decisions,
        }
    )
    return request, summary
