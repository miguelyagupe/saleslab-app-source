"""Preview evidence must describe actual results without changing execution contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid5

import pytest
from product_fixtures import csv_content, upload_case
from test_saleslab_v2_cash_reconciliation_upload import cash_rows

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.pipeline.evaluator import evaluate
from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.reconciliation_preview import LABELS, reconciliation_evidence


def case(*, language="pt", kind="cash", status="pass", scale="raw", **updates):
    multiplier = 1000 if scale == "thousands" else 1
    end = {"pass": "320", "rounding_pass": "320.005", "blocking": "321"}[status]
    equity = {"pass": "40", "rounding_pass": "40.005", "blocking": "41"}[status]
    opts = UploadChoices(**{
        "source_language": "en", "format_locale": "en-GB",
        "report_language": language, "planning_version": "6",
        "normalization_version": "2", "target_period": "2026",
        "rounding_tolerance": "0.01",
        "cash_rounding_tolerance": str(Decimal("0.01") * multiplier),
        **updates,
    })
    content = csv_content(
        changes={"equity": {"amount": equity if kind == "balance" else "40"}},
        extra=cash_rows(end=end if kind == "cash" else "320", scale=scale),
    )
    parsed, preview, _, _, user = upload_case(content=content, choices=opts)
    request, _ = prepare_upload(parsed, opts, user, "preview")
    evaluation = evaluate(
        request, uuid5(request.workspace_id, "preview:" + request.fingerprint),
        datetime.now(UTC),
    )
    rule_id = "REC-CF-001" if kind == "cash" else "REC-BS-001"
    result = next(r for r in evaluation.reconciliations if r.rule_id == rule_id)
    check = next(r for r in preview["validation_summary"]["reconciliations"]
                 if r["rule_id"] == rule_id)
    return parsed, preview, request, result, check


@pytest.mark.parametrize("language", ("pt", "en", "es", "fr"))
@pytest.mark.parametrize("kind", ("cash", "balance"))
@pytest.mark.parametrize("status", ("pass", "rounding_pass", "blocking"))
@pytest.mark.parametrize("scale", ("raw", "thousands"))
def test_projection_retains_actual_policy_equations_inputs_and_original_sources(
    language, kind, status, scale
):
    parsed, preview, request, result, check = case(
        language=language, kind=kind, status=status, scale=scale
    )
    evidence = check["reconciliation_evidence"]
    raw = unpack(canonicalize(result.payload()))
    assert check["status"] == status
    assert preview["ready"] == (status != "blocking")
    assert evidence["rule_version"] == raw["rule_version"]
    assert evidence["engine_version"] == raw["engine_version"]
    assert evidence["policy_fingerprint"] == raw["policy_fingerprint"]
    assert evidence["input_fingerprint"] == raw["input_fingerprint"]
    assert {k: v for k, v in evidence["policy"].items()
            if k != "display_rounding_absolute"} == raw["policy_payload"]
    assert evidence["policy"]["display_rounding_absolute"].endswith(" EUR")
    for operand, original in zip(evidence["inputs"], raw["input_references"]):
        assert {k: v for k, v in operand.items()
                if k not in {"label", "display_value", "sources"}} == original
        assert operand["label"] == LABELS[operand["role"]][("pt", "en", "es", "fr").index(language)]
        assert operand["sources"] == [
            row for ref in operand["source_references"]
            for row in parsed.original_rows if row["location"] == ref
        ]
    equation = evidence["equations"][0]
    for key, value in raw["equations"][0].items():
        assert equation[key] == value
    assert equation["canonical_expression"] == (
        "opening_cash+cfo+cfi+cff+fx_other=closing_cash" if kind == "cash"
        else "liabilities+equity=assets"
    )
    assert Decimal(equation["signed_delta"]) == (
        Decimal(equation["absolute_delta"]) * (1 if kind == "cash" else -1)
    )
    assert check["result_hash"] == result.result_hash
    if kind == "cash":
        fx = next(i for i in evidence["inputs"] if i["role"] == "fx_other")
        assert fx["status"] == "calculated"
        assert fx["calculation_trace_hash"]
        assert fx["sources"][0]["amount"] == "0"
        opening = next(i for i in evidence["inputs"] if i["role"] == "opening_cash")
        assert opening["sources"][0]["amount"] == "100"
        assert opening["sources"][0]["scale"] == scale
        assert Decimal(opening["value"]) == (100000 if scale == "thousands" else 100)
        assert opening["period"][1] == "2025-12-31"
    # Projection must not mutate the engine result, request or imported row evidence.
    before = (result.payload(), request.fingerprint, canonicalize(parsed.original_rows))
    reconciliation_evidence(result, parsed.original_rows, language)
    assert before == (result.payload(), request.fingerprint, canonicalize(parsed.original_rows))


def test_mapped_source_label_and_written_number_are_not_replaced_by_canonical_identity():
    rows = cash_rows()
    rows[-1] = ("Our reported FX movements", "0.000", *rows[-1][2:])
    opts = UploadChoices(
        source_language="en", format_locale="en-GB", report_language="pt",
        planning_version="6", normalization_version="2", target_period="2026",
        mappings={"csv:12": "cash_fx_other"},
    )
    _, preview, _, _, _ = upload_case(content=csv_content(extra=rows), choices=opts)
    evidence = next(r for r in preview["validation_summary"]["reconciliations"]
                    if r["rule_id"] == "REC-CF-001")["reconciliation_evidence"]
    source = next(i for i in evidence["inputs"] if i["role"] == "fx_other")["sources"][0]
    assert source["line"] == "Our reported FX movements"
    assert source["amount"] == "0.000"


@pytest.mark.parametrize("failure", ("mapping", "currency"))
def test_unexecuted_checks_never_fabricate_an_evidence_inspector(failure):
    if failure == "mapping":
        _, preview, _, _, _ = upload_case(
            content=csv_content(changes={"revenue": {"line": "unknown account"}})
        )
    else:
        rows = cash_rows()
        rows[-1] = (*rows[-1][:5], "USD", *rows[-1][6:])
        opts = UploadChoices(
            source_language="en", format_locale="en-GB", report_language="pt",
            planning_version="6", normalization_version="2", target_period="2026",
        )
        _, preview, _, _, _ = upload_case(content=csv_content(extra=rows), choices=opts)
    checks = preview["validation_summary"]["reconciliations"]
    unavailable = [r for r in checks if r["status"] == "not_evaluated"]
    assert unavailable
    assert all(r["reconciliation_evidence"] is None for r in unavailable)
    assert not preview["ready"]


def test_missing_original_rows_retain_references_without_inventing_source_content():
    _, _, _, result, _ = case()
    evidence = reconciliation_evidence(result, (), "en")
    assert all(not i["sources"] for i in evidence["inputs"])
    assert all(i["source_references"] for i in evidence["inputs"])


def test_zero_expected_preserves_undefined_relative_delta_and_signed_direction():
    rows = cash_rows(end="1")
    rows[0] = (rows[0][0], "-220", *rows[0][2:])
    opts = UploadChoices(
        source_language="en", format_locale="en-GB", report_language="pt",
        planning_version="6", normalization_version="2", target_period="2026",
    )
    _, preview, _, _, _ = upload_case(content=csv_content(extra=rows), choices=opts)
    check = next(r for r in preview["validation_summary"]["reconciliations"]
                 if r["rule_id"] == "REC-CF-001")
    equation = check["reconciliation_evidence"]["equations"][0]
    assert equation["expected"] == "0"
    assert equation["relative_delta"] is None
    assert equation["relative_delta_kind"] == "undefined_zero_expected"
    assert equation["signed_delta"] == "1"
    assert check["status"] == "blocking"


def test_plan_six_hashes_recorded_before_read_only_projection_are_unchanged():
    workspace = UUID("10000000-0000-4000-8000-000000000001")
    user = UUID("10000000-0000-4000-8000-000000000002")
    opts = UploadChoices(
        source_language="en", format_locale="en-GB", report_language="pt",
        planning_version="6", normalization_version="2", target_period="2026",
    )
    parsed, preview, _, _, _ = upload_case(
        workspace, user, content=csv_content(extra=cash_rows()), choices=opts
    )
    request, _ = prepare_upload(parsed, opts, user, "preview")
    assert preview["preview_hash"] == "4d0a3b541c37fcb100dd55ea5cc07c259655a0a4fd5fb015f2dd8f79615a4523"
    assert request.fingerprint == "ab29d278e01c8a2888acff36019fb237d23033a498c822694c4a7362e4a88ab9"
