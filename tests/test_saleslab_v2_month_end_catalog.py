"""Catalog metadata cannot satisfy a close requirement or rewrite saved evidence."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from close_catalog_fixtures import close_catalog_case
from month_end_upload_fixtures import month_end_case

from saleslab_v2.domain.idempotency import canonicalize, semantic_hash
from saleslab_v2.product.month_end import prepare_month_end
from saleslab_v2.product.month_end_catalog import (
    LABELS,
    LANGUAGES,
    MAX_REQUIRED_RECONCILIATIONS,
    reconciliation_catalog,
)
from saleslab_v2.product.month_end_saved import (
    input_snapshot,
    restore_input,
    semantic_result,
)
from saleslab_v2.reconciliation.contracts import ReconciliationRegistry
from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1


def test_catalog_enumerates_every_registered_rule_and_its_versions_without_defaults():
    value = reconciliation_catalog()
    expected = {}
    for rule in FINANCIAL_RULES_V1.rules:
        expected.setdefault(rule.rule_id, set()).add(rule.version)
    assert len(value["rules"]) == len(expected) == 14
    assert {r["rule_id"]: set(r["versions"]) for r in value["rules"]} == expected
    assert value["selection"] == "explicit"
    assert value["max_required_reconciliations"] == MAX_REQUIRED_RECONCILIATIONS == 20
    assert value["result_period_requirement"] == "target_month_current_currency_scope"
    assert all(not r["label_fallback"] for r in value["rules"])
    assert [r["rule_id"] for r in value["rules"]] == sorted(expected)
    assert not any(
        key in json.dumps(value)
        for key in ('"selected"', '"satisfied"', '"workspace_id"', '"source_hash"')
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_rule_has_an_explicit_localized_label_and_unmodified_identifier(language):
    value = reconciliation_catalog()
    assert set(LABELS) == {r["rule_id"] for r in value["rules"]}
    assert all(
        set(r["labels"]) == set(LANGUAGES) and r["labels"][language]
        for r in value["rules"]
    )


def test_catalog_is_deterministic_detached_and_independent_of_registry_order():
    first = reconciliation_catalog()
    reordered = ReconciliationRegistry(reversed(FINANCIAL_RULES_V1.rules))
    assert reconciliation_catalog(reordered) == first
    before = deepcopy(first)
    first["rules"][0]["labels"]["pt"] = "changed client value"
    first["rules"][0]["versions"].append("future")
    assert reconciliation_catalog() == before
    assert semantic_hash(before) == semantic_hash(reconciliation_catalog())


def test_future_registered_rule_is_discovered_with_visible_canonical_fallback():
    future = replace(
        FINANCIAL_RULES_V1.rules[0], rule_id="REC-FUTURE-001", version="10"
    )
    another = replace(future, version="2")
    registry = ReconciliationRegistry((*FINANCIAL_RULES_V1.rules, future, another))
    result = next(
        r
        for r in reconciliation_catalog(registry)["rules"]
        if r["rule_id"] == future.rule_id
    )
    assert result["versions"] == ["2", "10"]
    assert result["label_fallback"] is True
    assert set(result["labels"].values()) == {future.rule_id}


@pytest.mark.parametrize(
    "field,value",
    [
        ("rule_id", "REC-<script>"),
        ("rule_id", "REC-" + "X" * 101),
        ("version", "bad version"),
        ("version", "x" * 33),
    ],
)
def test_catalog_rejects_unrenderable_internal_identifiers(field, value):
    rule = replace(FINANCIAL_RULES_V1.rules[0], **{field: value})
    with pytest.raises(ValueError):
        reconciliation_catalog(ReconciliationRegistry([rule]))


def test_new_working_capital_requirements_are_satisfied_only_by_month_scoped_results():
    parsed, choices, close, _, user = close_catalog_case()
    value = prepare_month_end(parsed, choices, close, user)
    assert value["result"]["preparation_readiness"] == "ready_for_review"
    recs = [
        i for i in value["result"]["checklist"] if i["category"] == "reconciliation"
    ]
    assert {i["item_id"] for i in recs} == {
        "reconciliation/REC-WC-CLOSING-001",
        "reconciliation/REC-WC-CHANGE-001",
    }
    assert all(i["status"] == "satisfied" and i["evidence"] for i in recs)
    assert value["quota_consumed"] is value["persisted"] is False
    assert value["result"]["accounting_close_attested"] is False


@pytest.mark.parametrize(
    "rule_id",
    ["REC-CAPEX-001", "REC-DEBT-RATE-001", "REC-GROWTH-001", "REC-WC-OPENING-001"],
)
def test_catalog_membership_does_not_substitute_for_missing_or_other_month_evidence(
    rule_id,
):
    parsed, choices, close, _, user = close_catalog_case(required=[rule_id])
    assert rule_id in {r["rule_id"] for r in reconciliation_catalog()["rules"]}
    value = prepare_month_end(parsed, choices, close, user)
    rec = next(
        i for i in value["result"]["checklist"] if i["category"] == "reconciliation"
    )
    assert rec["status"] == "pending" and rec["evidence"] == []
    assert rec["reason_code"] == "month_end.reconciliation_missing_or_ambiguous"
    assert value["result"]["preparation_readiness"] == "incomplete"


def test_material_source_difference_cannot_pass_merely_because_rule_is_selectable():
    parsed, choices, close, _, user = close_catalog_case(changes={"change": {1: "21"}})
    value = prepare_month_end(parsed, choices, close, user)
    rec = next(
        i
        for i in value["result"]["checklist"]
        if i["item_id"] == "reconciliation/REC-WC-CHANGE-001"
    )
    assert (
        rec["status"] == "pending"
        and value["result"]["preparation_readiness"] == "incomplete"
    )


@pytest.mark.parametrize("modern", [False, True])
def test_old_and_new_policies_keep_identical_snapshot_replay_without_catalog_metadata(
    modern,
):
    if modern:
        parsed, choices, close, _, user = close_catalog_case()
    else:
        parsed, choices, close, _, _, user = month_end_case()
    request, data, prepared = input_snapshot(parsed, choices, close, user)
    run = SimpleNamespace(
        workspace_id=request.workspace_id,
        created_by_user_id=user,
        idempotency_key=str(uuid4()),
        language_context=request.language.to_dict(),
        created_at=datetime.now(UTC),
    )
    saved = json.loads(json.dumps(canonicalize(data)))
    with patch(
        "saleslab_v2.product.month_end_catalog.reconciliation_catalog",
        side_effect=AssertionError("Catalog is not snapshot evidence"),
    ):
        restored, fresh = restore_input(saved, run)
    assert restored.fingerprint == request.fingerprint
    assert semantic_result(fresh) == semantic_result(prepared)
    assert (
        data["close_choices"]["required_reconciliations"]
        == close.required_reconciliations
    )
    assert not any("catalog" in key for key in data)


def test_catalog_version_count_matches_client_contract_limit():
    rules = [replace(FINANCIAL_RULES_V1.rules[0], version=str(i)) for i in range(21)]
    assert (
        len(
            reconciliation_catalog(ReconciliationRegistry(rules[:20]))["rules"][0][
                "versions"
            ]
        )
        == 20
    )
    with pytest.raises(ValueError, match="at most 20 versions"):
        reconciliation_catalog(ReconciliationRegistry(rules))
