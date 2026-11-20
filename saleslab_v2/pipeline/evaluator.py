"""Deterministic F-02 → F-05 execution. No database, models or network calls."""

from dataclasses import asdict, dataclass, replace
from uuid import uuid5

from saleslab_v2.domain.enums import Capability, Severity, WorkflowStatus
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.domain.quality import DataQualityIssue
from saleslab_v2.metrics.engine import MetricCalculationError, MetricEngine
from saleslab_v2.metrics.financial_v1 import FINANCIAL_V1
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.normalization.financial import FinancialNormalizer
from saleslab_v2.quality.capabilities import decide_capabilities
from saleslab_v2.quality.coverage import assess_coverage
from saleslab_v2.quality.issues import scoped_issue_id
from saleslab_v2.quality.requirements import requirement_for
from saleslab_v2.reconciliation.contracts import ReconciliationError
from saleslab_v2.reconciliation.engine import ReconciliationEngine
from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1

from .adapters import metric_input, reconciliation_input, scope_metric


@dataclass(frozen=True)
class Evaluation:
    normalized: tuple
    metrics: tuple
    reconciliations: tuple
    issues: tuple
    coverage: object
    decisions: tuple
    workflow_status: WorkflowStatus
    ready: bool
    payloads: tuple[dict, ...]


def evaluate(request, run_id, timestamp):
    normalizer = FinancialNormalizer()
    rows = {}
    normalized = []
    issues = []

    def issue(code, refs, *, capabilities=(), severity=Severity.BLOCKING):
        value = DataQualityIssue(
            code,
            severity,
            "pipeline",
            code,
            affected_refs=tuple(sorted(set(refs))),
            remediation_key="provide_core_financials",
            blocking_capabilities=capabilities,
        )
        issues.append(
            replace(
                value, issue_id=scoped_issue_id(request.workspace_id, run_id, value)
            )
        )

    for batch in request.sources:
        values = normalizer.normalize(list(batch.rows), batch.metadata)
        normalized.extend(values)
        rows.update(
            {
                (batch.document.source_id, row.source_reference): value
                for row, value in zip(batch.rows, values)
            }
        )
    normalized.sort(key=lambda o: (str(o.source_document_id), o.source_reference))
    candidates = {}
    for value in normalized:
        if value.concept_id and value.period:
            key = (
                value.concept_id,
                value.period.identity,
                value.scope,
                value.dimensions,
                value.currency,
                value.unit,
                value.statement_type,
            )
            candidates.setdefault(key, []).append(value)
        if value.normalization_status != "normalized":
            issue(
                "pipeline.mapping_required",
                (str(value.source_document_id) + ":" + value.source_reference,),
            )
    for values in candidates.values():
        if len(values) > 1:
            issue(
                "pipeline.duplicate_candidate",
                (str(v.source_document_id) + ":" + v.source_reference for v in values),
            )
    mapping_required = bool(issues)
    metrics, calculated, metric_errors = [], {}, []
    reconciliations, reconciliation_errors = [], []

    def resolve(ref):
        return (
            rows[(ref.source_id, ref.key)]
            if ref.source_id is not None
            else calculated[ref.key]
        )

    if not mapping_required:
        for step in request.ordered_metrics():
            definition = FINANCIAL_V1.get(step.metric_id, step.version)
            try:
                bindings = dict(step.inputs)
                inputs = {
                    c.role: metric_input(resolve(bindings[c.role]), c)
                    for c in definition.operand_contracts
                }
                observation, trace = MetricEngine(FINANCIAL_V1).calculate(
                    step.metric_id, inputs, step.version
                )
                observation = scope_metric(
                    request.workspace_id, run_id, observation, trace
                )
                metrics.append((step.key, observation, trace))
                calculated[step.key] = observation
            except (KeyError, MetricCalculationError) as exc:
                code = (
                    "pipeline.metric_input_missing"
                    if isinstance(exc, KeyError)
                    else "pipeline.metric_invalid"
                )
                metric_errors.append(
                    {"step": step.key, "code": code, "error_type": type(exc).__name__}
                )
                issue(code, (step.key,))
        for step in request.reconciliations:
            rule = FINANCIAL_RULES_V1.get(step.rule_id, step.version)
            requirements = {o.role: o for o in rule.operands}
            try:
                inputs = tuple(
                    reconciliation_input(role, resolve(ref), requirements[role])
                    for role, ref in step.inputs
                )
                result = ReconciliationEngine().evaluate(
                    rule,
                    inputs,
                    step.policy,
                    timestamp=timestamp,
                    workspace_id=request.workspace_id,
                    run_id=run_id,
                )
                reconciliations.append(result)
                if result.status in {"critical", "blocking"}:
                    issue("pipeline.reconciliation_blocked", (step.rule_id,))
                elif result.status == "warning":
                    issue(
                        "pipeline.reconciliation_warning",
                        (step.rule_id,),
                        severity=Severity.WARNING,
                    )
                if any(effect.blocked for effect in result.capability_effects):
                    issue(
                        "pipeline.causal_evidence_required",
                        (step.rule_id,),
                        capabilities=(Capability.INFER_CAUSE,),
                    )
            except (KeyError, ReconciliationError) as exc:
                code = (
                    "pipeline.reconciliation_input_missing"
                    if isinstance(exc, KeyError)
                    else "pipeline.reconciliation_invalid"
                )
                reconciliation_errors.append(
                    {
                        "rule": step.rule_id,
                        "code": code,
                        "error_type": type(exc).__name__,
                    }
                )
                issue(code, (step.rule_id,))

    present = set()
    valid = [
        o
        for o in normalized
        if o.normalization_status == "normalized" and o.value is not None
    ]
    if valid and not mapping_required:
        present.add("mapped_inputs")
        if all(o.period is not None for o in valid):
            present.add("identified_period")
        monetary = [o for o in valid if o.unit == "monetary"]
        if (
            monetary
            and all(o.currency for o in monetary)
            and len({o.currency for o in monetary}) == 1
        ):
            present.add("currency")
        if monetary and all(o.scale for o in monetary):
            present.add("scale")
        if len({(o.scope, o.dimensions) for o in valid}) == 1:
            present.add("scope")
        statement_rules = {
            "REC-BS-001": "balance_sheet",
            "REC-PL-001": "income_statement",
            "REC-CF-001": "cash_flow_statement",
        }
        validated = {
            (statement_rules[r.rule_id], r.period, r.scope, r.dimensions, r.currency)
            for r in reconciliations
            if r.rule_id in statement_rules and r.status in {"pass", "rounding_pass"}
        }
        if all(
            (o.statement_type, o.period.identity, o.scope, o.dimensions, o.currency)
            in validated
            for o in valid
        ):
            present.add("validated_statement")
    if metrics and not metric_errors:
        present.update(("metric_definition", "non_zero_denominator"))
    if any(
        o.metric_id
        in {
            "revenue_growth_reported_pct",
            "revenue_growth_cc_pct",
            "revenue_growth_lfl_pct",
        }
        for _, o, _ in metrics
    ):
        present.update(
            (
                "two_comparable_periods",
                "consistent_granularity",
                "consistent_scope",
                "comparison",
            )
        )
    required = tuple(
        sorted(
            {
                x
                for cap in request.requested_capabilities
                for x in requirement_for(cap, request.profile).required_inputs
            }
        )
    )
    # Keep evidence outside the requested minimum available to other capabilities
    # without fabricating coverage of evidence that this slice cannot produce.
    coverage = assess_coverage(
        run_id,
        tuple(sorted(set(required) | present)),
        tuple(sorted(present)),
        profile=request.profile,
    )
    coverage = replace(
        coverage,
        assessment_id=uuid5(TRACE_NAMESPACE, f"coverage:{run_id}:{request.profile}"),
        assessed_at=timestamp,
    )
    issues = tuple(
        sorted({i.issue_id: i for i in issues}.values(), key=lambda i: str(i.issue_id))
    )
    decisions = tuple(
        replace(
            d,
            decision_id=uuid5(
                TRACE_NAMESPACE,
                f"decision:{run_id}:{request.profile}:{d.capability.value}",
            ),
        )
        for d in decide_capabilities(run_id, coverage, issues)
    )
    ready = all(
        d.allowed for d in decisions if d.capability in request.requested_capabilities
    )
    status = (
        WorkflowStatus.MAPPING_REQUIRED
        if mapping_required
        else WorkflowStatus.CALCULATING if ready else WorkflowStatus.BLOCKED
    )
    payloads = (
        {
            "request": canonicalize(request.semantic_payload()),
            "observations": [canonicalize(asdict(o)) for o in normalized],
            "mapping_required": mapping_required,
        },
        {
            "executed": not mapping_required,
            "metrics": [
                {"key": key, "observation": canonicalize(asdict(o)), "trace": t.payload}
                for key, o, t in metrics
            ],
            "errors": metric_errors,
        },
        {
            "executed": not mapping_required,
            "results": [r.payload() for r in reconciliations],
            "errors": reconciliation_errors,
        },
        {
            "coverage": coverage.to_dict(),
            "issues": [canonicalize(asdict(i)) for i in issues],
            "decisions": [canonicalize(asdict(d)) for d in decisions],
        },
    )
    return Evaluation(
        tuple(normalized),
        tuple(metrics),
        tuple(reconciliations),
        issues,
        coverage,
        decisions,
        status,
        ready,
        payloads,
    )
