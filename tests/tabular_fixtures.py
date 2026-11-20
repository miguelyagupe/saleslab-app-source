"""Synthetic ERP-style worksheets with explicit layout confirmations."""

import io

from openpyxl import Workbook

from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.tabular import inspect_upload


def workbook_content():
    book = Workbook()
    income = book.active
    income.title = "Resultados"
    income.append(["Exportação financeira"])
    income.merge_cells("A1:D1")
    income.append([])
    income.append(["Conta", "2025", "2026", "Nota"])
    income.append(["revenue", 800, 1000, "Receita"])
    income.append(["net income", 80, 100, "Resultado"])
    balance = book.create_sheet("Balanço")
    balance.append(["Mapa do balanço"])
    balance.append(["Conta", "2025", "2026"])
    balance.append(["total assets", 1200, 1500])
    balance.append(["total liabilities", 800, 1000])
    balance.append(["equity", 400, 500])
    notes = book.create_sheet("Notas")
    notes.append(["Dados não selecionados"])
    notes.append(["=1+1"])
    notes.sheet_state = "hidden"
    target = io.BytesIO()
    book.save(target)
    book.close()
    return target.getvalue()


def table(
    sheet, header, first, last, statement, *, amount="C", period="2026", basis="current"
):
    return {
        "sheet": sheet,
        "header_row": header,
        "first_row": first,
        "last_row": last,
        "fields": {
            "line": {"column": "A"},
            "amount": {"column": amount},
            **{
                key: {"constant": value}
                for key, value in {
                    "period": period,
                    "statement_type": statement,
                    "scope": "consolidated",
                    "currency": "EUR",
                    "scale": "raw",
                    "comparison_basis": basis,
                }.items()
            },
        },
    }


def import_choices(
    content=None, *, filename="erp.xlsx", delimiter=None, tables=None, **kwargs
):
    content = workbook_content() if content is None else content
    inspection = inspect_upload(filename, content, delimiter)
    return UploadChoices(
        source_language="en",
        format_locale="en-GB",
        report_language="pt",
        planning_version="8",
        normalization_version="2",
        target_period="2026",
        tabular_import={
            "version": "1",
            "inspection_hash": inspection["inspection_hash"],
            "delimiter": delimiter,
            "tables": tables
            or [
                table(1, 3, 4, 5, "income_statement"),
                table(2, 2, 3, 5, "balance_sheet"),
            ],
            "excluded_content_confirmed": True,
        },
        **kwargs,
    )
