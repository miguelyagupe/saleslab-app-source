"""Text-only PDF inspection, explicit slices and source review before calculation."""

import base64
import json
import os
import subprocess
import sys
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from threading import BoundedSemaphore

from openpyxl.utils.cell import column_index_from_string

from saleslab_v2.domain.idempotency import semantic_hash

from .contracts import InvalidUpload
from .pdf_contracts import PdfLayoutRequest
from .tabular import MAX_CELLS, Grid, _description, _filename, extract_grid_tables
from .tabular_contracts import FIELDS

VERSION = "financial-pdf-text-v1"
_DECODERS = BoundedSemaphore(1)


class PdfReviewRequired(InvalidUpload):
    def __init__(self, payload):
        super().__init__(
            "Confirm every extracted PDF row against the original document before financial validation."
        )
        self.payload = payload


class PdfDecoderBusy(InvalidUpload):
    """Transient contention; a committed upload job must remain retryable."""


def pdf_available():
    return os.name == "posix"


def _decode(content, layouts):
    if not _DECODERS.acquire(blocking=False):
        raise PdfDecoderBusy("PDF text extraction is busy; retry shortly.")
    try:
        return _run_decoder(content, layouts)
    finally:
        _DECODERS.release()


def _run_decoder(content, layouts):
    if not pdf_available():
        raise InvalidUpload("Bounded PDF text extraction is unavailable on this host.")
    if not content.startswith(b"%PDF-"):
        raise InvalidUpload("Use a valid text PDF.")
    request = json.dumps(
        {
            "content": base64.b64encode(content).decode(),
            "pages": [p.page for p in layouts.pages],
        }
    ).encode()
    environment = {
        key: os.environ[key]
        for key in ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "SYSTEMROOT")
        if key in os.environ
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "saleslab_v2.product.pdf_worker"],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=12,
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            check=False,
        )
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError("decoder failed")
        value = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise InvalidUpload(
            "PDF text extraction exceeded its limits or failed. Select fewer pages or provide an explicit CSV/XLSX source."
        ) from exc
    if "error" in value:
        messages = {
            "encrypted": "Encrypted PDFs are not accepted.",
            "pages": "Select at most eight existing pages from a PDF of at most 300 pages.",
            "no_text": "A selected page has no extractable text. Provide a text PDF or an explicitly verified tabular source.",
            "bounds": "The selected PDF content exceeds the bounded text limits. Select fewer pages.",
            "rotation": "Use unrotated source pages with horizontal text.",
            "orientation": "Selected text has unsupported orientation or font information.",
            "encoding": "Selected text cannot be decoded reliably; verify a tabular source instead.",
        }
        raise InvalidUpload(
            messages.get(value["error"], "The PDF is invalid or unsupported.")
        )
    return value


def inspect_pdf(filename, content, layouts):
    filename = _filename(filename, content)
    layouts = PdfLayoutRequest.model_validate(layouts.model_dump())
    decoded = _decode(content, layouts)
    grids = []
    for page, layout in zip(decoded["pages"], layouts.pages):
        width = max(map(len, page["lines"]), default=0)
        if layout.cuts and layout.cuts[-1] >= width:
            raise InvalidUpload(
                "Place character boundaries within the extracted page width."
            )
        bounds = [0, *layout.cuts, width]
        rows = tuple(
            tuple(line[start:end] for start, end in pairwise(bounds))
            for line in page["lines"]
        )
        grids.append(Grid("Página " + str(page["page"]), rows))
    if sum(len(row) for g in grids for row in g.rows) > MAX_CELLS:
        raise InvalidUpload("Selected PDF text exceeds 20000 grid cells.")
    description = _description(filename, content, None, grids)
    text_hash = semantic_hash(decoded["pages"])
    description.update(
        version=VERSION,
        pdf={
            "engine": decoded["engine"],
            "page_count": decoded["page_count"],
            "pages": layouts.model_dump()["pages"],
            "text_hash": text_hash,
            "excluded_pages": [
                p
                for p in range(1, decoded["page_count"] + 1)
                if p not in {v.page for v in layouts.pages}
            ],
        },
    )
    description["inspection_hash"] = semantic_hash(
        {
            "version": VERSION,
            "filename": filename,
            "source_hash": sha256(content).hexdigest(),
            "pdf": description["pdf"],
        }
    )
    for sheet, page in zip(description["sheets"], decoded["pages"]):
        sheet["page"] = page["page"]
        sheet["text_lines"] = page["lines"]
    return description, grids


def extract_pdf_tables(filename, content, choices):
    selected = choices.pdf_import
    description, grids = inspect_pdf(
        filename, content, PdfLayoutRequest(pages=selected.pages)
    )
    if description["inspection_hash"] != selected.inspection_hash:
        raise InvalidUpload(
            "The PDF, selected pages, column boundaries or extraction engine changed; inspect again."
        )
    refs = (
        lambda sheet, column, row: f"p{selected.pages[sheet - 1].page}:L{row}:{column}"
    )
    table, origins, manifest = extract_grid_tables(
        grids, selected, description, version=VERSION, source_ref=refs
    )
    review_rows = []
    for raw, origin in zip(table[1:], origins):
        sheet_index = next(i for i, g in enumerate(grids) if g.name == origin["sheet"])
        layout = selected.pages[sheet_index]
        line = int(
            origin["source_cells"]["amount"]["cell"].lstrip(
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            )
        )
        origin["page"] = layout.page
        origin["pdf_line"] = line
        origin["extracted_text"] = description["sheets"][sheet_index]["text_lines"][
            line - 1
        ]
        text = origin["extracted_text"]
        for binding in origin["source_cells"].values():
            if "cell" not in binding:
                continue
            column = column_index_from_string(binding["cell"].rstrip("0123456789")) - 1
            bounds = [0, *layout.cuts, len(text)]
            binding.update(
                page=layout.page,
                line=line,
                span=[
                    min(bounds[column], len(text)),
                    min(bounds[column + 1], len(text)),
                ],
            )
        review_rows.append({**origin, "values": dict(zip(FIELDS, raw))})
    if len(json.dumps(review_rows, ensure_ascii=True).encode()) > 256000:
        raise InvalidUpload(
            "The PDF source review exceeds its limit; select fewer rows."
        )
    interpretation = {
        key: getattr(choices, key)
        for key in (
            "source_language",
            "format_locale",
            "accounting_parentheses",
            "fiscal_year_end_month",
            "source_currency",
            "source_scale",
            "normalization_version",
        )
    }
    review_hash = semantic_hash(
        {
            "inspection": selected.inspection_hash,
            "tables": [t.model_dump() for t in selected.tables],
            "rows": review_rows,
            "interpretation": interpretation,
        }
    )
    for sheet in manifest["excluded_sheets"]:
        sheet.pop("text_lines", None)
    manifest["pdf"] = description["pdf"]
    manifest["review_hash"] = review_hash
    required = {row["location"] for row in review_rows}
    if selected.review_hash != review_hash or set(selected.confirmed_rows) != required:
        raise PdfReviewRequired(
            {
                "schema": "financial-pdf-review-v1",
                "ready": False,
                "review_hash": review_hash,
                "review_rows": review_rows,
                "source_import": manifest,
                "message": "Confronte cada linha extraída com o PDF original e confirme os valores antes de validar.",
            }
        )
    return table, origins, manifest
