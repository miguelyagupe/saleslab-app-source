"""Explicit tabular preparation, exact cells, provenance and legacy isolation."""

import io
import json
import zipfile
from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook, load_workbook
from product_fixtures import csv_content, upload_case
from tabular_fixtures import import_choices, table, workbook_content

from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.product.contracts import InvalidUpload
from saleslab_v2.product.ingestion import HEADERS, parse_upload
from saleslab_v2.product.planning import prepare_upload
from saleslab_v2.product.tabular import inspect_upload
from saleslab_v2.product.tabular_contracts import TabularImport


def change_book(content, change):
    book = load_workbook(io.BytesIO(content))
    change(book)
    target = io.BytesIO()
    book.save(target)
    book.close()
    return target.getvalue()


def rewrite_xml(content, path, change):
    target = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(
        target, "w"
    ) as out:
        for name in source.namelist():
            data = source.read(name)
            out.writestr(name, change(data) if name == path else data)
    return target.getvalue()


def test_inspection_preserves_all_sheet_inventory_without_executing_formulas():
    content = workbook_content()
    inspected = inspect_upload("erp.xlsx", content)
    assert inspected == inspect_upload("erp.xlsx", content)
    assert [s["name"] for s in inspected["sheets"]] == [
        "Resultados",
        "Balanço",
        "Notas",
    ]
    assert inspected["sheets"][2]["hidden"]
    assert inspected["sheets"][2]["formula_count"] == 1
    assert inspected["sheets"][2]["sample_rows"][1]["cells"][0]["value"] == "=1+1"
    assert inspected["source_hash"]


def test_multiple_selected_tables_preserve_cells_metadata_and_exclusions():
    content = workbook_content()
    opts = import_choices(content)
    p, v, *_ = upload_case(content=content, filename="erp.xlsx", choices=opts)
    assert v["ready"]
    assert len(p.batch.rows) == 5
    assert [r.source_reference for r in p.batch.rows] == [
        "s1!C4",
        "s1!C5",
        "s2!C3",
        "s2!C4",
        "s2!C5",
    ]
    assert p.original_rows[0]["source_cells"]["line"] == {"cell": "A4"}
    assert p.original_rows[0]["source_cells"]["amount"] == {"cell": "C4"}
    assert p.original_rows[0]["source_cells"]["period"] == {"constant": "2026"}
    assert p.batch.document.parser_version == "financial-tabular-v1"
    assert json.loads(json.dumps(p.import_manifest)) == p.import_manifest
    assert v["source_import"]["excluded_sheets"][0]["sheet"] == 3
    assert "sample_rows" not in v["source_import"]["excluded_sheets"][0]
    assert v["source_import"]["tables"][0]["excluded_columns"] == ["B", "D"]
    margin = next(
        m for m in v["calculated_metrics"] if m["metric_id"] == "net_margin_pct"
    )
    assert Decimal(margin["exact_value"]) == 10
    assert (
        margin["calculation_evidence"]["inputs"][0]["sources"][0]["location"] == "s1!C5"
    )
    assert v["rows"][0]["original_input"]["sheet"] == "Resultados"


def test_explicit_wide_period_selections_do_not_infer_years_from_headers():
    content = workbook_content()
    tables = [
        table(1, 3, 4, 5, "income_statement"),
        table(
            1,
            3,
            4,
            5,
            "income_statement",
            amount="B",
            period="2025",
            basis="comparative",
        ),
        table(2, 2, 3, 5, "balance_sheet"),
    ]
    _, v, *_ = upload_case(
        content=content,
        filename="erp.xlsx",
        choices=import_choices(content, tables=tables),
    )
    assert v["ready"]
    growth = next(
        m
        for m in v["calculated_metrics"]
        if m["metric_id"] == "revenue_growth_reported_pct"
    )
    assert Decimal(growth["exact_value"]) == 25
    assert {r["location"] for r in v["rows"]} >= {"s1!B4", "s1!C4"}


@pytest.mark.parametrize("delimiter", (";", ",", "\t"))
def test_arbitrary_csv_columns_and_source_metadata_columns(delimiter):
    import csv

    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerows(
        [
            [
                "Unrelated",
                "Valor",
                "Conta",
                "Ano",
                "Tipo",
                "Moeda",
                "Escala",
                "Scope",
                "Base",
            ],
            [
                "note",
                "1000",
                "revenue",
                "2026",
                "income_statement",
                "EUR",
                "raw",
                "consolidated",
                "current",
            ],
            [
                "note",
                "100",
                "net income",
                "2026",
                "income_statement",
                "EUR",
                "raw",
                "consolidated",
                "current",
            ],
        ]
    )
    content = out.getvalue().encode()
    fields = dict(
        zip(HEADERS, ({"column": c} for c in ("C", "B", "D", "E", "H", "F", "G", "I")))
    )
    t = {"sheet": 1, "header_row": 1, "first_row": 2, "last_row": 3, "fields": fields}
    opts = import_choices(content, filename="erp.csv", delimiter=delimiter, tables=[t])
    _, v, *_ = upload_case(content=content, filename="erp.csv", choices=opts)
    assert v["ready"] and v["rows"][0]["location"] == "s1!B2"


@pytest.mark.parametrize("locale", ("en-GB", "pt-PT", "es-ES", "fr-FR"))
def test_xlsx_numeric_xml_is_exact_and_separate_from_text_locale(locale):
    exact = "1000.123456789012345678901234567890123456789"
    content = rewrite_xml(
        workbook_content(),
        "xl/worksheets/sheet1.xml",
        lambda b: b.replace(b"<v>1000</v>", ("<v>" + exact + "</v>").encode()),
    )
    opts = import_choices(content).model_copy(update={"format_locale": locale})
    p, v, *_ = upload_case(content=content, filename="erp.xlsx", choices=opts)
    assert v["ready"]
    revenue = next(r for r in v["rows"] if r["concept_id"] == "revenue")
    assert Decimal(revenue["value"]) == Decimal(exact)
    assert p.original_rows[0]["amount"] == (
        exact if locale == "en-GB" else exact.replace(".", ",")
    )


@pytest.mark.parametrize(
    "change",
    (
        lambda d: d.update(excluded_content_confirmed=False),
        lambda d: d["tables"][0].update(header_row=4),
        lambda d: d["tables"][0].update(last_row=1001),
        lambda d: d["tables"][0]["fields"].pop("scope"),
        lambda d: d["tables"][0]["fields"]["amount"].update(column="AG"),
        lambda d: d["tables"][0]["fields"]["amount"].update(column="A"),
        lambda d: d["tables"][0]["fields"]["amount"].update(constant="100"),
        lambda d: d.update(tables=[]),
        lambda d: d.update(tables=d["tables"] * 7),
    ),
)
def test_layout_contract_requires_explicit_bounded_nonconflicting_decisions(change):
    value = import_choices().tabular_import.model_dump()
    change(value)
    with pytest.raises(ValueError):
        TabularImport.model_validate(value)


@pytest.mark.parametrize(
    "change",
    (
        lambda d: d.update(inspection_hash="0" * 64),
        lambda d: d["tables"][0].update(sheet=8),
        lambda d: d["tables"][0].update(last_row=900),
        lambda d: d["tables"][0]["fields"]["amount"].update(column="AF"),
        lambda d: d["tables"].append(deepcopy(d["tables"][0])),
    ),
)
def test_stale_or_overlapping_selections_never_guess_or_double_count(change):
    content = workbook_content()
    opts = import_choices(content)
    value = opts.tabular_import.model_dump()
    change(value)
    opts = opts.model_copy(
        update={"tabular_import": TabularImport.model_validate(value)}
    )
    with pytest.raises(InvalidUpload):
        parse_upload(uuid4(), "erp.xlsx", content, opts)


def test_selection_order_and_canonical_binding_are_bound_but_dictionary_order_is_not():
    content = workbook_content()
    opts = import_choices(content)
    w, u = uuid4(), uuid4()
    p, v, *_ = upload_case(w, u, content=content, filename="erp.xlsx", choices=opts)
    original, _ = prepare_upload(p, opts, u, "preview")
    value = opts.tabular_import.model_dump()
    for t in value["tables"]:
        t["fields"] = dict(reversed(list(t["fields"].items())))
    reordered = opts.model_copy(
        update={"tabular_import": TabularImport.model_validate(value)}
    )
    q, check, *_ = upload_case(
        w, u, content=content, filename="erp.xlsx", choices=reordered
    )
    other, _ = prepare_upload(q, reordered, u, "preview")
    assert v["preview_hash"] == check["preview_hash"]
    assert original.fingerprint == other.fingerprint
    value["tables"][0]["header_row"] = 2
    changed = opts.model_copy(
        update={"tabular_import": TabularImport.model_validate(value)}
    )
    q, check, *_ = upload_case(
        w, u, content=content, filename="erp.xlsx", choices=changed
    )
    assert v["preview_hash"] != check["preview_hash"]
    assert p.batch.document.source_id != q.batch.document.source_id


@pytest.mark.parametrize(
    "change",
    (
        lambda b: b["Resultados"].__setitem__("C4", "=SUM(B4)"),
        lambda b: b["Resultados"].__setitem__("C4", True),
        lambda b: b["Resultados"].merge_cells("A4:C4"),
    ),
)
def test_selected_formulas_booleans_and_merged_values_are_rejected(change):
    content = change_book(workbook_content(), change)
    with pytest.raises(InvalidUpload):
        parse_upload(uuid4(), "erp.xlsx", content, import_choices(content))


def test_formula_in_unselected_column_is_not_evaluated_or_included():
    content = change_book(
        workbook_content(), lambda b: b["Resultados"].__setitem__("D4", "=1/0")
    )
    _, v, *_ = upload_case(
        content=content, filename="erp.xlsx", choices=import_choices(content)
    )
    assert v["ready"]
    assert all("=1/0" not in json.dumps(r) for r in v["rows"])


@pytest.mark.parametrize(
    "content", (b"", b"a;b\n" * 1001, b";".join([b"x"] * 33), b'"unterminated')
)
def test_inspection_bounds_csv_and_rejects_malformed_sources(content):
    with pytest.raises(InvalidUpload):
        inspect_upload("erp.csv", content, ";")


def test_sparse_workbook_and_xml_entities_are_rejected_before_expansion():
    content = rewrite_xml(
        workbook_content(),
        "xl/worksheets/sheet1.xml",
        lambda b: b.replace(b'r="C4"', b'r="AF1001"'),
    )
    with pytest.raises(InvalidUpload):
        inspect_upload("erp.xlsx", content)
    content = rewrite_xml(
        workbook_content(),
        "xl/worksheets/sheet1.xml",
        lambda b: b'<!DOCTYPE foo [<!ENTITY x "text">]>' + b,
    )
    with pytest.raises(InvalidUpload):
        inspect_upload("erp.xlsx", content)


def test_aggregate_selected_row_limit_and_blank_rows_are_explicit():
    book = Workbook()
    sheet = book.active
    sheet.append(["line", "amount"])
    for _ in range(501):
        sheet.append(["revenue", 1])
    out = io.BytesIO()
    book.save(out)
    book.close()
    content = out.getvalue()
    opts = import_choices(
        content, tables=[table(1, 1, 2, 502, "income_statement", amount="B")]
    )
    with pytest.raises(InvalidUpload, match="500"):
        parse_upload(uuid4(), "erp.xlsx", content, opts)
    content = change_book(
        workbook_content(), lambda b: b["Resultados"].__setitem__("A6", None)
    )
    # Blank physical rows are reported; nonempty rows with missing selected values are not discarded.
    opts = import_choices(content)
    assert len(parse_upload(uuid4(), "erp.xlsx", content, opts).batch.rows) == 5


def test_old_template_and_pre_change_choices_have_identical_hashes():
    from profitability_fixtures import profitability_choices, profitability_rows

    w = UUID("10000000-0000-4000-8000-000000000001")
    u = UUID("10000000-0000-4000-8000-000000000002")
    opts = profitability_choices()
    p, v, *_ = upload_case(
        w, u, content=csv_content(extra=profitability_rows()), choices=opts
    )
    request, _ = prepare_upload(p, opts, u, "preview")
    assert (
        v["preview_hash"]
        == "9941760271d8d3f729e3d5ee7a31777c44bff3aaba19b2efeadc34f3b0e8db72"
    )
    assert (
        request.fingerprint
        == "8f707055da3ec2f2e690df343c8676f4e74179cb40e89c512021b9418b2f6584"
    )
    assert "tabular_import" not in opts.contract_payload()
    assert "source_import" not in v
    assert p.import_manifest is None
    assert semantic_hash(opts.contract_payload())


def test_complete_capital_import_preserves_metrics_audit_and_bounded_publication():
    import csv
    from unittest.mock import patch

    from analyst_fixtures import model_output
    from capital_fixtures import capital_content
    from report_fixtures import report_fixture

    from saleslab_v2.reports.render import render_channels

    grouped = {}
    for row in list(csv.reader(io.StringIO(capital_content().decode()), delimiter=";"))[
        1:
    ]:
        grouped.setdefault(row[3], []).append(row)
    book = Workbook()
    book.remove(book.active)
    selections = []
    for number, (statement, rows) in enumerate(grouped.items(), 1):
        sheet = book.create_sheet(statement[:31])
        sheet.append(list(HEADERS))
        for row in rows:
            sheet.append(row)
        selections.append(
            {
                "sheet": number,
                "header_row": 1,
                "first_row": 2,
                "last_row": len(rows) + 1,
                "fields": {
                    role: {"column": chr(65 + index)}
                    for index, role in enumerate(HEADERS)
                },
            }
        )
    target = io.BytesIO()
    book.save(target)
    book.close()
    content = target.getvalue()
    opts = import_choices(content, tables=selections)
    p, preview, _, _, user = upload_case(
        content=content, filename="erp.xlsx", choices=opts
    )
    assert preview["ready"] and len(preview["calculated_metrics"]) == 63
    request, _ = prepare_upload(p, opts, user, "preview")

    def selected(context):
        raw = model_output(context)
        raw["findings"] = raw["findings"][:20]
        return raw

    with patch("report_fixtures.request", return_value=request), patch(
        "audit_fixtures.model_output", side_effect=selected
    ):
        report, (_, _, context, _, draft, audit, checks) = report_fixture(growth=False)
    assert audit["status"] in {"approved", "approved_with_warnings"}
    assert not any(c["status"] == "block" for c in checks)
    assert len(report["metric_observations"]) == 63
    assert (
        len(
            json.dumps(
                {"evidence_context": context, "draft": draft}, ensure_ascii=True
            ).encode()
        )
        < 384000
    )
    report["render_version"] = "financial-render-v4"
    assert render_channels(report)["parity"]["status"] == "pass"


def test_inspection_multipart_limit_covers_declared_and_streamed_bodies():
    import asyncio

    from fastapi import FastAPI, Request
    from httpx import ASGITransport, AsyncClient

    from saleslab_v2.product.limits import FinancialUploadLimit

    app = FastAPI()
    app.add_middleware(FinancialUploadLimit)

    @app.post("/financial-v2/inspect")
    async def inspect_body(request: Request):
        await request.body()
        return {"ok": True}

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            result = await client.post(
                "/financial-v2/inspect",
                content=b"x",
                headers={"Content-Length": str(7 * 1024 * 1024)},
            )
            assert result.status_code == 413

            async def chunks():
                for _ in range(7):
                    yield b"x" * (1024 * 1024)

            result = await client.post("/financial-v2/inspect", content=chunks())
            assert result.status_code == 413

    asyncio.run(scenario())


@pytest.mark.parametrize("value", (1, "true", False, None))
def test_confirmation_is_an_explicit_boolean(value):
    selected = import_choices().tabular_import.model_dump()
    selected["excluded_content_confirmed"] = value
    with pytest.raises(ValueError):
        TabularImport.model_validate(selected)


def test_excel_dates_are_not_reinterpreted_as_numeric_financial_amounts():
    from datetime import datetime

    content = change_book(
        workbook_content(),
        lambda b: b["Resultados"].__setitem__("C4", datetime(2026, 1, 1)),
    )
    with pytest.raises(InvalidUpload, match="explicit numeric"):
        parse_upload(uuid4(), "erp.xlsx", content, import_choices(content))


def test_chart_sheets_and_aliased_worksheet_relationships_are_rejected():
    from openpyxl.chart import BarChart, Reference

    def add_chart(book):
        chart = BarChart()
        chart.add_data(Reference(book["Resultados"], min_col=3, min_row=4, max_row=5))
        sheet = book.create_chartsheet("Chart", 1)
        sheet.add_chart(chart)

    content = change_book(workbook_content(), add_chart)
    with pytest.raises(InvalidUpload, match="data worksheets"):
        inspect_upload("erp.xlsx", content)
    content = rewrite_xml(
        workbook_content(),
        "xl/_rels/workbook.xml.rels",
        lambda data: data.replace(
            b"/xl/worksheets/sheet2.xml", b"/xl/worksheets/sheet1.xml"
        ),
    )
    with pytest.raises(InvalidUpload, match="distinct"):
        inspect_upload("erp.xlsx", content)


@pytest.mark.parametrize("position", (0, 3))
def test_empty_worksheets_are_inventoried_without_blocking_populated_selections(
    position,
):
    content = change_book(
        workbook_content(), lambda b: b.create_sheet("Empty", position)
    )
    inspection = inspect_upload("erp.xlsx", content)
    empty = inspection["sheets"][position]
    assert empty["name"] == "Empty"
    assert empty["row_count"] == empty["column_count"] == 0
    assert empty["sample_rows"] == []
    offset = 1 if position == 0 else 0
    opts = import_choices(
        content,
        tables=[
            table(1 + offset, 3, 4, 5, "income_statement"),
            table(2 + offset, 2, 3, 5, "balance_sheet"),
        ],
    )
    _, preview, *_ = upload_case(content=content, filename="erp.xlsx", choices=opts)
    assert preview["ready"]
    assert any(
        s["name"] == "Empty" for s in preview["source_import"]["excluded_sheets"]
    )


def test_merged_section_titles_in_excluded_gap_do_not_intersect_selected_cells():
    book = Workbook()
    sheet = book.active
    sheet.append(["Account", "Amount"])
    sheet.append(["Excluded section title"])
    sheet.merge_cells("A2:B2")
    sheet.append(["revenue", 1000])
    sheet.append(["net income", 100])
    out = io.BytesIO()
    book.save(out)
    book.close()
    content = out.getvalue()
    opts = import_choices(
        content, tables=[table(1, 1, 3, 4, "income_statement", amount="B")]
    )
    _, preview, *_ = upload_case(content=content, filename="erp.xlsx", choices=opts)
    assert preview["ready"]
    assert preview["source_import"]["sheet_coverage"][0]["excluded_row_ranges"] == [
        [1, 2]
    ]
