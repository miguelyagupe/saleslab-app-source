"""ROIC evidence binds source definitions without inventing tax assumptions."""

from copy import deepcopy

import month_end_upload_fixtures  # noqa: F401
import pytest
from pydantic import ValidationError

from saleslab_v2.product.contracts import InvalidUpload
from saleslab_v2.product.roic_source import RoicSourceChoices, bind_roic_sources


def case():
    rows = [
        {
            "location": "csv:2",
            "line": "Reported NOPAT after operating taxes",
            "amount": "80",
        },
        {
            "location": "csv:3",
            "line": "Operating tax assumption is 20 percent",
            "amount": "20",
        },
        {
            "location": "csv:4",
            "line": "Average invested capital over reporting period",
            "amount": "400",
        },
    ]
    return rows, RoicSourceChoices(
        nopat_location="csv:2",
        nopat_definition=rows[0]["line"],
        tax_assumptions_location="csv:3",
        tax_assumptions=rows[1]["line"],
        average_capital_location="csv:4",
        average_capital_definition=rows[2]["line"],
    )


def test_binding_is_deterministic_detached_and_does_not_calculate_roic():
    rows, choices = case()
    saved = deepcopy(rows)
    value = bind_roic_sources(rows, choices)
    assert value == bind_roic_sources(list(reversed(rows)), choices)
    assert rows == saved
    assert set(value) == {"schema", "choices", "evidence", "binding_hash"}
    rows[0]["amount"] = "90"
    assert value["binding_hash"] != bind_roic_sources(rows, choices)["binding_hash"]
    assert value["choices"]["nopat_definition"] == saved[0]["line"]


@pytest.mark.parametrize("index", range(3))
def test_missing_or_duplicate_source_location_is_rejected(index):
    rows, choices = case()
    with pytest.raises(InvalidUpload):
        bind_roic_sources(rows[:index] + rows[index + 1 :], choices)
    with pytest.raises(InvalidUpload):
        bind_roic_sources(rows + [deepcopy(rows[index])], choices)


@pytest.mark.parametrize(
    "field", ["nopat_definition", "tax_assumptions", "average_capital_definition"]
)
def test_invented_definition_is_rejected(field):
    rows, choices = case()
    choices = RoicSourceChoices.model_validate(
        {**choices.model_dump(), field: "Invented unsupported definition"}
    )
    with pytest.raises(InvalidUpload):
        bind_roic_sources(rows, choices)


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": "untrusted"},
        {"tax_assumptions": ""},
        {"nopat_definition": " trailing quotation "},
        {"average_capital_location": "csv:2"},
        {"tax_assumptions_location": 3},
    ],
)
def test_choices_reject_incomplete_untyped_or_actor_supplied_decisions(change):
    _, choices = case()
    with pytest.raises(ValidationError):
        RoicSourceChoices.model_validate({**choices.model_dump(), **change})
