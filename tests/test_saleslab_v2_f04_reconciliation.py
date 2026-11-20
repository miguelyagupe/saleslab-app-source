from datetime import datetime, timezone
from decimal import Decimal, getcontext
from uuid import uuid4

import pytest

from saleslab_v2.domain.financial import NormalizedObservation
from saleslab_v2.domain.value_objects import Period
from saleslab_v2.i18n.financial_v8 import FINANCIAL_TERMS_V8
from saleslab_v2.metrics import FINANCIAL_V1
from saleslab_v2.reconciliation import (
    FINANCIAL_RULES_V1,
    CapabilityEvidence,
    Equation,
    InputContractError,
    PolicyRequiredError,
    ReconciliationEngine,
    ReconciliationInput,
    ReconciliationRule,
    RelativeDeltaKind,
    TolerancePolicy,
)
from saleslab_v2.reconciliation.contracts import OperandRequirement
from saleslab_v2.reconciliation.financial_v1 import RAW_BRIDGE_IDENTITIES_V1

NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)
PERIOD = ("2026-01-01", "2026-12-31", "year", 2026, "current", 12)


def item(role, identity, value, *, variant=None, status="confirmed"):
    return ReconciliationInput(
        role,
        uuid4(),
        identity,
        Decimal(value),
        "monetary",
        "EUR",
        "millions",
        PERIOD,
        "consolidated",
        (),
        status,
        ("synthetic:1",),
        (),
        variant,
    )


def policy():
    return TolerancePolicy(
        "SYNTHETIC-EUR-M",
        "1",
        "monetary",
        "EUR",
        "millions",
        Decimal("0.1"),
        Decimal("1"),
        Decimal("5"),
        Decimal("0.01"),
        Decimal("0.05"),
        "synthetic-test-only",
    )


@pytest.mark.parametrize(
    ("assets", "status"),
    (
        ("100", "pass"),
        ("100.1", "rounding_pass"),
        ("100.5", "warning"),
        ("103", "critical"),
        ("120", "blocking"),
    ),
)
def test_all_five_auditable_states(assets, status):
    inputs = (
        item("liabilities", "total_liabilities", "60"),
        item("equity", "equity", "40"),
        item("assets", "total_assets", assets),
    )
    result = ReconciliationEngine().evaluate(
        FINANCIAL_RULES_V1.get("REC-BS-001"), inputs, policy(), timestamp=NOW
    )
    assert result.status == status
    assert len(result.result_hash) == len(result.input_fingerprint) == 64


def test_rejects_missing_duplicate_identity_and_policy():
    rule = FINANCIAL_RULES_V1.get("REC-BS-001")
    engine = ReconciliationEngine()
    valid = (
        item("liabilities", "total_liabilities", "60"),
        item("equity", "equity", "40"),
        item("assets", "total_assets", "100"),
    )
    with pytest.raises(PolicyRequiredError):
        engine.evaluate(rule, valid, None, timestamp=NOW)
    with pytest.raises(InputContractError, match="missing"):
        engine.evaluate(rule, valid[:-1], policy(), timestamp=NOW)
    with pytest.raises(InputContractError, match="duplicate"):
        engine.evaluate(rule, valid + (valid[-1],), policy(), timestamp=NOW)
    bad = valid[:-1] + (item("assets", "operating_profit", "100"),)
    with pytest.raises(InputContractError, match="identity"):
        engine.evaluate(rule, bad, policy(), timestamp=NOW)


def test_hash_ignores_decimal_context_and_presentation_language():
    rule = FINANCIAL_RULES_V1.get("REC-BS-001")
    inputs = (
        item("liabilities", "total_liabilities", "60"),
        item("equity", "equity", "40"),
        item("assets", "total_assets", "100.333333333333333333333333"),
    )
    getcontext().prec = 6
    first = ReconciliationEngine().evaluate(rule, inputs, policy(), timestamp=NOW)
    getcontext().prec = 50
    second = ReconciliationEngine().evaluate(rule, inputs, policy(), timestamp=NOW)
    assert first.result_hash == second.result_hash


def test_zero_expected_has_typed_undefined_relative_delta():
    inputs = (
        item("liabilities", "total_liabilities", "0"),
        item("equity", "equity", "0"),
        item("assets", "total_assets", "1"),
    )
    result = ReconciliationEngine().evaluate(
        FINANCIAL_RULES_V1.get("REC-BS-001"), inputs, policy(), timestamp=NOW
    )
    assert result.relative_delta is None
    assert result.relative_delta_kind is RelativeDeltaKind.ZERO_EXPECTED


def test_jm_fin_005_and_006_require_complete_typed_bridges():
    growth = FINANCIAL_RULES_V1.get("REC-GROWTH-001")
    lease = FINANCIAL_RULES_V1.get("REC-LEASE-001")
    with pytest.raises(InputContractError, match="missing"):
        ReconciliationEngine().evaluate(
            growth,
            (item("reported", "reported_growth", "5", variant="reported"),),
            policy(),
            timestamp=NOW,
        )
    with pytest.raises(InputContractError, match="missing"):
        ReconciliationEngine().evaluate(
            lease,
            (item("additions", "lease_additions", "535", variant="lease"),),
            policy(),
            timestamp=NOW,
        )


def test_catalog_has_all_stable_rules_and_no_policy_defaults():
    assert {rule.rule_id for rule in FINANCIAL_RULES_V1.rules} == {
        "REC-BS-001",
        "REC-CF-001",
        "REC-PL-001",
        "REC-EBITDA-001",
        "REC-DEBT-001",
        "REC-LEASE-001",
        "REC-GROWTH-001",
        "REC-CAPEX-001",
        "REC-CAPEX-002",
        "REC-DEBT-RATE-001",
        "REC-DEBT-MATURITY-001",
        "REC-WC-CLOSING-001",
        "REC-WC-OPENING-001",
        "REC-WC-CHANGE-001",
    }


def test_real_f02_balance_sheet_ids_and_unconfirmed_ebit_rejected():
    inputs = (
        item("liabilities", "total_liabilities", "60"),
        item("equity", "equity", "40"),
        item("assets", "total_assets", "100"),
    )
    assert (
        ReconciliationEngine()
        .evaluate(FINANCIAL_RULES_V1.get("REC-BS-001"), inputs, policy(), timestamp=NOW)
        .status
        == "pass"
    )
    pl = FINANCIAL_RULES_V1.get("REC-PL-001")
    assert (
        "operating_profit_reported_unconfirmed"
        not in next(o for o in pl.operands if o.role == "ebit").identities
    )


def test_growth_requires_canonical_percentage_contract():
    growth = FINANCIAL_RULES_V1.get("REC-GROWTH-001")
    inputs = tuple(
        item(role, identity, value, variant=variant)
        for role, identity, value, variant in (
            ("lfl", "growth_lfl_pct", "2", "growth_driver"),
            (
                "expansion_acquisition",
                "growth_expansion_acquisition_pct",
                "1",
                "growth_driver",
            ),
            ("fx", "growth_fx_pct", "1", "growth_driver"),
            ("other", "growth_other_pct", "1", "growth_driver"),
            ("reported", "revenue_growth_reported_pct", "5", "reported"),
        )
    )
    with pytest.raises(InputContractError, match="unit"):
        ReconciliationEngine().evaluate(growth, inputs, policy(), timestamp=NOW)


def test_timestamp_is_not_semantic_and_all_equations_are_retained():
    rule = FINANCIAL_RULES_V1.get("REC-BS-001")
    inputs = (
        item("liabilities", "total_liabilities", "60"),
        item("equity", "equity", "40"),
        item("assets", "total_assets", "100"),
    )
    first = ReconciliationEngine().evaluate(rule, inputs, policy(), timestamp=NOW)
    second = ReconciliationEngine().evaluate(
        rule, inputs, policy(), timestamp=datetime(2027, 1, 1, tzinfo=timezone.utc)
    )
    assert first.result_hash == second.result_hash
    assert len(first.equations) == 1


def test_real_normalized_observation_flows_from_f02_without_status_rewrite():
    def observation(concept, value):
        return NormalizedObservation(
            concept_id=concept,
            semantic_type="monetary_stock",
            value=Decimal(value),
            null_reason=None,
            currency="EUR",
            scale="millions",
            unit="monetary",
            sign="positive",
            expected_sign="contextual",
            period=Period(
                datetime(2026, 1, 1).date(),
                datetime(2026, 12, 31).date(),
                granularity="year",
                comparison_basis="current",
                fiscal_year=2026,
                fiscal_year_end_month=12,
            ),
            statement_type="balance_sheet",
            scope="consolidated",
            original_name=concept,
            original_text=value,
            source_document_id=uuid4(),
            source_content_hash="a" * 64,
            source_reference=f"document:{concept}",
            source_language="en",
            format_locale="en-GB",
            confidence=Decimal("1"),
        )

    inputs = tuple(
        ReconciliationInput.from_normalized_observation(
            role, uuid4(), observation(identity, value)
        )
        for role, identity, value in (
            ("liabilities", "total_liabilities", "60"),
            ("equity", "equity", "40"),
            ("assets", "total_assets", "100"),
        )
    )
    result = ReconciliationEngine().evaluate(
        FINANCIAL_RULES_V1.get("REC-BS-001"), inputs, policy(), timestamp=NOW
    )
    assert result.status == "pass"
    assert {ref["status"] for ref in result.input_references} == {"normalized"}


def test_optional_operand_referenced_by_equation_is_invalid_not_key_error():
    with pytest.raises(ValueError, match="optional"):
        ReconciliationRule(
            "REC-TEST-001",
            "1",
            "invalid optional arithmetic",
            (
                OperandRequirement("base", ("base",)),
                OperandRequirement("optional", ("optional",), required=False),
                OperandRequirement("total", ("total",)),
            ),
            (Equation((("base", 1), ("optional", 1)), "total"),),
        )


def test_causal_capabilities_stay_blocked_without_scoped_confirmed_evidence():
    workspace_id, run_id = uuid4(), uuid4()
    growth_policy = policy().__class__(
        **{**policy().__dict__, "unit": "percentage", "currency": None, "scale": "unit"}
    )

    def percentage(role, identity, value, variant):
        return ReconciliationInput(
            role,
            uuid4(),
            identity,
            Decimal(value),
            "percentage",
            None,
            "unit",
            PERIOD,
            "consolidated",
            (),
            "calculated",
            ("metric:1",),
            (),
            variant,
        )

    inputs = tuple(
        percentage(*values)
        for values in (
            ("lfl", "growth_lfl_pct", "2", "growth_driver"),
            (
                "expansion_acquisition",
                "growth_expansion_acquisition_pct",
                "1",
                "growth_driver",
            ),
            ("fx", "growth_fx_pct", "1", "growth_driver"),
            ("other", "growth_other_pct", "1", "growth_driver"),
            ("reported", "revenue_growth_reported_pct", "5", "reported"),
        )
    )
    rule = FINANCIAL_RULES_V1.get("REC-GROWTH-001")
    no_evidence = ReconciliationEngine().evaluate(
        rule, inputs, growth_policy, timestamp=NOW
    )
    assert no_evidence.status == "pass"
    assert no_evidence.capability_effects[0].blocked
    evidence = CapabilityEvidence(
        uuid4(),
        "call_growth_organic",
        "drivers_support_organic",
        (str(uuid4()),),
        "confirmed",
        workspace_id,
        run_id,
        PERIOD,
        (),
    )
    allowed = ReconciliationEngine().evaluate(
        rule,
        inputs,
        growth_policy,
        timestamp=NOW,
        capability_evidence=(evidence,),
        workspace_id=workspace_id,
        run_id=run_id,
    )
    assert not allowed.capability_effects[0].blocked
    with pytest.raises(InputContractError, match="outside workspace/run"):
        ReconciliationEngine().evaluate(
            rule,
            inputs,
            growth_policy,
            timestamp=NOW,
            capability_evidence=(evidence,),
            workspace_id=workspace_id,
            run_id=uuid4(),
        )
    warning = CapabilityEvidence(
        **{**evidence.__dict__, "status": "warning", "fingerprint": ""}
    )
    result = ReconciliationEngine().evaluate(
        rule,
        inputs,
        growth_policy,
        timestamp=NOW,
        capability_evidence=(warning,),
        workspace_id=workspace_id,
        run_id=run_id,
    )
    assert result.capability_effects[0].blocked


def test_lease_pass_needs_direct_expansion_evidence_to_unlock():
    workspace_id, run_id = uuid4(), uuid4()
    opening = ("2025-01-01", "2025-12-31", "year", 2025, "current", 12)

    def lease(role, identity, value, period, variant="lease_bridge"):
        return ReconciliationInput(
            role,
            uuid4(),
            identity,
            Decimal(value),
            "monetary",
            "EUR",
            "millions",
            period,
            "consolidated",
            (),
            "calculated",
            ("metric:1",),
            (),
            variant,
        )

    inputs = (
        lease("opening", "lease_liabilities", "100", opening),
        lease("additions", "lease_additions", "535", PERIOD),
        lease("modifications", "lease_modifications", "0", PERIOD),
        lease("interest", "lease_interest", "10", PERIOD),
        lease("payments", "lease_payments", "45", PERIOD),
        lease("fx_other", "lease_fx_other", "0", PERIOD),
        lease("closing", "lease_liabilities", "600", PERIOD),
    )
    rule = FINANCIAL_RULES_V1.get("REC-LEASE-001")
    without = ReconciliationEngine().evaluate(rule, inputs, policy(), timestamp=NOW)
    assert without.status == "pass" and without.capability_effects[0].blocked
    evidence = CapabilityEvidence(
        uuid4(),
        "attribute_leasing_to_expansion",
        "lease_additions_due_to_expansion",
        (str(uuid4()),),
        "confirmed",
        workspace_id,
        run_id,
        PERIOD,
        (),
    )
    with_evidence = ReconciliationEngine().evaluate(
        rule,
        inputs,
        policy(),
        timestamp=NOW,
        capability_evidence=(evidence,),
        workspace_id=workspace_id,
        run_id=run_id,
    )
    assert not with_evidence.capability_effects[0].blocked


@pytest.mark.parametrize(
    ("opening", "flow", "closing", "valid"),
    (
        (
            ("2025-01-01", "2025-12-31", "year", 2025, "current", 12),
            PERIOD,
            PERIOD,
            True,
        ),
        (
            ("2025-10-01", "2025-12-31", "quarter", 2025, "current", 12),
            PERIOD,
            PERIOD,
            False,
        ),
        (
            ("2025-01-01", "2025-12-31", "year", 2025, "budget", 12),
            PERIOD,
            PERIOD,
            False,
        ),
        (
            ("2025-04-01", "2026-03-31", "year", 2026, "current", 3),
            ("2026-04-01", "2027-03-31", "year", 2027, "current", 3),
            ("2026-04-01", "2027-03-31", "year", 2027, "current", 3),
            True,
        ),
    ),
)
def test_roll_forward_validates_complete_civil_and_fiscal_identity(
    opening, flow, closing, valid
):
    def cash(role, identity, value, period):
        return ReconciliationInput(
            role,
            uuid4(),
            identity,
            Decimal(value),
            "monetary",
            "EUR",
            "millions",
            period,
            "consolidated",
            (),
            "confirmed",
            ("source:1",),
            (),
        )

    inputs = (
        cash("opening_cash", "cash_and_equivalents", "10", opening),
        cash("cfo", "cfo", "2", flow),
        cash("cfi", "cfi", "-1", flow),
        cash("cff", "cff", "0", flow),
        cash("fx_other", "cash_fx_other", "0", flow),
        cash("closing_cash", "cash_and_equivalents", "11", closing),
    )

    def action():
        return ReconciliationEngine().evaluate(
            FINANCIAL_RULES_V1.get("REC-CF-001"), inputs, policy(), timestamp=NOW
        )

    if valid:
        assert action().status == "pass"
    else:
        with pytest.raises(InputContractError, match="period|fiscal"):
            action()


def test_f04_identity_catalog_contains_only_upstream_or_versioned_raw_ids():
    upstream = {entry.concept_id for entry in FINANCIAL_TERMS_V8}
    upstream.update(definition.metric_id for definition in FINANCIAL_V1.definitions)
    for definition in FINANCIAL_V1.definitions:
        for contract in definition.operand_contracts:
            upstream.update(contract.metric_ids)
    permitted = {
        identity
        for rule in FINANCIAL_RULES_V1.rules
        for operand in rule.operands
        for identity in operand.identities
    }
    assert permitted <= upstream | RAW_BRIDGE_IDENTITIES_V1
    assert {"assets", "liabilities"}.isdisjoint(permitted)
