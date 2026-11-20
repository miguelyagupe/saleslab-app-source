"""Bounded grid inspection and explicitly confirmed tabular extraction."""

import csv
import io
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal
from hashlib import sha256
from itertools import islice
from pathlib import PurePath
from xml.etree import ElementTree

from openpyxl import load_workbook
from openpyxl.utils.cell import (
    column_index_from_string,
    get_column_letter,
    range_boundaries,
)

from saleslab_v2.domain.idempotency import semantic_hash

from .contracts import InvalidUpload
from .tabular_contracts import FIELDS

VERSION = "financial-tabular-v1"
MAX_ROWS, MAX_COLUMNS, MAX_SHEETS, MAX_CELLS = 1000, 32, 8, 20000


class ExactNumber(str):
    """Stored XLSX numeric text, never passed through a binary float."""


@dataclass(frozen=True)
class Grid:
    name: str
    rows: tuple
    formulas: frozenset = frozenset()
    merges: tuple = ()
    hidden: bool = False


def _filename(filename, content):
    name = PurePath(filename.replace("\\", "/")).name
    if not name or len(name) > 100 or "\x00" in name:
        raise InvalidUpload("Use a valid bounded filename.")
    if not content or len(content) > 5 * 1024 * 1024:
        raise InvalidUpload("Use a nonempty file of at most 5 MB.")
    return name


def _archive(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if (
            len(members) > 200
            or len({m.filename for m in members}) != len(members)
            or sum(m.file_size for m in members) > 20 * 1024 * 1024
        ):
            raise InvalidUpload("The workbook exceeds the safe expanded-size limit.")
        for member in members:
            if not member.filename.endswith((".xml", ".rels")):
                continue
            data = archive.read(member)
            if (
                b"\x00" in data
                or b"<!DOCTYPE" in data.upper()
                or b"<!ENTITY" in data.upper()
            ):
                raise InvalidUpload("XML entities are not accepted in workbooks.")
            if not member.filename.startswith(
                "xl/worksheets/"
            ) or not member.filename.endswith(".xml"):
                continue
            for _, element in ElementTree.iterparse(io.BytesIO(data), events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                coordinate = element.get("r", "")
                if tag == "row" and (
                    not coordinate.isascii()
                    or not coordinate.isdigit()
                    or len(coordinate) > 4
                    or not 1 <= int(coordinate) <= MAX_ROWS
                ):
                    raise InvalidUpload("Worksheet rows exceed the inspection limit.")
                if tag == "c":
                    match = re.fullmatch(r"([A-Z]{1,2})([1-9][0-9]{0,3})", coordinate)
                    if (
                        not match
                        or column_index_from_string(match[1]) > MAX_COLUMNS
                        or int(match[2]) > MAX_ROWS
                    ):
                        raise InvalidUpload(
                            "Worksheet cells exceed the inspection limit."
                        )
                element.clear()


def _grids(content, suffix, delimiter):
    if suffix == ".csv":
        if delimiter not in {";", ",", "\t"}:
            raise InvalidUpload("Confirm the CSV delimiter before inspection.")
        try:
            rows = list(
                islice(
                    csv.reader(
                        io.StringIO(content.decode("utf-8-sig"), newline=""),
                        delimiter=delimiter,
                        strict=True,
                    ),
                    MAX_ROWS + 1,
                )
            )
        except (UnicodeError, csv.Error) as exc:
            raise InvalidUpload("Use a valid UTF-8 CSV file.") from exc
        if not rows or len(rows) > MAX_ROWS or max(map(len, rows)) > MAX_COLUMNS:
            raise InvalidUpload("Use at most 1000 physical rows and 32 columns.")
        grids = [Grid("CSV", tuple(tuple(row) for row in rows))]
    elif suffix == ".xlsx":
        if delimiter is not None:
            raise InvalidUpload("A CSV delimiter does not apply to XLSX.")
        try:
            _archive(content)
            book = load_workbook(
                io.BytesIO(content), read_only=True, data_only=False, keep_links=False
            )
            try:
                if not 1 <= len(book.worksheets) <= MAX_SHEETS:
                    raise InvalidUpload("Use at most eight worksheets.")
                if len(book.sheetnames) != len(book.worksheets):
                    raise InvalidUpload(
                        "Use a workbook containing only data worksheets; chart sheets are not supported."
                    )
                grids, total = [], 0
                for sheet in book.worksheets:
                    sheet.reset_dimensions()
                    try:
                        sheet.calculate_dimension(force=True)
                    except UnboundLocalError:
                        # Empty read-only worksheets have no last cell from which
                        # openpyxl can calculate dimensions. Confirm emptiness;
                        # never reinterpret a populated sheet as an exclusion.
                        if any(
                            cell.value is not None
                            for row in sheet.iter_rows()
                            for cell in row
                        ):
                            raise InvalidUpload(
                                "Worksheet dimensions could not be resolved."
                            ) from None
                        grids.append(
                            Grid(sheet.title, (), hidden=sheet.sheet_state != "visible")
                        )
                        continue
                    if sheet.max_row > MAX_ROWS or sheet.max_column > MAX_COLUMNS:
                        raise InvalidUpload(
                            "Worksheet dimensions exceed the inspection limit."
                        )
                    total += sheet.max_row * sheet.max_column
                    if total > MAX_CELLS:
                        raise InvalidUpload("The workbook exceeds 20000 grid cells.")
                    rows, formulas = [], set()
                    for number, row in enumerate(sheet.iter_rows(), 1):
                        rows.append(tuple(cell.value for cell in row))
                        formulas.update(
                            (number, index)
                            for index, cell in enumerate(row, 1)
                            if cell.data_type == "f"
                        )
                    grids.append(
                        Grid(
                            sheet.title,
                            tuple(rows),
                            frozenset(formulas),
                            hidden=sheet.sheet_state != "visible",
                        )
                    )
            finally:
                book.close()
            # Read merged coordinates directly; read-only openpyxl omits them.
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                # Worksheet filenames need not correspond to display ordering.
                workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
                relations = {
                    r.get("Id"): r.get("Target")
                    for r in ElementTree.fromstring(
                        archive.read("xl/_rels/workbook.xml.rels")
                    )
                }
                sheets = next(
                    e for e in workbook if e.tag.rsplit("}", 1)[-1] == "sheets"
                )
                updated, worksheet_targets = [], set()
                for grid, sheet in zip(grids, sheets):
                    rid = sheet.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    )
                    target = relations[rid]
                    target = (
                        target.lstrip("/") if target.startswith("/") else "xl/" + target
                    )
                    if ".." in PurePath(target).parts:
                        raise InvalidUpload("Unsupported worksheet relationship.")
                    if target in worksheet_targets:
                        raise InvalidUpload(
                            "Worksheet relationships must identify distinct source sheets."
                        )
                    worksheet_targets.add(target)
                    xml = ElementTree.fromstring(archive.read(target))
                    exact_rows = [list(row) for row in grid.rows]
                    for cell in xml.iter():
                        if (
                            cell.tag.rsplit("}", 1)[-1] != "c"
                            or cell.get("t", "n") != "n"
                        ):
                            continue
                        if any(e.tag.rsplit("}", 1)[-1] == "f" for e in cell):
                            continue
                        value = next(
                            (e.text for e in cell if e.tag.rsplit("}", 1)[-1] == "v"),
                            None,
                        )
                        if value is not None:
                            number = Decimal(value)
                            if (
                                not number.is_finite()
                                or number.adjusted() > 1999
                                or number.as_tuple().exponent < -1999
                            ):
                                raise InvalidUpload(
                                    "A workbook numeric value exceeds the exact-cell limit."
                                )
                            text = format(number, "f")
                            if len(text) > 2000:
                                raise InvalidUpload(
                                    "A workbook numeric value exceeds 2000 characters."
                                )
                            match = re.fullmatch(
                                r"([A-Z]{1,2})([1-9][0-9]{0,3})", cell.get("r", "")
                            )
                            if isinstance(
                                exact_rows[int(match[2]) - 1][
                                    column_index_from_string(match[1]) - 1
                                ],
                                (date, time, timedelta),
                            ):
                                continue
                            exact_rows[int(match[2]) - 1][
                                column_index_from_string(match[1]) - 1
                            ] = ExactNumber(text)
                    merges = tuple(
                        range_boundaries(e.get("ref", ""))
                        for e in xml.iter()
                        if e.tag.rsplit("}", 1)[-1] == "mergeCell"
                    )
                    updated.append(
                        Grid(
                            grid.name,
                            tuple(tuple(row) for row in exact_rows),
                            grid.formulas,
                            merges,
                            grid.hidden,
                        )
                    )
                grids = updated
        except InvalidUpload:
            raise
        except Exception as exc:
            raise InvalidUpload("The XLSX workbook is invalid or unsupported.") from exc
    else:
        raise InvalidUpload("Inspect a CSV or XLSX file.")
    if sum(len(row) for g in grids for row in g.rows) > MAX_CELLS:
        raise InvalidUpload("Use at most 20000 grid cells.")
    if any(
        isinstance(v, str) and len(v) > 2000
        for g in grids
        for row in g.rows
        for v in row
    ):
        raise InvalidUpload("A source cell exceeds 2000 characters.")
    return grids


def _description(filename, content, delimiter, grids):
    digest = sha256(content).hexdigest()
    return {
        "version": VERSION,
        "filename": filename,
        "source_hash": digest,
        "inspection_hash": semantic_hash(
            {
                "version": VERSION,
                "filename": filename,
                "source_hash": digest,
                "delimiter": delimiter,
            }
        ),
        "delimiter": delimiter,
        "sheets": [
            {
                "sheet": number,
                "name": g.name,
                "hidden": g.hidden,
                "row_count": len(g.rows),
                "column_count": max(map(len, g.rows), default=0),
                "formula_count": len(g.formulas),
                "merged_ranges": [list(m) for m in g.merges],
                "sample_rows": [
                    {
                        "row": r,
                        "cells": [
                            {
                                "column": get_column_letter(c),
                                "value": "" if value is None else str(value)[:160],
                                "type": (
                                    "formula"
                                    if (r, c) in g.formulas
                                    else type(value).__name__
                                ),
                            }
                            for c, value in enumerate(row, 1)
                        ],
                    }
                    for r, row in enumerate(g.rows[:20], 1)
                ],
            }
            for number, g in enumerate(grids, 1)
        ],
        "limits": {"selected_rows": 500, "tables": 12, "grid_cells": MAX_CELLS},
    }


def inspect_upload(filename, content, delimiter=None, pdf_layout=None):
    filename = _filename(filename, content)
    if PurePath(filename).suffix.lower() == ".pdf":
        if delimiter is not None or pdf_layout is None:
            raise InvalidUpload("Select PDF pages explicitly before inspection.")
        from .pdf_source import inspect_pdf

        return inspect_pdf(filename, content, pdf_layout)[0]
    if pdf_layout is not None:
        raise InvalidUpload("PDF page choices do not apply to this file.")
    grids = _grids(content, PurePath(filename).suffix.lower(), delimiter)
    return _description(filename, content, delimiter, grids)


def extract_tables(filename, content, selection):
    filename = _filename(filename, content)
    grids = _grids(content, PurePath(filename).suffix.lower(), selection.delimiter)
    description = _description(filename, content, selection.delimiter, grids)
    if description["inspection_hash"] != selection.inspection_hash:
        raise InvalidUpload("The inspected file or delimiter changed; inspect again.")
    return extract_grid_tables(grids, selection, description)


def extract_grid_tables(
    grids, selection, description, *, version=VERSION, source_ref=None
):
    rows, origins, claimed, summaries = [], [], set(), []
    for table_number, table in enumerate(selection.tables, 1):
        if table.sheet > len(grids):
            raise InvalidUpload("A selected worksheet is outside this file.")
        grid = grids[table.sheet - 1]
        width = max(map(len, grid.rows), default=0)
        if table.last_row > len(grid.rows):
            raise InvalidUpload("A selected interval is outside its worksheet.")
        if any(
            b.column and column_index_from_string(b.column) > width
            for b in table.fields.values()
        ):
            raise InvalidUpload("A selected column is outside its worksheet.")
        for left, top, right, bottom in grid.merges:
            if any(
                b.column
                and left <= column_index_from_string(b.column) <= right
                and (
                    top <= table.header_row <= bottom
                    or max(top, table.first_row) <= min(bottom, table.last_row)
                )
                for b in table.fields.values()
            ):
                raise InvalidUpload(
                    "Selected cells intersect a merged range; choose explicit unmerged source values."
                )
        count, blanks = 0, []
        for number in range(table.first_row, table.last_row + 1):
            raw = grid.rows[number - 1]
            if all(value is None or value == "" for value in raw):
                blanks.append(number)
                continue
            amount_column = table.fields["amount"].column
            key = (table.sheet, number, amount_column)
            if key in claimed:
                raise InvalidUpload("A source amount cell is selected more than once.")
            claimed.add(key)
            values, cells = [], {}
            for role in FIELDS:
                binding = table.fields[role]
                if binding.column is None:
                    values.append(binding.constant)
                    cells[role] = {"constant": binding.constant}
                else:
                    column = column_index_from_string(binding.column)
                    if (number, column) in grid.formulas:
                        raise InvalidUpload(
                            "Replace selected formulas with explicit source values."
                        )
                    value = raw[column - 1] if column <= len(raw) else None
                    if (
                        isinstance(value, bool)
                        or isinstance(value, float)
                        and not math.isfinite(value)
                    ):
                        raise InvalidUpload(
                            "Boolean and nonfinite cells cannot supply financial fields."
                        )
                    values.append(value)
                    cells[role] = {"cell": f"{binding.column}{number}"}
            # Preserve every mapped origin in the canonical source reference.
            # Constants are marked, and their values remain in the source row.
            location = (
                source_ref(table.sheet, amount_column, number)
                if source_ref
                else f"s{table.sheet}!{amount_column}{number}"
            )
            origins.append(
                {"location": location, "source_cells": cells, "sheet": grid.name}
            )
            rows.append(values)
            count += 1
            if len(rows) > 500:
                raise InvalidUpload(
                    "Select at most 500 financial rows across all tables."
                )
        if not count:
            raise InvalidUpload("A selected table contains no financial rows.")
        summaries.append(
            {
                "table": table_number,
                **table.model_dump(),
                "sheet_name": grid.name,
                "selected_rows": count,
                "blank_rows": blanks,
                "excluded_columns": [
                    get_column_letter(c)
                    for c in range(1, width + 1)
                    if get_column_letter(c)
                    not in {b.column for b in table.fields.values()}
                ],
            }
        )
    manifest = {
        "version": version,
        "inspection_hash": selection.inspection_hash,
        "selection": selection.model_dump(),
        "tables": summaries,
        "excluded_sheets": [
            s
            for s in description["sheets"]
            if s["sheet"] not in {t.sheet for t in selection.tables}
        ],
    }
    # Samples are not repeated in publication receipts or model evidence.
    for sheet in manifest["excluded_sheets"]:
        sheet.pop("sample_rows")
    coverage = []
    for number, grid in enumerate(grids, 1):
        tables = [t for t in selection.tables if t.sheet == number]
        excluded, start = [], None
        for row in range(1, len(grid.rows) + 2):
            omitted = row <= len(grid.rows) and not any(
                t.first_row <= row <= t.last_row for t in tables
            )
            if omitted and start is None:
                start = row
            if not omitted and start is not None:
                excluded.append([start, row - 1])
                start = None
        coverage.append(
            {
                "sheet": number,
                "name": grid.name,
                "row_count": len(grid.rows),
                "excluded_row_ranges": excluded,
                "selected_intervals": [
                    list(interval)
                    for interval in sorted({(t.first_row, t.last_row) for t in tables})
                ],
                "excluded_columns": [
                    get_column_letter(c)
                    for c in range(1, max(map(len, grid.rows), default=0) + 1)
                    if get_column_letter(c)
                    not in {b.column for t in tables for b in t.fields.values()}
                ],
            }
        )
    manifest["sheet_coverage"] = coverage
    return [list(FIELDS), *rows], origins, manifest
