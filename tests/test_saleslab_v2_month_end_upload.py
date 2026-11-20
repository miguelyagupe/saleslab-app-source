"""Customer file → deterministic, source-bound close preparation without publication."""

from uuid import uuid4

import pytest
from month_end_upload_fixtures import month_end_case
from pydantic import ValidationError

from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.product.contracts import InvalidUpload
from saleslab_v2.product.month_end import MonthEndChoices, prepare_month_end


def prepared(change=None):
    parsed, choices, close, _, _, user = month_end_case()
    if change:
        close = MonthEndChoices.model_validate({**close.model_dump(), **change})
    return prepare_month_end(parsed, choices, close, user)


def test_complete_monthly_checklist_has_traces_exact_values_and_original_sources():
    value = prepared()
    result = value["result"]
    assert result["preparation_readiness"] == "ready_for_review"
    assert len(result["comparisons"]) == 3
    assert {c["delta"] for c in result["comparisons"]} == {"-100", "200", "2000"}
    assert len(result["calculations"]) == 6
    display = next(
        c for c in value["presentation"]["comparisons"] if c["key"] == "revenue-mom"
    )
    assert display["percentage"] == "-9,09 %"
    assert (
        len(
            next(c for c in result["comparisons"] if c["key"] == "revenue-mom")[
                "percentage"
            ]
        )
        > 10
    )
    assert result["accounting_close_attested"] is False
    assert result["customer_publication_authorized"] is False
    assert value["persisted"] is value["quota_consumed"] is False
    assert value["original_rows"][0]["amount"] == "1000"
    assert value["payload_hash"] == semantic_hash(
        {k: v for k, v in value.items() if k != "payload_hash"}
    )


@pytest.mark.parametrize(
    "change,category",
    [
        ({"confirmations": []}, "task"),
        ({"comparisons": []}, "comparison"),
        ({"required_statements": ["cash_flow_statement"]}, "statement"),
        ({"currency": "USD"}, "statement"),
    ],
)
def test_incomplete_data_produces_concrete_requests(change, category):
    result = prepared(change)["result"]
    assert result["preparation_readiness"] == "incomplete"
    assert any(
        r["action"] == "resolve_" + category for r in result["missing_data_requests"]
    )


def test_decline_with_complete_data_is_review_not_incomplete_and_threshold_is_explicit():
    result = prepared({"absolute_variation_threshold": "100"})["result"]
    assert result["preparation_readiness"] == "review_required"
    assert result["missing_data_requests"] == []
    assert result["performance_assessment"] == "not_assessed"
    assert (
        next(c for c in result["comparisons"] if c["relation"] == "month_on_month")[
            "direction"
        ]
        == "decrease"
    )


@pytest.mark.parametrize(
    "threshold", ["0", "-1", "NaN", "Infinity", "garbage", "1e9999999", "1e-9999999"]
)
def test_invalid_or_unbounded_threshold_rejected(threshold):
    with pytest.raises((ValidationError, InvalidUpload)):
        prepared({"absolute_variation_threshold": threshold})


@pytest.mark.parametrize(
    "change",
    [
        {"target_month": "2026-13"},
        {"target_month": "0000-01"},
        {"fiscal_year_start_month": 7},
        {"preview_hash": "0" * 64},
        {"required_tasks": ["same", "same"]},
        {"required_reconciliations": ["unknown"]},
        {
            "comparisons": [
                {
                    "key": "outside",
                    "current": "csv:999",
                    "prior": "csv:2",
                    "relation": "month_on_month",
                }
            ]
        },
        {
            "confirmations": [
                {"task_id": "outside", "status": "complete", "evidence": ["csv:2"]}
            ]
        },
    ],
)
def test_invalid_stale_or_foreign_bindings_rejected(change):
    with pytest.raises(InvalidUpload):
        prepared(change)


def test_bindings_cover_source_policy_actor_and_language():
    parsed, choices, close, _, _, user = month_end_case()
    first = prepare_month_end(parsed, choices, close, user)
    assert (
        first["binding_hash"]
        == prepare_month_end(parsed, choices, close, user)["binding_hash"]
    )
    assert (
        first["binding_hash"]
        != prepare_month_end(parsed, choices, close, uuid4())["binding_hash"]
    )
    changed = close.model_copy(update={"policy_version": "2"})
    assert (
        first["binding_hash"]
        != prepare_month_end(parsed, choices, changed, user)["binding_hash"]
    )
    with pytest.raises(InvalidUpload):
        prepare_month_end(
            parsed, choices.model_copy(update={"report_language": "fr"}), close, user
        )


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
def test_labels_are_localized_without_changing_arithmetic(language):
    parsed, choices, close, _, content, user = month_end_case()
    from product_fixtures import upload_case

    localized = choices.model_copy(update={"report_language": language})
    preview = upload_case(
        parsed.batch.document.workspace_id, user, content=content, choices=localized
    )[1]
    close = close.model_copy(update={"preview_hash": preview["preview_hash"]})
    result = prepare_month_end(parsed, localized, close, user)["result"]
    assert result["language"] == language
    assert result["preparation_readiness"] == "ready_for_review"
    assert {c["delta"] for c in result["comparisons"]} == {"-100", "200", "2000"}


def test_missing_evidence_and_mismatched_periods_never_invent_comparisons():
    value = prepared(
        {
            "comparisons": [
                {
                    "key": "wrong",
                    "current": "csv:2",
                    "prior": "csv:8",
                    "relation": "month_on_month",
                }
            ]
        }
    )
    assert value["result"]["comparisons"] == []
    assert value["result"]["preparation_readiness"] == "incomplete"


def test_display_rounding_does_not_change_exact_materiality():
    value = prepared(
        {
            "absolute_variation_threshold": "100.001",
            "percentage_variation_threshold": "1000",
        }
    )
    actual = next(
        c for c in value["result"]["comparisons"] if c["key"] == "revenue-mom"
    )
    display = next(
        c for c in value["presentation"]["comparisons"] if c["key"] == "revenue-mom"
    )
    assert actual["delta"] == "-100"
    assert actual["material"] is False
    assert display["delta"] == "-100,00 EUR"


def test_zero_base_retains_absolute_delta_without_displaying_a_percentage():
    from product_fixtures import upload_case

    parsed, choices, close, _, content, user = month_end_case()
    parsed, preview, *_ = upload_case(
        parsed.batch.document.workspace_id,
        user,
        content=content.replace(b"1100;", b"0;"),
        choices=choices,
    )
    close = close.model_copy(update={"preview_hash": preview["preview_hash"]})
    value = prepare_month_end(parsed, choices, close, user)
    actual = next(
        c for c in value["result"]["comparisons"] if c["key"] == "revenue-mom"
    )
    display = next(
        c for c in value["presentation"]["comparisons"] if c["key"] == "revenue-mom"
    )
    assert actual["delta"] == "1000"
    assert actual["percentage"] is display["percentage"] is None
    assert actual["percentage_reason"] == "month_end.zero_comparison_base"
    assert value["result"]["preparation_readiness"] == "review_required"


def test_invalid_configuration_keeps_capability_fallback_available():
    import asyncio
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from saleslab_v2.product.api import capabilities

    session = {"workspace": SimpleNamespace(id=uuid4()), "role": "owner"}
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with (
        patch(
            "saleslab_v2.product.api.get_current_session",
            AsyncMock(return_value=session),
        ),
        patch(
            "saleslab_v2.product.api.get_settings",
            side_effect=ValueError("invalid flag"),
        ) as settings,
    ):
        response = asyncio.run(capabilities("synthetic-token", db))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    value = json.loads(response.body)
    assert value["enabled"] is False
    assert value["month_end_preview_versions"] == []
    assert value["formats"][:2] == ["csv", "xlsx"]
    settings.assert_called_once()
