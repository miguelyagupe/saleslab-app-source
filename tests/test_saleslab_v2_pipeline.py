from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from pipeline_fixtures import change_row, request, with_growth
from saleslab_v2.domain.enums import Capability, WorkflowStatus
from saleslab_v2.pipeline.contracts import InputRef, MetricStep, SourceBatch
from saleslab_v2.pipeline.evaluator import evaluate

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def test_normalization_metrics_reconciliation_and_all_nine_gates_are_connected():
    result = evaluate(request(), uuid4(), NOW)
    assert result.ready
    assert result.workflow_status == WorkflowStatus.CALCULATING
    assert len(result.normalized) == 5
    assert result.metrics[0][1].value == Decimal("10")
    assert result.reconciliations[0].status == "pass"
    decisions = {d.capability: d for d in result.decisions}
    assert set(decisions) == set(Capability)
    assert decisions[Capability.CALCULATE].allowed
    for cap in (
        Capability.FORECAST,
        Capability.QUANTIFY_IMPACT,
        Capability.RECOMMEND_MATERIAL,
    ):
        assert not decisions[cap].allowed
    # A balance-sheet identity cannot validate an unrelated income statement.
    assert not decisions[Capability.SUMMARIZE].allowed


def test_summary_requires_the_actual_statement_context_to_reconcile():
    value = request()
    batch = value.sources[0]
    balance = replace(
        batch, rows=tuple(r for r in batch.rows if r.statement_type == "balance_sheet")
    )
    result = evaluate(
        replace(
            value,
            sources=(balance,),
            metrics=(),
            requested_capabilities=(Capability.SUMMARIZE,),
        ),
        uuid4(),
        NOW,
    )
    assert result.ready
    assert "validated_statement" in result.coverage.present_inputs


@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
def test_all_four_source_languages_produce_the_same_numbers_and_gate_statuses(language):
    result = evaluate(request(language=language), uuid4(), NOW)
    baseline = evaluate(request(), uuid4(), NOW)
    assert result.metrics[0][1].value == baseline.metrics[0][1].value
    assert result.reconciliations[0].status == baseline.reconciliations[0].status
    assert [(d.capability, d.decision_status) for d in result.decisions] == [
        (d.capability, d.decision_status) for d in baseline.decisions
    ]


@pytest.mark.parametrize(
    "scale,expected",
    (("raw", "1000"), ("thousands", "1000000"), ("millions", "1000000000")),
)
def test_units_cross_the_boundary_as_base_values_without_double_scaling(
    scale, expected
):
    value = request(scale=scale)
    result = evaluate(value, uuid4(), NOW)
    assert next(
        o.value for o in result.normalized if o.concept_id == "revenue"
    ) == Decimal(expected)
    assert result.metrics[0][1].value == Decimal("10")
    assert result.reconciliations[0].status == "pass"
    assert result.reconciliations[0].policy_payload["scale"] == "raw"
    assert {r["scale"] for r in result.reconciliations[0].input_references} == {"raw"}
    assert {o.scale for o in result.normalized} == {scale}


def test_dependency_graph_is_topological_and_uses_calculated_provenance():
    value = request()
    sid = value.sources[0].document.source_id
    revenue = MetricStep(
        "z_revenue", "revenue", (("reported_revenue", InputRef("revenue", sid)),)
    )
    margin = replace(
        value.metrics[0],
        inputs=(
            ("net_income", InputRef("net_income", sid)),
            ("revenue", InputRef("z_revenue")),
        ),
    )
    result = evaluate(replace(value, metrics=(margin, revenue)), uuid4(), NOW)
    assert [key for key, _, _ in result.metrics] == ["z_revenue", "margin"]
    assert result.metrics[-1][1].value == Decimal("10")
    assert (
        result.metrics[-1][2].inputs[1].observation_id
        == result.metrics[0][1].observation_id
    )


@pytest.mark.parametrize(
    "reference,changes,expected_code",
    (
        ("revenue", {"text": "0"}, "pipeline.metric_invalid"),
        ("assets", {"text": "200"}, "pipeline.reconciliation_blocked"),
        ("revenue", {"name": "unknown field"}, "pipeline.mapping_required"),
        ("net_income", {"text": ""}, "pipeline.metric_invalid"),
    ),
)
def test_invalid_data_cannot_reach_a_ready_checkpoint(
    reference, changes, expected_code
):
    result = evaluate(change_row(request(), reference, **changes), uuid4(), NOW)
    assert not result.ready
    assert expected_code in {i.code for i in result.issues}
    assert all(not d.allowed for d in result.decisions)


def test_unresolved_mapping_stops_metric_and_reconciliation_execution():
    result = evaluate(change_row(request(), "revenue", name="unknown"), uuid4(), NOW)
    assert result.workflow_status == WorkflowStatus.MAPPING_REQUIRED
    assert not result.metrics and not result.reconciliations
    assert not result.payloads[1]["executed"] and not result.payloads[2]["executed"]


def test_growth_uses_temporal_evidence_from_raw_and_calculated_revenue():
    result = evaluate(with_growth(request()), uuid4(), NOW)
    assert result.ready
    growth = next(
        o for _, o, _ in result.metrics if o.metric_id == "revenue_growth_reported_pct"
    )
    assert growth.value == Decimal("25")
    assert next(
        d for d in result.decisions if d.capability == Capability.COMPARE
    ).allowed


@pytest.mark.parametrize(
    "changes", ({"comparison_basis": "current"}, {"period_label": "2024"})
)
def test_growth_cannot_relabel_an_incompatible_prior_period(changes):
    value = change_row(with_growth(request()), "revenue_prior", **changes)
    result = evaluate(value, uuid4(), NOW)
    assert not result.ready
    assert "pipeline.metric_invalid" in {i.code for i in result.issues}
    assert "two_comparable_periods" not in result.coverage.present_inputs


def test_missing_bound_rows_are_audited_as_missing_data():
    value = request()
    batch = value.sources[0]
    value = replace(
        value,
        sources=(
            replace(
                batch,
                rows=tuple(r for r in batch.rows if r.source_reference != "equity"),
            ),
        ),
    )
    result = evaluate(value, uuid4(), NOW)
    assert not result.ready
    assert "pipeline.reconciliation_input_missing" in {i.code for i in result.issues}
    # A failed rule has no ReconciliationResult: its original bindings and
    # tolerance policy must still be retained so the failure is reproducible.
    plan = result.payloads[0]["request"]["reconciliations"][0]
    assert dict(plan["inputs"])["equity"]["key"] == "equity"
    assert plan["policy"]["scale"] == "raw"


def test_binding_an_unrelated_concept_cannot_impersonate_a_metric_operand():
    value = request()
    metric = replace(
        value.metrics[0],
        inputs=(
            ("net_income", InputRef("equity", value.sources[0].document.source_id)),
            ("revenue", InputRef("revenue", value.sources[0].document.source_id)),
        ),
    )
    result = evaluate(replace(value, metrics=(metric,)), uuid4(), NOW)
    assert not result.ready and not result.metrics
    assert result.issues[0].code == "pipeline.metric_invalid"


def test_duplicate_candidates_across_different_sources_require_confirmation():
    value = request()
    first = value.sources[0]
    sid = uuid4()
    document = replace(first.document, source_id=sid)
    second = SourceBatch(
        document, replace(first.metadata, source_document_id=sid), first.rows
    )
    result = evaluate(replace(value, sources=(first, second)), uuid4(), NOW)
    assert result.workflow_status == WorkflowStatus.MAPPING_REQUIRED
    assert "pipeline.duplicate_candidate" in {i.code for i in result.issues}


def test_semantic_request_canonicalizes_order_and_hashes_every_execution_option():
    value = request()
    batch = value.sources[0]
    assert (
        replace(
            value, sources=(replace(batch, rows=tuple(reversed(batch.rows))),)
        ).fingerprint
        == value.fingerprint
    )
    assert (
        replace(
            value,
            language=replace(
                value.language, format_locale=value.language.format_locale
            ),
        ).fingerprint
        == value.fingerprint
    )
    assert change_row(value, "net_income", text="101").fingerprint != value.fingerprint
    assert replace(value, profile="financial_hedge_v1").fingerprint != value.fingerprint
    assert (
        replace(value, requested_capabilities=(Capability.FORECAST,)).fingerprint
        != value.fingerprint
    )
    assert (
        replace(
            value,
            reconciliations=(
                replace(
                    value.reconciliations[0],
                    policy=replace(
                        value.reconciliations[0].policy, critical_absolute=Decimal("2")
                    ),
                ),
            ),
        ).fingerprint
        != value.fingerprint
    )


def test_repeat_evaluation_for_the_same_run_is_identical():
    value, run_id = request(), uuid4()
    first, second = evaluate(value, run_id, NOW), evaluate(value, run_id, NOW)
    assert first.payloads == second.payloads
    assert first.issues == second.issues
    assert first.decisions == second.decisions


def test_transport_snapshot_revalidates_nested_metadata_before_any_io():
    value = request()
    object.__setattr__(value.sources[0].metadata, "source_document_id", uuid4())
    with pytest.raises(ValueError, match="metadata disagree"):
        value.snapshot()


def test_unsupported_bindings_versions_and_source_mismatches_fail_before_execution():
    value = request()
    with pytest.raises(ValueError, match="outside the request"):
        replace(
            value,
            metrics=(
                replace(
                    value.metrics[0],
                    inputs=(
                        ("net_income", InputRef("x", uuid4())),
                        ("revenue", InputRef("y", uuid4())),
                    ),
                ),
            ),
        )
    with pytest.raises(KeyError):
        replace(value.metrics[0], version="unknown")
    with pytest.raises(ValueError, match="metadata disagree"):
        replace(
            value.sources[0],
            metadata=replace(value.sources[0].metadata, content_hash="b" * 64),
        )
    with pytest.raises(ValueError, match="cyclic"):
        replace(
            value,
            metrics=(
                replace(
                    value.metrics[0],
                    inputs=(
                        ("net_income", InputRef("margin")),
                        ("revenue", InputRef("margin")),
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError, match="unsupported financial profile"):
        replace(value, profile="unknown")
