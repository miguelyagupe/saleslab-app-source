"""Grouped liquidity, debt/lease bridges and actual-calendar working-capital acceptance."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from analyst_fixtures import context_fixture, model_output
from audit_fixtures import draft_fixture
from capital_fixtures import capital_choices, capital_content
from product_fixtures import csv_content, upload_case
from profitability_fixtures import profitability_choices, profitability_rows

from saleslab_v2.analyst.contracts import InvalidAnalystOutput, bind_findings
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.i18n.financial_v4 import FINANCIAL_TERMS_V4
from saleslab_v2.i18n.financial_v5 import FINANCIAL_TERMS_V5
from saleslab_v2.pipeline.adapters import variant_for
from saleslab_v2.pipeline.replay import restore_request
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.preview import LABELS


def case(**kwargs):
    option_names = {
        "source_language",
        "format_locale",
        "report_language",
        "target_period",
        "fiscal_year_end_month",
        "capital_rounding_tolerance",
        "rounding_tolerance",
        "cash_rounding_tolerance",
        "profitability_rounding_tolerance",
        "accounting_parentheses",
    }
    options = {k: kwargs.pop(k) for k in list(kwargs) if k in option_names}
    opts = capital_choices(**options)
    return upload_case(content=capital_content(**kwargs), choices=opts)


def metrics(v):
    return {m["key"]: m for m in v["calculated_metrics"]}


def checks(v):
    return {r["rule_id"]: r for r in v["validation_summary"]["reconciliations"]}


@pytest.mark.parametrize(
    "language,locale",
    (("pt", "pt-PT"), ("en", "en-GB"), ("es", "es-ES"), ("fr", "fr-FR")),
)
@pytest.mark.parametrize("report", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize(
    "scale,factor", (("raw", 1), ("thousands", 1000), ("millions", 1000000))
)
def test_complete_bundle_has_explicit_eligibility_variants_real_days_and_independent_bridges(
    language, locale, report, scale, factor
):
    p, v, *_ = case(
        language=language,
        source_language=language,
        format_locale=locale,
        report_language=report,
        scale=scale,
    )
    assert v["ready"]
    assert p.batch.metadata.terminology_version == "5"
    ms = metrics(v)
    expected = {
        "current_ratio": 4,
        "quick_ratio": 1,
        "net_working_capital": 120 * factor,
        "gross_debt": 600 * factor,
        "net_debt": 500 * factor,
        "net_debt_incl_leases": 620 * factor,
        "dso": Decimal("36.5"),
        "dio": 73,
        "dpo": Decimal("45.625"),
        "cash_conversion_cycle": Decimal("63.875"),
        "days_for_dso": 365,
        "net_debt_to_ebitda_statutory": 2,
        "net_debt_incl_leases_to_ebitda_statutory": Decimal("2.48"),
        "interest_coverage_ebit_confirmed": 5,
        "interest_coverage_ebitda_statutory": Decimal("6.25"),
        "interest_coverage_ebitda_adjusted": Decimal("6.75"),
    }
    for key, amount in expected.items():
        assert Decimal(ms[key]["exact_value"]) == amount
        assert ms[key]["calculation_evidence"]["trace_hash"]
        assert ms[key]["label"] == LABELS[key][("pt", "en", "es", "fr").index(report)]
    assert (
        ms["current_ratio"]["calculation_evidence"]["metric_definition_version"] == "2"
    )
    assert checks(v)["REC-DEBT-001"]["status"] == "pass"
    assert checks(v)["REC-LEASE-001"]["status"] == "pass"
    debt = checks(v)["REC-DEBT-001"]["reconciliation_evidence"]
    assert (
        debt["equations"][0]["canonical_expression"]
        == "opening+drawdowns-repayments+fx_other=closing"
    )
    opening = next(i for i in debt["inputs"] if i["role"] == "opening")
    closing = next(i for i in debt["inputs"] if i["role"] == "closing")
    assert opening["variant"] == closing["variant"] == "excluding_leases"
    assert opening["sources"][0]["period"] == "2025"
    assert closing["sources"][0]["period"] == "2026"
    assert opening["source_references"] != closing["source_references"]
    lease = checks(v)["REC-LEASE-001"]["reconciliation_evidence"]
    assert all(i["variant"] == "lease_bridge" for i in lease["inputs"])
    assert any(i["code"] == "pipeline.causal_evidence_required" for i in v["issues"])
    assert (
        next(
            c
            for c in v["validation_summary"]["capabilities"]
            if c["capability"] == "infer_cause"
        )["status"]
        == "blocked"
    )


@pytest.mark.parametrize(
    "period,opening,month,days",
    (
        ("2024", "2023", None, 366),
        ("February 2024", "January 2024", None, 29),
        ("2024 Q1", "2023 Q4", None, 91),
        ("FY 2024", "FY 2023", 6, 366),
        ("FY 2024 Q1", "FY 2023 Q4", 6, 92),
        ("2026 Q2 YTD", "2025", None, 181),
    ),
)
def test_actual_calendar_days_are_period_bound_and_never_default_to_360(
    period, opening, month, days
):
    _, v, *_ = case(
        period=period,
        opening=opening,
        target_period=period,
        fiscal_year_end_month=month,
    )
    ms = metrics(v)
    for key in ("days_for_dso", "days_for_dio", "days_for_dpo"):
        assert Decimal(ms[key]["exact_value"]) == days
        trace = ms[key]["calculation_evidence"]
        assert (
            trace["canonical_expression"]
            == "inclusive_calendar_days(period_anchor.start_date,period_anchor.end_date)"
        )
        assert trace["division_policy"] is None
    assert Decimal(ms["dso"]["exact_value"]) == Decimal(days) / 10
    if period in {"2024", "February 2024", "2024 Q1", "FY 2024"}:
        assert checks(v)["REC-DEBT-001"]["status"] == "pass"


@pytest.mark.parametrize(
    "missing",
    (
        "eligible_unrestricted_cash",
        "eligible_trade_receivables",
        "other_permitted_liquid_assets",
        "average_trade_receivables",
        "average_inventory",
        "average_trade_payables",
        "purchases",
        "current_operating_assets",
        "current_operating_liabilities",
        "interest_expense",
    ),
)
def test_missing_components_do_not_use_totals_closing_balances_cogs_or_net_finance_cost(
    missing,
):
    legacy = [
        (
            "cash and cash equivalents",
            "100",
            "2026",
            "balance_sheet",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ),
        (
            "trade receivables",
            "100",
            "2026",
            "balance_sheet",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ),
        (
            "inventory",
            "120",
            "2026",
            "balance_sheet",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ),
        (
            "trade payables",
            "90",
            "2026",
            "balance_sheet",
            "consolidated",
            "EUR",
            "raw",
            "current",
        ),
    ]
    _, v, *_ = case(omit=(missing,), extra=legacy)
    assert any(
        any(
            r["concept_id"] == missing and r["status"] == "missing"
            for r in m["requirements"]
        )
        for m in v["metric_availability"]
    )
    required = {
        "eligible_unrestricted_cash": "net_debt",
        "eligible_trade_receivables": "quick_ratio",
        "other_permitted_liquid_assets": "quick_ratio",
        "average_trade_receivables": "dso",
        "average_inventory": "dio",
        "average_trade_payables": "dpo",
        "purchases": "dpo",
        "current_operating_assets": "net_working_capital",
        "current_operating_liabilities": "net_working_capital",
        "interest_expense": "interest_coverage_ebit_confirmed",
    }
    assert required[missing] not in metrics(v)


@pytest.mark.parametrize(
    "missing",
    (
        "debt_drawdowns",
        "debt_repayments",
        "debt_fx_other",
        "opening_debt",
        "lease_additions",
        "lease_modifications",
        "lease_interest",
        "lease_payments",
        "lease_fx_other",
        "opening_lease",
    ),
)
def test_every_bridge_movement_and_opening_is_required_even_for_explicit_zero(missing):
    _, v, *_ = case(omit=(missing,))
    rule = (
        "REC-DEBT-001"
        if missing.startswith("debt_") or missing == "opening_debt"
        else "REC-LEASE-001"
    )
    assert rule not in checks(v)
    gap = next(g for g in v["capital_reconciliations"] if g["rule_id"] == rule)
    assert gap["status"] == "not_planned" and gap["requirements"]


@pytest.mark.parametrize(
    "concept",
    (
        "debt_excluding_leases",
        "eligible_unrestricted_cash",
        "average_inventory",
        "purchases",
        "debt_drawdowns",
        "lease_liabilities",
    ),
)
def test_duplicate_candidates_cannot_be_selected(concept):
    from capital_fixtures import TERMS

    from saleslab_v2.domain.enums import LanguageCode

    t = TERMS[concept]
    duplicate = (
        t.labels[LanguageCode.EN],
        "(1)",
        "2026",
        t.statement_type,
        "consolidated",
        "EUR",
        "raw",
        "current",
    )
    _, v, *_ = case(extra=(duplicate,))
    assert not v["ready"]
    assert any(
        any(
            r["concept_id"] == concept and r["status"] == "ambiguous"
            for r in m["requirements"]
        )
        for m in v["metric_availability"]
    )


@pytest.mark.parametrize(
    "concept",
    (
        "eligible_unrestricted_cash",
        "eligible_trade_receivables",
        "other_permitted_liquid_assets",
        "debt_excluding_leases",
        "lease_liabilities",
        "average_inventory",
        "purchases",
        "debt_repayments",
        "lease_payments",
        "interest_expense",
    ),
)
def test_negative_magnitudes_are_blocked_and_not_absolutized(concept):
    _, v, *_ = case(changes={concept: {"amount": "-1"}})
    assert not v["ready"]
    assert any(r["concept_id"] == concept and r["value"] == "-1" for r in v["rows"])


@pytest.mark.parametrize(
    "changes,base,profit,blocked",
    (
        ({"current_liabilities": {"amount": "0"}}, None, None, "quick_ratio"),
        ({"current_liabilities": {"amount": "-1"}}, None, None, "current_ratio"),
        ({"purchases": {"amount": "0"}}, None, None, "dpo"),
        (
            {"interest_expense": {"amount": "0"}},
            None,
            None,
            "interest_coverage_ebit_confirmed",
        ),
        (None, {"revenue": {"amount": "0"}}, None, "dso"),
        (None, None, {"ebit": {"amount": "-100"}}, "net_debt_to_ebitda_statutory"),
    ),
)
def test_zero_or_negative_denominators_do_not_produce_misleading_ratios(
    changes, base, profit, blocked
):
    _, v, *_ = case(changes=changes, base_changes=base, profit_changes=profit)
    assert not v["ready"]
    assert metrics(v)[blocked]["exact_value"] is None


@pytest.mark.parametrize(
    "concept,rule",
    (("debt_excluding_leases", "REC-DEBT-001"), ("lease_liabilities", "REC-LEASE-001")),
)
def test_independent_stock_differences_require_their_own_allowance(concept, rule):
    amount = "600.005" if concept == "debt_excluding_leases" else "120.005"
    _, blocked, *_ = case(
        changes={concept: {"amount": amount}},
        rounding_tolerance="100",
        cash_rounding_tolerance="100",
        profitability_rounding_tolerance="100",
    )
    assert checks(blocked)[rule]["status"] == "blocking" and not blocked["ready"]
    _, accepted, *_ = case(
        changes={concept: {"amount": amount}}, capital_rounding_tolerance="0.01"
    )
    assert accepted["ready"] and checks(accepted)[rule]["status"] == "rounding_pass"
    assert Decimal(checks(accepted)[rule]["absolute_delta"]) == Decimal("0.005")


@pytest.mark.parametrize(
    "concept",
    (
        "eligible_unrestricted_cash",
        "average_inventory",
        "debt_repayments",
        "opening_lease",
    ),
)
@pytest.mark.parametrize(
    "field,value",
    (("currency", "USD"), ("period", "2024"), ("comparison_basis", "comparative")),
)
def test_mismatched_period_basis_or_currency_is_not_converted_or_guessed(
    concept, field, value
):
    _, v, *_ = case(changes={concept: {field: value}})
    if field == "currency":
        assert not v["ready"]
    else:
        assert any(
            any(
                r["concept_id"]
                == ("lease_liabilities" if concept == "opening_lease" else concept)
                for r in m["requirements"]
            )
            for m in v["metric_availability"]
        )


def test_omitted_movement_zero_is_distinct_from_explicit_zero_and_net_cash_is_allowed():
    _, v, *_ = case(
        changes={
            "debt_fx_other": {"amount": "0"},
            "debt_drawdowns": {"amount": "170"},
            "eligible_unrestricted_cash": {"amount": "700"},
        }
    )
    assert v["ready"] and checks(v)["REC-DEBT-001"]["status"] == "pass"
    assert Decimal(metrics(v)["net_debt"]["exact_value"]) == -100
    assert Decimal(
        metrics(v)["net_debt_to_ebitda_statutory"]["exact_value"]
    ) == Decimal("-0.4")


def test_legacy_gross_debt_variants_are_not_reinterpreted():
    assert variant_for("gross_debt", ("excluding_leases",)) is None
    assert variant_for("gross_debt", ("excluding_leases",), "1") is None
    assert variant_for("gross_debt", ("excluding_leases",), "2") == "excluding_leases"
    assert FINANCIAL_TERMS_V5[: len(FINANCIAL_TERMS_V4)] == FINANCIAL_TERMS_V4


def test_selection_is_bound_replayable_and_preserves_all_calculations_and_guards():
    p, v, _, _, user = case()
    request, _ = prepare_upload(p, capital_choices(), user, "preview")
    restored = restore_request(
        canonicalize(request.semantic_payload()),
        run=SimpleNamespace(
            workspace_id=request.workspace_id,
            created_by_user_id=user,
            idempotency_key="preview",
            created_at=datetime.now(UTC),
            language_context={"format_locale": "en-GB"},
        ),
    )
    assert restored.fingerprint == request.snapshot().fingerprint == request.fingerprint
    assert restored.analyst_metric_keys == request.analyst_metric_keys
    assert restored.sources[0].metadata.terminology_version == "5"
    assert replace(request, analyst_metric_keys=None).fingerprint != request.fingerprint
    with pytest.raises(ValueError):
        replace(request, analyst_metric_keys=("not-a-step",))
    with pytest.raises(ValueError):
        replace(request, analyst_metric_keys="gross_debt")

    def selected(context):
        raw = model_output(context)
        raw["findings"] = raw["findings"][:20]
        return raw

    fixed_run = UUID("20000000-0000-4000-8000-000000000001")
    with patch("analyst_fixtures.uuid4", return_value=fixed_run), patch(
        "audit_fixtures.model_output", side_effect=selected
    ):
        _, _, context, _, draft = draft_fixture(request)
    assert len(v["calculated_metrics"]) == 63 and len(context["evidence"]) == 33
    assert "days_for_dso" in context["evidence_selection"]["excluded_keys"]
    assert "dso" in context["evidence_selection"]["included_keys"]
    assert len(json.dumps(context, ensure_ascii=True).encode()) < 256000
    assert (
        len(
            json.dumps(
                {"evidence_context": context, "draft": draft}, ensure_ascii=True
            ).encode()
        )
        < 384000
    )
    assert {r["rule_id"] for r in context["reconciliations"]} >= {
        "REC-DEBT-001",
        "REC-LEASE-001",
    }
    all_request = replace(request, analyst_metric_keys=None)
    with patch("analyst_fixtures.uuid4", return_value=fixed_run):
        *_, all_context = context_fixture(all_request)
    omitted = next(
        e for e in all_context["evidence"] if e["concept_id"] == "days_in_period"
    )
    assert omitted["evidence_id"] not in {e["evidence_id"] for e in context["evidence"]}
    fake = {
        "schema_version": "1",
        "language": "pt",
        "findings": [
            {
                "title": "Observação",
                "statement": "Valor {{" + omitted["evidence_id"] + "}}",
                "claim_type": "calculation",
                "capability": "calculate",
                "evidence_ids": [omitted["evidence_id"]],
            }
        ],
    }
    with pytest.raises(InvalidAnalystOutput):
        bind_findings(fake, context)


def test_pre_change_plan_seven_hash_and_absent_selection_are_unchanged():
    w = UUID("10000000-0000-4000-8000-000000000001")
    u = UUID("10000000-0000-4000-8000-000000000002")
    opts = profitability_choices()
    p, v, *_ = upload_case(
        w, u, content=csv_content(extra=profitability_rows()), choices=opts
    )
    r, _ = prepare_upload(p, opts, u, "preview")
    assert (
        v["preview_hash"]
        == "9941760271d8d3f729e3d5ee7a31777c44bff3aaba19b2efeadc34f3b0e8db72"
    )
    assert (
        r.fingerprint
        == "8f707055da3ec2f2e690df343c8676f4e74179cb40e89c512021b9418b2f6584"
    )
    assert "analyst_metric_keys" not in r.semantic_payload()
    assert "capital_rounding_tolerance" not in opts.contract_payload()
    assert "capital_reconciliations" not in v


@pytest.mark.parametrize(
    "value", ("NaN", "Infinity", "-1", "1e64", "1e-64", "", 0, True)
)
def test_capital_tolerance_remains_strict_finite_and_bounded(value):
    with pytest.raises(ValueError):
        capital_choices(capital_rounding_tolerance=value)


def test_helper_only_input_does_not_offer_generation_without_narrative_evidence():
    from capital_fixtures import capital_rows

    rows = capital_rows()
    # Keep exactly the explicit eligible-cash row and template header.
    content = csv_content(extra=[rows[2]]).splitlines()
    _, preview, _, _, _ = upload_case(
        content=b"\n".join([content[0], content[-1]]) + b"\n", choices=capital_choices()
    )
    assert "cash_and_cash_equivalents" in metrics(preview)
    assert not preview["ready"]


def test_full_bundle_survives_independent_statement_reexecution_and_report_compilation():
    from report_fixtures import report_fixture

    from saleslab_v2.audit.context import presentation_preview

    p, _, _, _, user = case()
    request, _ = prepare_upload(p, capital_choices(), user, "preview")

    def selected(context):
        raw = model_output(context)
        sizes = {e["evidence_id"]: len(json.dumps(e)) for e in context["evidence"]}
        raw["findings"] = sorted(
            raw["findings"], key=lambda f: sizes[f["evidence_ids"][0]], reverse=True
        )[:20]
        return raw

    with patch("report_fixtures.request", return_value=request), patch(
        "audit_fixtures.model_output", side_effect=selected
    ):
        report, (_, _, context, _, draft, audit, checks_) = report_fixture(growth=False)
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert all(c["status"] != "block" for c in checks_)
    assert len(report["metric_observations"]) == 63
    assert len(report["evidence"]) == 33
    assert report["methodology"]["evidence_selection"] == context["evidence_selection"]
    model_input = {
        "evidence_context": context,
        "draft": draft,
        "presentation": presentation_preview(draft, context),
    }
    assert len(json.dumps(model_input, ensure_ascii=True).encode()) < 384000

    from saleslab_v2.reports.render import render_channels

    report["render_version"] = "financial-render-v4"
    artifacts = render_channels(report)
    assert artifacts["parity"]["status"] == "pass"
    assert 1 <= artifacts["parity"]["page_count"] <= 120
    assert len(report["metric_observations"]) == 63
    assert _restore_technical_appendix(report) == report


def test_evidence_selection_cannot_hide_a_failed_bridge_or_bypass_quality_gates():
    from uuid import uuid4

    from saleslab_v2.pipeline.evaluator import evaluate

    p, v, _, _, user = case(changes={"debt_repayments": {"amount": "71"}})
    assert not v["ready"]
    request, _ = prepare_upload(p, capital_choices(), user, "preview")
    request = replace(request, analyst_metric_keys=("revenue",))
    result = evaluate(request, uuid4(), datetime.now(UTC))
    assert not result.ready
    assert len(result.metrics) == 63
    assert (
        next(r for r in result.reconciliations if r.rule_id == "REC-DEBT-001").status
        == "blocking"
    )


@pytest.mark.parametrize(
    "concept,statement",
    (
        ("debt_drawdowns", "debt_schedule"),
        ("lease_additions", "lease_schedule"),
        ("purchases", "working_capital_detail"),
    ),
)
def test_bridge_schedules_are_typed_and_never_reclassified_as_cash_flow(
    concept, statement
):
    from capital_fixtures import TERMS

    from saleslab_v2.domain.enums import LanguageCode
    from saleslab_v2.normalization.financial import FinancialNormalizer
    from saleslab_v2.product.contracts import InvalidUpload

    t = TERMS[concept]
    assert t.statement_type == statement
    p, v, *_ = case(changes={concept: {"statement_type": "cash_flow_statement"}})
    assert not v["ready"]
    assert (
        next(r for r in v["rows"] if r["original_line"] == t.labels[LanguageCode.EN])[
            "concept_id"
        ]
        is None
    )
    p, v, *_ = case()
    with pytest.raises(InvalidUpload):
        upload_case(
            content=capital_content(),
            choices=capital_choices().model_copy(update={"planning_version": "7"}),
        )
    with pytest.raises(ValueError, match="statement_type"):
        FinancialNormalizer().normalize(
            list(p.batch.rows), replace(p.batch.metadata, terminology_version="4")
        )


@pytest.mark.parametrize(
    "period,opening,month",
    (("FY 2024 Q2", "FY 2024 Q1", 6), ("January 2024", "2023", None)),
)
def test_new_debt_and_lease_rules_support_real_stock_dates_without_changing_v1(
    period, opening, month
):
    from uuid import uuid4

    from saleslab_v2.pipeline.evaluator import evaluate
    from saleslab_v2.reconciliation.contracts import (
        InputContractError,
        ReconciliationInput,
    )
    from saleslab_v2.reconciliation.engine import ReconciliationEngine
    from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1

    opts = capital_choices(target_period=period, fiscal_year_end_month=month)
    content = capital_content(period=period, opening=opening)
    p, v, _, _, user = upload_case(content=content, choices=opts)
    assert v["ready"]
    request, _ = prepare_upload(p, opts, user, "preview")
    evaluated = evaluate(request, uuid4(), datetime.now(UTC))
    for rule_id in ("REC-DEBT-001", "REC-LEASE-001"):
        result = next(r for r in evaluated.reconciliations if r.rule_id == rule_id)
        step = next(s for s in request.reconciliations if s.rule_id == rule_id)
        assert step.version == result.rule_version == "2"
        assert result.status == "pass"
        inputs = tuple(
            ReconciliationInput(
                **{
                    **item,
                    "observation_id": UUID(item["observation_id"]),
                    "value": Decimal(item["value"]),
                    "calculation_trace_id": (
                        UUID(item["calculation_trace_id"])
                        if item["calculation_trace_id"]
                        else None
                    ),
                }
            )
            for item in result.input_references
        )
        with pytest.raises(InputContractError):
            ReconciliationEngine().evaluate(
                FINANCIAL_RULES_V1.get(rule_id, "1"),
                inputs,
                step.policy,
                timestamp=datetime.now(UTC),
            )


def _restore_technical_appendix(report):
    """Rebuild the complete JSON exclusively from displayed appendix blocks."""
    from copy import deepcopy

    from saleslab_v2.reports.presentation import document

    root = {}
    references = 0

    def segments(path):
        return [p.replace("~1", "/").replace("~0", "~") for p in path.split("/")[1:]]

    def lookup(parts):
        node = root
        for part in parts:
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node

    def install(parts, value, merge=False):
        if not parts:
            root.update(value)
            return
        node = root
        for offset, part in enumerate(parts):
            key = int(part) if isinstance(node, list) else part
            if isinstance(node, list):
                while len(node) <= key:
                    node.append(None)
            if offset == len(parts) - 1:
                if merge and isinstance(
                    node[key] if isinstance(node, list) else node.get(key), dict
                ):
                    node[key].update(value)
                else:
                    node[key] = value
            else:
                existing = node[key] if isinstance(node, list) else node.get(key)
                if existing is None:
                    node[key] = [] if parts[offset + 1].isdigit() else {}
                node = node[key]

    for block in document(report):
        if not block.key.startswith("ReportResult"):
            continue
        path, content = block.text.split(" | ", 1)
        if block.key.endswith("/reference"):
            assert content.startswith("identical content: ")
            target = content.removeprefix("identical content: ")
            install(segments(path), deepcopy(lookup(segments(target))))
            references += 1
        else:
            install(segments(path), json.loads(content), block.key.endswith("/scalars"))
    assert references > 0
    return root


def test_compact_appendix_preserves_escaped_paths_and_near_duplicates():
    from copy import deepcopy

    from report_fixtures import report_fixture

    report, _ = report_fixture(growth=False)
    report["render_version"] = "financial-render-v4"
    nested = {"a/b~c": {"long": "explicit source text " * 20, "amount": "12.0"}}
    report["company_profile"] = {
        "one": nested,
        "two": deepcopy(nested),
        "three": {"a/b~c": {**nested["a/b~c"], "amount": "12.00"}},
        "empty": [],
        "null": None,
        "bool": False,
        "integer": 0,
        "unicode": "ação / échéance",
    }
    assert _restore_technical_appendix(report) == report


@pytest.mark.parametrize("report_language", ("pt", "en", "es", "fr"))
def test_complete_cash_and_capital_bundle_fits_critic_and_renderer_bounds(
    report_language,
):
    from report_fixtures import report_fixture

    from saleslab_v2.audit.context import presentation_preview
    from saleslab_v2.audit.contracts import CRITIC_CHECKS
    from saleslab_v2.reports.presentation import document

    extra = [
        (label, amount, period, category, "consolidated", "EUR", "millions", "current")
        for label, amount, period, category in (
            (
                "purchases of property plant and equipment",
                "-80",
                "2026",
                "cash_flow_statement",
            ),
            ("cash flow from investing", "-120", "2026", "cash_flow_statement"),
            ("cash flow from financing", "40", "2026", "cash_flow_statement"),
            ("cash and cash equivalents", "100", "2025", "balance_sheet"),
            ("cash and cash equivalents", "320", "2026", "balance_sheet"),
            (
                "cash foreign exchange and other movements",
                "0",
                "2026",
                "cash_flow_statement",
            ),
        )
    ]
    opts = capital_choices(report_language=report_language)
    p, preview, _, _, user = upload_case(
        content=capital_content(scale="millions", extra=extra),
        choices=opts,
        filename="explicit_financial_source_" + "x" * 70 + ".csv",
    )
    assert preview["ready"]
    request, _ = prepare_upload(p, opts, user, "preview")

    def selected(context):
        raw = model_output(context)
        sizes = {e["evidence_id"]: len(json.dumps(e)) for e in context["evidence"]}
        raw["findings"] = sorted(
            raw["findings"], key=lambda f: sizes[f["evidence_ids"][0]], reverse=True
        )[:20]
        return raw

    with patch("report_fixtures.request", return_value=request), patch(
        "audit_fixtures.model_output", side_effect=selected
    ):
        report, (_, _, context, _, draft, _, _) = report_fixture(
            language=report_language, growth=False
        )
    model_input = {
        "evidence_context": context,
        "draft": draft,
        "presentation": presentation_preview(draft, context),
        "required_checks": sorted(CRITIC_CHECKS),
        "available_comparatives": [],
        "comparative_directions": [],
    }
    assert len(json.dumps(model_input, ensure_ascii=True).encode()) < 384000
    assert len(report["metric_observations"]) == 67
    assert len(report["evidence"]) == 36
    assert "cash_fx_other" in context["evidence_selection"]["excluded_keys"]
    assert any(m["key"] == "cash_fx_other" for m in report["metric_observations"])
    assert (
        next(r for r in report["reconciliations"] if r["rule_id"] == "REC-CF-001")[
            "status"
        ]
        == "pass"
    )
    report["render_version"] = "financial-render-v4"
    assert len(document(report)) < 6000
    assert _restore_technical_appendix(report) == report
