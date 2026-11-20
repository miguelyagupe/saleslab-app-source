"""Pure snapshot binding and replay tests; no database connection or paid models."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from month_end_upload_fixtures import month_end_case

from saleslab_v2.config import V2Settings
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.product.month_end_saved import (
    InvalidMonthlySnapshot,
    SaveMonthEndChoices,
    bounded,
    input_snapshot,
    readable,
    restore_input,
    saved_payload,
    semantic_result,
)


def example():
    parsed, choices, close, _, _, user = month_end_case()
    request, data, result = input_snapshot(parsed, choices, close, user)
    run = SimpleNamespace(
        workspace_id=request.workspace_id,
        created_by_user_id=user,
        idempotency_key=str(uuid4()),
        language_context=request.language.to_dict(),
        created_at=datetime.now(UTC),
    )
    return run, data, result


def test_saved_snapshot_replays_the_same_reviewed_decisions_and_preserves_preview():
    run, data, result = example()
    before = deepcopy(result)
    _, fresh = restore_input(data, run)
    snapshot = saved_payload(result, uuid4(), datetime.now(UTC))
    assert semantic_result(snapshot) == semantic_result(fresh)
    assert result == before and result["persisted"] is False
    assert snapshot["persisted"] is True and snapshot["quota_consumed"] is False
    assert snapshot["schema"] != result["schema"]
    assert snapshot["preparation_schema"] == result["schema"]
    assert snapshot["result"]["accounting_close_attested"] is False
    assert snapshot["result"]["customer_publication_authorized"] is False
    assert (
        semantic_hash({k: v for k, v in snapshot.items() if k != "payload_hash"})
        == snapshot["payload_hash"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", str(uuid4())),
        ("version", "unknown"),
        ("request_hash", "0" * 64),
        ("binding_hash", "0" * 64),
    ],
)
def test_changed_snapshot_bindings_do_not_replay(field, value):
    run, data, _ = example()
    data[field] = value
    with pytest.raises((InvalidMonthlySnapshot, ValueError)):
        restore_input(data, run)


@pytest.mark.parametrize(
    "kind", ["policy", "row", "mapping", "source_hash", "currency", "language"]
)
def test_changed_policy_source_or_presentation_input_is_detected(kind):
    run, data, _ = example()
    if kind == "policy":
        data["close_choices"]["absolute_variation_threshold"] = "1"
    elif kind == "row":
        data["request"]["sources"][0]["rows"][0]["text"] = "999"
    elif kind == "mapping":
        data["mapping_decisions"]["csv:999"] = "revenue"
    elif kind == "source_hash":
        data["request"]["sources"][0]["document"]["content_hash"] = "0" * 64
    elif kind == "currency":
        data["upload_choices"]["source_currency"] = "USD"
    else:
        data["upload_choices"]["report_language"] = "fr"
    with pytest.raises((InvalidMonthlySnapshot, ValueError)):
        restore_input(data, run)


@pytest.mark.parametrize(
    "value",
    [
        {"binding_hash": "0" * 64, "attempt_key": "invalid"},
        {"binding_hash": "invalid", "attempt_key": str(uuid4())},
        {
            "binding_hash": "0" * 64,
            "attempt_key": str(uuid4()),
            "workspace_id": str(uuid4()),
        },
    ],
)
def test_save_contract_forbids_unscoped_or_invalid_confirmation(value):
    with pytest.raises(ValueError):
        SaveMonthEndChoices.model_validate(value)


def test_incomplete_preparation_can_be_preserved_without_becoming_complete():
    parsed, choices, close, _, _, user = month_end_case()
    _, _, result = input_snapshot(
        parsed, choices, close.model_copy(update={"confirmations": []}), user
    )
    value = saved_payload(result, uuid4(), datetime.now(UTC))
    assert value["result"]["preparation_readiness"] == "incomplete"
    assert value["result"]["accounting_close_attested"] is False


def test_snapshot_size_is_bounded_before_storage():
    from saleslab_v2.product.contracts import InvalidUpload

    with pytest.raises(InvalidUpload):
        bounded({"data": "x" * 2_000_000})


@pytest.mark.parametrize("role", ["viewer", "member", "owner", "manager", None])
def test_readability_is_current_workspace_role_and_allowlist_bound(role):
    workspace = uuid4()
    settings = V2Settings(
        enabled=True, persist_stages=True, internal_workspaces=(str(workspace),)
    )
    assert readable(settings, workspace, role) == (role in {"owner", "manager"})
    assert not readable(settings, uuid4(), role)
