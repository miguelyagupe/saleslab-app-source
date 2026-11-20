"""Original row evidence and localized guidance cannot change financial decisions."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from product_fixtures import csv_content, upload_case

from saleslab_v2.domain.financial import NormalizationIssue
from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.row_preview import GUIDANCE, row_preview


@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize(
    "changes,code",
    (
        ({"amount": "(100)"}, "number.parentheses_sign_ambiguous"),
        ({"amount": "(-100)"}, "number.parentheses_sign_ambiguous"),
        ({"amount": "1,23.00"}, "number.invalid_grouping"),
        ({"currency": ""}, "currency.missing"),
        ({"scale": ""}, "scale.missing"),
        ({"period": "unknown"}, "period.year_required"),
        ({"line": "Private original label"}, "mapping.unknown_label"),
    ),
)
def test_guidance_tracks_actual_issues_and_preserves_original_input(
    language, changes, code
):
    choices = UploadChoices(
        source_language="en", format_locale="en-GB", report_language=language
    )
    parsed, preview, _, _, _ = upload_case(
        choices=choices, content=csv_content(changes={"net_income": changes})
    )
    row = next(r for r in preview["rows"] if r["location"] == "csv:3")
    original = next(r for r in parsed.original_rows if r["location"] == "csv:3")
    assert row["original_input"] == original
    assert code in [i["code"] for i in row["validation_guidance"]]
    assert [(i["code"], i["field"]) for i in row["validation_guidance"]] == [
        (i["code"], i["field"]) for i in row["issues"]
    ]
    key = code if code in GUIDANCE else code.split(".")[0]
    message = GUIDANCE[key][("pt", "en", "es", "fr").index(language)]
    assert message in [i["message"] for i in row["validation_guidance"]]
    assert not preview["ready"]


def test_projection_changes_do_not_change_bindings_or_request():
    parsed, preview, choices, _, user = upload_case(
        content=csv_content(changes={"net_income": {"amount": "(100)"}})
    )
    request, before = prepare_upload(parsed, choices, user, "preview")
    with patch.dict(GUIDANCE, {"number.parentheses_sign_ambiguous": ("new copy",) * 4}):
        updated_request, after = prepare_upload(parsed, choices, user, "preview")
    assert before["rows"] != after["rows"]
    assert preview["preview_hash"] == before["preview_hash"] == after["preview_hash"]
    assert request.fingerprint == updated_request.fingerprint
    assert before["ready"] == after["ready"]
    assert before["planned_metrics"] == after["planned_metrics"]
    assert before["calculated_metrics"] == after["calculated_metrics"]


def test_clean_rows_and_confirmed_parentheses_have_no_spurious_guidance():
    choices = UploadChoices(
        source_language="en",
        format_locale="en-GB",
        report_language="pt",
        accounting_parentheses=True,
    )
    _, preview, _, _, _ = upload_case(
        choices=choices,
        content=csv_content(changes={"net_income": {"amount": "(100)"}}),
    )
    row = next(r for r in preview["rows"] if r["concept_id"] == "net_income")
    assert row["original_input"]["amount"] == "(100)"
    assert row["value"] == "-100"
    assert row["validation_guidance"] == []
    assert preview["ready"]


def test_unknown_issue_has_localized_fallback_without_echoing_details():
    parsed, _, choices, _, user = upload_case()
    request, _ = prepare_upload(parsed, choices, user, "preview")
    from saleslab_v2.normalization.financial import FinancialNormalizer

    observation = FinancialNormalizer().normalize(
        list(request.sources[0].rows), parsed.batch.metadata
    )[0]
    observation = replace(
        observation,
        normalization_status="needs_confirmation",
        issues=(
            NormalizationIssue("future.check", "value", (("sensitive", "PRIVATE"),)),
        ),
    )
    for index, language in enumerate(("pt", "en", "es", "fr")):
        row = row_preview(observation, parsed.original_rows[0], language)
        assert row["validation_guidance"] == [
            {
                "code": "future.check",
                "field": "value",
                "message": GUIDANCE["fallback"][index],
            }
        ]
        assert "PRIVATE" not in row["validation_guidance"][0]["message"]


def test_duplicate_candidates_keep_each_original_row_and_do_not_select_one():
    _, preview, _, _, _ = upload_case(
        content=csv_content(
            extra=[
                (
                    "net income",
                    "200",
                    "2026",
                    "income_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                )
            ]
        )
    )
    rows = [r for r in preview["rows"] if r["concept_id"] == "net_income"]
    assert len(rows) == 2
    assert {r["original_input"]["amount"] for r in rows} == {"100", "200"}
    assert all(
        "observation.duplicate_candidate"
        in [g["code"] for g in r["validation_guidance"]]
        for r in rows
    )
    assert "net_margin_pct" not in preview["planned_metrics"]


@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
def test_sign_conflicts_and_positive_cash_outflows_keep_distinct_guidance(language):
    choices = UploadChoices(
        source_language="en",
        format_locale="en-GB",
        report_language=language,
        accounting_parentheses=True,
        planning_version="3",
    )
    _, preview, _, _, _ = upload_case(
        choices=choices,
        content=csv_content(
            changes={"net_income": {"amount": "(-100)"}},
            extra=[
                (
                    "purchases of property plant and equipment",
                    "80",
                    "2026",
                    "cash_flow_statement",
                    "consolidated",
                    "EUR",
                    "raw",
                    "current",
                )
            ],
        ),
    )
    income = next(r for r in preview["rows"] if r["concept_id"] == "net_income")
    capex = next(r for r in preview["rows"] if r["concept_id"] == "capex")
    assert income["validation_guidance"][0]["code"] == "number.conflicting_sign"
    assert "sign.convention_confirmation_required" in [
        g["code"] for g in capex["validation_guidance"]
    ]
    assert capex["original_input"]["amount"] == "80"
    assert capex["value"] == "80"
    assert not preview["ready"]


def test_xlsx_original_cells_are_exposed_without_replacing_mapped_labels():
    import io

    from openpyxl import Workbook

    from saleslab_v2.product.ingestion import HEADERS

    book = Workbook()
    sheet = book.active
    sheet.append(HEADERS)
    sheet.append(
        (
            "Our sales",
            "1,234.50",
            "2026",
            "income_statement",
            "consolidated",
            "EUR",
            "thousands",
            "current",
        )
    )
    content = io.BytesIO()
    book.save(content)
    choices = UploadChoices(
        source_language="en",
        format_locale="en-GB",
        report_language="fr",
        mappings={"sheet1:2": "revenue"},
    )
    _, preview, _, _, _ = upload_case(
        filename="input.xlsx", content=content.getvalue(), choices=choices
    )
    row = preview["rows"][0]
    assert row["original_input"] == dict(
        zip(
            ("location", *HEADERS),
            (
                "sheet1:2",
                "Our sales",
                "1,234.50",
                "2026",
                "income_statement",
                "consolidated",
                "EUR",
                "thousands",
                "current",
            ),
        )
    )
    assert row["original_line"] == "Our sales"
    assert row["concept_id"] == "revenue"
    assert row["value"] == "1234500.00"
    assert row["validation_guidance"] == []
