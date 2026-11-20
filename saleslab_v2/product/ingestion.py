"""Bounded, deterministic tabular parsing. No formula execution or PDF guessing."""

import csv
import io
import math
import re
import zipfile
from dataclasses import dataclass, replace
from decimal import localcontext
from hashlib import sha256
from itertools import islice
from pathlib import PurePath
from uuid import uuid5
from xml.etree import ElementTree

from openpyxl import load_workbook

from saleslab_v2.domain.enums import LanguageCode, SourceFormat, TrustLevel
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.domain.language import LocaleTag
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.i18n.catalog import FINANCIAL_TERMS
from saleslab_v2.i18n.financial_v2 import FINANCIAL_TERMS_V2
from saleslab_v2.i18n.financial_v3 import FINANCIAL_TERMS_V3
from saleslab_v2.i18n.financial_v4 import FINANCIAL_TERMS_V4
from saleslab_v2.i18n.financial_v5 import FINANCIAL_TERMS_V5
from saleslab_v2.i18n.financial_v6 import FINANCIAL_TERMS_V6, FinancialTerminologyV6
from saleslab_v2.i18n.financial_v7 import FINANCIAL_TERMS_V7, FinancialTerminologyV7
from saleslab_v2.i18n.financial_v8 import FINANCIAL_TERMS_V8, FinancialTerminologyV8
from saleslab_v2.i18n.financial_v9 import FINANCIAL_TERMS_V9, FinancialTerminologyV9
from saleslab_v2.i18n.financial_v10 import FINANCIAL_TERMS_V10, FinancialTerminologyV10
from saleslab_v2.normalization.financial import FinancialRow, SourceMetadata
from saleslab_v2.normalization.numbers import LocaleNumberParser
from saleslab_v2.normalization.periods import PeriodNormalizer
from saleslab_v2.pipeline.contracts import SourceBatch

from .capex_source import check_capex_definitions
from .contracts import VERSION, InvalidUpload
from .debt_source import check_debt_definitions
from .growth_source import check_definitions
from .roic_source import check_roic_sources
from .tabular import ExactNumber
from .working_capital_source import check_working_capital_definitions

HEADERS = (
    "line",
    "amount",
    "period",
    "statement_type",
    "scope",
    "currency",
    "scale",
    "comparison_basis",
)
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 500
TERMS = {t.concept_id: t for t in FINANCIAL_TERMS}
TERMS_V2 = {t.concept_id: t for t in FINANCIAL_TERMS_V2}
TERMS_V3 = {t.concept_id: t for t in FINANCIAL_TERMS_V3}
TERMS_V4 = {t.concept_id: t for t in FINANCIAL_TERMS_V4}
TERMS_V5 = {t.concept_id: t for t in FINANCIAL_TERMS_V5}
TERMS_V6 = {t.concept_id: t for t in FINANCIAL_TERMS_V6}
TERMS_V10 = {t.concept_id: t for t in FINANCIAL_TERMS_V10}
WORKING_CAPITAL_TERMINOLOGY = FinancialTerminologyV10()
TERMS_V9 = {t.concept_id: t for t in FINANCIAL_TERMS_V9}
ROIC_TERMINOLOGY = FinancialTerminologyV9()
TERMS_V8 = {t.concept_id: t for t in FINANCIAL_TERMS_V8}
CAPEX_TERMINOLOGY = FinancialTerminologyV8()
TERMS_V7 = {t.concept_id: t for t in FINANCIAL_TERMS_V7}
DEBT_TERMINOLOGY = FinancialTerminologyV7()
GROWTH_TERMINOLOGY = FinancialTerminologyV6()


def terms_for_plan(version):
    return {
        "5": TERMS_V2,
        "6": TERMS_V3,
        "7": TERMS_V4,
        "8": TERMS_V5,
        "9": TERMS_V6,
        "10": TERMS_V7,
        "11": TERMS_V8,
        "12": TERMS_V9,
        "13": TERMS_V10,
    }.get(version, TERMS)


@dataclass(frozen=True)
class ParsedUpload:
    batch: SourceBatch
    original_rows: tuple[dict, ...]
    decisions: dict
    import_manifest: dict | None = None


def _check_sign_precision(record, choices, number):
    # Version 2 applies signs exactly. Preserve the legacy guard for explicit
    # conventions under version 1 without changing old request semantics.
    if choices.normalization_version == "2" or choices.accounting_parentheses is None:
        return
    parser = LocaleNumberParser()
    options = {
        "locale": LocaleTag(choices.format_locale),
        "accounting_parentheses": choices.accounting_parentheses,
    }
    actual = parser.parse(record["amount"], **options)
    with localcontext() as context:
        context.prec = 2048  # More than the upload's maximum 2000-character cell.
        exact = parser.parse(record["amount"], **options)
    if actual.value != exact.value:
        raise InvalidUpload(
            f"Row {number}: this signed amount exceeds the parser's supported precision; no rounded source value was accepted."
        )


def _table(content, suffix):
    if suffix == ".csv":
        try:
            text = content.decode("utf-8-sig", errors="strict")
            first = text.splitlines()[0]
            delimiters = [
                d
                for d in (";", ",", "\t")
                if tuple(next(csv.reader([first], delimiter=d))) == HEADERS
            ]
            if len(delimiters) != 1:
                raise InvalidUpload("Use the template headers in their original order.")
            return list(
                islice(
                    csv.reader(
                        io.StringIO(text, newline=""),
                        delimiter=delimiters[0],
                        strict=True,
                    ),
                    MAX_ROWS + 2,
                )
            )
        except (UnicodeError, csv.Error, IndexError) as exc:
            raise InvalidUpload("Use a valid UTF-8 CSV file.") from exc
    if suffix != ".xlsx":
        raise InvalidUpload(
            "This validated flow accepts CSV and XLSX templates; convert other formats first."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if (
                len(members) > 200
                or len({m.filename for m in members}) != len(members)
                or sum(m.file_size for m in members) > 20 * 1024 * 1024
            ):
                raise InvalidUpload(
                    "The workbook exceeds the safe expanded-size limit."
                )
            for member in members:
                if member.filename.endswith((".xml", ".rels")):
                    data = archive.read(member)
                    if (
                        b"\x00" in data
                        or b"<!DOCTYPE" in data.upper()
                        or b"<!ENTITY" in data.upper()
                    ):
                        raise InvalidUpload(
                            "XML entities are not accepted in workbooks."
                        )
                    if member.filename.startswith(
                        "xl/worksheets/"
                    ) and member.filename.endswith(".xml"):
                        # Bound sparse coordinates before openpyxl can fill their gaps.
                        for _, element in ElementTree.iterparse(
                            io.BytesIO(data), events=("end",)
                        ):
                            tag = element.tag.rsplit("}", 1)[-1]
                            if tag == "row":
                                coordinate = element.get("r", "")
                                if (
                                    not coordinate.isascii()
                                    or not coordinate.isdigit()
                                    or len(coordinate) > 3
                                    or not 1 <= int(coordinate) <= MAX_ROWS + 1
                                ):
                                    raise InvalidUpload(
                                        "Workbook row coordinates exceed the template limit."
                                    )
                            elif tag == "c":
                                match = re.fullmatch(
                                    r"([A-H])([1-9][0-9]{0,2})", element.get("r", "")
                                )
                                if match is None or int(match[2]) > MAX_ROWS + 1:
                                    raise InvalidUpload(
                                        "Workbook cell coordinates exceed the template limit."
                                    )
                            element.clear()
        book = load_workbook(
            io.BytesIO(content), read_only=True, data_only=False, keep_links=False
        )
        try:
            if len(book.worksheets) != 1:
                raise InvalidUpload(
                    "Use one worksheet per upload so no sheets are silently omitted."
                )
            sheet = book.worksheets[0]
            sheet.reset_dimensions()
            sheet.calculate_dimension(force=True)
            if sheet.max_row > MAX_ROWS + 1 or sheet.max_column > len(HEADERS):
                raise InvalidUpload("Use at most 500 rows and the template columns.")
            result = []
            for row in sheet.iter_rows():
                if any(cell.data_type == "f" for cell in row):
                    raise InvalidUpload(
                        "Replace workbook formulas with their explicit source values."
                    )
                values = [cell.value for cell in row]
                if any(
                    isinstance(v, bool) or isinstance(v, float) and not math.isfinite(v)
                    for v in values
                ):
                    raise InvalidUpload(
                        "Boolean and nonfinite cells are not financial values."
                    )
                result.append(values)
            return result
        finally:
            book.close()
    except InvalidUpload:
        raise
    except Exception as exc:
        raise InvalidUpload("The XLSX workbook is invalid or unsupported.") from exc


def parse_upload(workspace_id, filename, content, choices):
    if not content or len(content) > MAX_BYTES:
        raise InvalidUpload("Use a nonempty file of at most 5 MB.")
    filename = PurePath(filename.replace("\\", "/")).name
    if not filename or len(filename) > 100 or "\x00" in filename:
        raise InvalidUpload("Use a valid bounded filename.")
    suffix = PurePath(filename).suffix.lower()
    origins, manifest = None, None
    if choices.pdf_import is not None:
        if suffix != ".pdf":
            raise InvalidUpload("PDF preparation requires the inspected PDF file.")
        if choices.normalization_version != "2":
            raise InvalidUpload(
                "PDF preparation requires exact normalization version 2."
            )
        from .pdf_source import extract_pdf_tables

        table, origins, manifest = extract_pdf_tables(filename, content, choices)
    elif choices.tabular_import is not None:
        from .tabular import extract_tables

        table, origins, manifest = extract_tables(
            filename, content, choices.tabular_import
        )
    else:
        table = _table(content, suffix)
    if not table or tuple(table[0]) != HEADERS or not 1 <= len(table) - 1 <= MAX_ROWS:
        raise InvalidUpload("Use the template headers and 1–500 financial rows.")
    digest = sha256(content).hexdigest()
    prepared = choices.pdf_import or choices.tabular_import
    parser_version = manifest["version"] if manifest is not None else VERSION
    if choices.pdf_import is not None:
        parser_version += ":" + manifest["pdf"]["engine"]
    selection_key = (
        ":" + semantic_hash(prepared.model_dump()) if manifest is not None else ""
    )
    if choices.working_capital_definitions is not None:
        selection_key += ":working-capital-definitions-v1:" + semantic_hash(
            choices.working_capital_definitions
        )
    if choices.roic_sources is not None:
        selection_key += ":roic-sources-v1:" + semantic_hash(
            choices.roic_sources.model_dump()
        )
    if choices.capex_definitions is not None:
        selection_key += ":capex-definitions-v1:" + semantic_hash(
            choices.capex_definitions
        )
    if choices.debt_definitions is not None:
        selection_key += ":debt-definitions-v1:" + semantic_hash(
            choices.debt_definitions
        )
    if choices.growth_definitions is not None:
        selection_key += ":growth-definitions-v1:" + semantic_hash(
            choices.growth_definitions
        )
    sid = uuid5(
        workspace_id,
        f"{parser_version}:{filename}:{digest}:{choices.source_language}:{choices.format_locale}{selection_key}",
    )
    document = SourceDocument(
        workspace_id,
        filename,
        {
            ".csv": SourceFormat.CSV,
            ".xlsx": SourceFormat.XLSX,
            ".pdf": SourceFormat.PDF,
        }[suffix],
        digest,
        parser_version,
        TrustLevel.UNTRUSTED,
        (LanguageCode(choices.source_language),),
        source_id=sid,
    )
    metadata = SourceMetadata(
        sid,
        digest,
        LanguageCode(choices.source_language),
        LocaleTag(choices.format_locale),
        accounting_parentheses=choices.accounting_parentheses,
        currency=choices.source_currency,
        scale=choices.source_scale,
        normalization_version=choices.normalization_version,
        terminology_version={
            "5": "2",
            "6": "3",
            "7": "4",
            "8": "5",
            "9": "6",
            "10": "7",
            "11": "8",
            "12": "9",
            "13": "10",
        }.get(choices.planning_version, "1"),
    )
    rows, originals, used = [], [], set()
    growth_concepts = {}
    debt_concepts = {}
    capex_concepts = {}
    roic_concepts = {}
    working_capital_concepts = {}
    for number, raw in enumerate(table[1:], start=2):
        if len(raw) != len(HEADERS):
            raise InvalidUpload(f"Row {number} must have exactly eight cells.")
        values = []
        for index, value in enumerate(raw):
            if value is None:
                text = ""
            elif isinstance(value, ExactNumber):
                text = str(value)
                if index == 1 and choices.format_locale != "en-GB":
                    text = text.replace(".", ",")
            elif isinstance(value, str):
                text = value.strip()
            elif isinstance(value, (int, float)) and index in (1, 2):
                text = str(value)
                if index == 1 and choices.format_locale != "en-GB":
                    text = text.replace(".", ",")
            else:
                raise InvalidUpload(
                    f"Row {number}: use text metadata and explicit numeric values."
                )
            if len(text) > 2000:
                raise InvalidUpload(f"Row {number} contains an oversized cell.")
            values.append(text)
        record = dict(zip(HEADERS, values))
        _check_sign_precision(record, choices, number)
        origin = origins[number - 2] if origins is not None else {}
        location = origin.get(
            "location", ("csv:" if suffix == ".csv" else "sheet1:") + str(number)
        )
        originals.append({**origin, "location": location, **record})
        if record["scope"] != "consolidated":
            raise InvalidUpload(
                "The initial upload template requires consolidated scope; segment data needs explicit dimension support."
            )
        if (
            (
                record["statement_type"] == "roic_schedule"
                and choices.planning_version in {"12", "13"}
            )
            or record["statement_type"] == "capex_schedule"
            and choices.planning_version in {"11", "12", "13"}
        ):
            pass
        elif record["statement_type"] not in {
            "income_statement",
            "balance_sheet",
            "cash_flow_statement",
        } and not (
            choices.planning_version in {"8", "9", "10", "11", "12", "13"}
            and record["statement_type"]
            in {"debt_schedule", "lease_schedule", "working_capital_detail"}
        ):
            raise InvalidUpload(
                f"Row {number}: use income_statement, balance_sheet or cash_flow_statement."
                if choices.planning_version not in {"8", "9", "10", "11", "12", "13"}
                else f"Row {number}: use income_statement, balance_sheet, cash_flow_statement, debt_schedule, lease_schedule or working_capital_detail."
            )
        if record["comparison_basis"] not in {"current", "comparative"}:
            raise InvalidUpload(
                f"Row {number}: use current or comparative comparison basis."
            )
        name = record["line"]
        if location in choices.mappings:
            term = terms_for_plan(choices.planning_version).get(
                choices.mappings[location]
            )
            if term is None or term.statement_type != record["statement_type"]:
                raise InvalidUpload(
                    f"Row {number}: mapping must match its declared statement type."
                )
            name = term.labels[LanguageCode(choices.source_language)]
            used.add(location)
        if choices.planning_version == "13":
            term = WORKING_CAPITAL_TERMINOLOGY.lookup(
                name, LanguageCode(choices.source_language), record["statement_type"]
            )
            working_capital_concepts[location] = term.concept_id if term else None
        if choices.planning_version in {"12", "13"}:
            term = ROIC_TERMINOLOGY.lookup(
                name, LanguageCode(choices.source_language), record["statement_type"]
            )
            roic_concepts[location] = term.concept_id if term else None
        if choices.planning_version in {"11", "12", "13"}:
            term = CAPEX_TERMINOLOGY.lookup(
                name, LanguageCode(choices.source_language), record["statement_type"]
            )
            capex_concepts[location] = term.concept_id if term else None
        if choices.planning_version in {"10", "11", "12", "13"}:
            term = DEBT_TERMINOLOGY.lookup(
                name, LanguageCode(choices.source_language), record["statement_type"]
            )
            debt_concepts[location] = term.concept_id if term else None
        if choices.planning_version in {"9", "10", "11", "12", "13"}:
            term = GROWTH_TERMINOLOGY.lookup(
                name, LanguageCode(choices.source_language), record["statement_type"]
            )
            growth_concepts[location] = term.concept_id if term else None
        try:
            # New fiscal choices can shift a year boundary outside datetime's
            # supported range. Reject that upload rather than leaking a 500;
            # ordinary unresolved periods still flow to normalization issues.
            if choices.fiscal_year_end_month is not None:
                PeriodNormalizer().normalize(
                    record["period"],
                    fiscal_year_end_month=choices.fiscal_year_end_month,
                    comparison_basis=record["comparison_basis"],
                )
            rows.append(
                FinancialRow(
                    name,
                    record["amount"],
                    record["period"],
                    record["statement_type"],
                    record["scope"],
                    currency=record["currency"] or None,
                    scale=record["scale"] or None,
                    comparison_basis=record["comparison_basis"],
                    fiscal_year_end_month=choices.fiscal_year_end_month,
                    definition_quote=(
                        (
                            (choices.growth_definitions or {})
                            | (choices.debt_definitions or {})
                            | (choices.capex_definitions or {})
                            | (choices.working_capital_definitions or {})
                        ).get(location)
                        if choices.planning_version in {"9", "10", "11", "12", "13"}
                        else None
                    ),
                    source_reference=location,
                )
            )
        except (TypeError, ValueError) as exc:
            raise InvalidUpload(
                f"Row {number}: check its label, period, statement, scope and comparison basis."
            ) from exc
    if used != set(choices.mappings):
        raise InvalidUpload("Mapping references a row outside this file.")
    if choices.planning_version in {"9", "10", "11", "12", "13"}:
        check_definitions(originals, growth_concepts, choices)
    if choices.planning_version in {"10", "11", "12", "13"}:
        check_debt_definitions(originals, debt_concepts, choices)
    if choices.planning_version in {"11", "12", "13"}:
        check_capex_definitions(originals, capex_concepts, choices)
    if choices.planning_version == "13":
        check_working_capital_definitions(originals, working_capital_concepts, choices)
    if choices.planning_version in {"12", "13"}:
        metadata = replace(
            metadata,
            roic_binding=check_roic_sources(originals, roic_concepts, choices, digest),
        )
    return ParsedUpload(
        SourceBatch(document, metadata, tuple(rows)),
        tuple(originals),
        dict(choices.mappings),
        manifest,
    )
