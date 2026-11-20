"""Render and inspect actual HTML/PDF bytes, not just channel manifests."""

import base64
import hashlib
import re
from html import escape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import reportlab
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .contracts import RenderedArtifacts, ReportParityError
from .presentation import document, manifest


def _font():
    name = "SalesLabVera"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont(name, str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )
    return name


def render_html(payload):
    blocks = document(payload)
    tags = {"title": "h1", "heading": "h2", "subheading": "h3"}
    contents = []
    for block in blocks:
        tag = tags.get(block.kind, "p")
        contents.append(
            f'<{tag} data-block="{escape(block.key, quote=True)}" class="{block.kind}">{escape(block.text)}</{tag}>'
        )
    return (
        '<!doctype html><html lang="'
        + payload["language"]
        + '"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>SalesLab</title><style>"
        "body{font:16px/1.6 system-ui,sans-serif;background:#f3f5f8;color:#172b40;margin:0}"
        "main{max-width:960px;margin:32px auto;background:white;padding:48px;overflow-wrap:anywhere}"
        "h1{font-size:36px;letter-spacing:-1px}h2{border-top:2px solid #dde5ed;padding-top:24px;margin-top:36px}"
        ".meta{font-size:12px;color:#506070}.notice{background:#f0f5fa;padding:16px;border-left:3px solid #315a78}"
        ".technical{font:11px/1.6 ui-monospace,monospace;border-bottom:1px solid #edf0f4;padding-bottom:10px;white-space:pre-wrap}"
        "@media(max-width:600px){main{margin:0;padding:24px}}"
        "</style></head><body><main>" + "".join(contents) + "</main></body></html>"
    )


def render_pdf(payload):
    blocks = document(payload)
    font = _font()
    glyphs = pdfmetrics.getFont(font).face.charToGlyph
    if any(ord(c) not in glyphs for b in blocks for c in b.text if not c.isspace()):
        raise ReportParityError(
            "unsupported PDF glyph; no silent character substitution"
        )
    output = BytesIO()
    styles = {
        "title": (26, 32, 18),
        "heading": (16, 22, 14),
        "subheading": (12, 18, 8),
        "body": (10, 15, 8),
        "notice": (10, 15, 12),
        "meta": (8, 12, 6),
        "technical": (7.5, 11, 6),
    }
    story = []
    for block in blocks:
        size, leading, after = styles[block.kind]
        style = ParagraphStyle(
            block.kind,
            fontName=font,
            fontSize=size,
            leading=leading,
            textColor=colors.HexColor("#172b40"),
            spaceAfter=after,
            keepWithNext=block.kind in {"heading", "subheading"},
            splitLongWords=True,
        )
        story.append(Paragraph(escape(block.text).replace("\n", "<br/>"), style))
        if block.kind == "title":
            story.append(Spacer(1, 6))

    def footer(canvas, doc):
        if doc.page > 120:
            raise ReportParityError("PDF exceeds page limit")
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#506070"))
        canvas.drawString(42, 26, f"SalesLab | {payload['report_id']} | {doc.page}")

    def invariant_canvas(*args, **kwargs):
        kwargs["invariant"] = 1
        return Canvas(*args, **kwargs)

    SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=42,
        bottomMargin=45,
        title=payload["report_id"],
        author="SalesLab",
        subject=payload["report_version"],
    ).build(
        story, onFirstPage=footer, onLaterPages=footer, canvasmaker=invariant_canvas
    )
    return output.getvalue()


class _VisibleBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.current, self.tag, self.text = [], None, None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-block" in attrs:
            if self.current is not None:
                raise ReportParityError("nested report blocks")
            self.current, self.tag, self.text = attrs["data-block"], tag, []
        elif self.current is not None:
            raise ReportParityError("unexpected markup inside a report block")

    def handle_data(self, data):
        if self.current is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if self.current is not None and tag == self.tag:
            self.blocks.append((self.current, "".join(self.text)))
            self.current, self.tag, self.text = None, None, []


def _compact(text):
    # PDF pagination/word wrapping changes whitespace, never non-whitespace text.
    return re.sub(r"\s+", "", text)


def verify_channels(payload, html, pdf):
    """Check visible ordered blocks and extracted page text against one projection."""
    try:
        expected = document(payload)
        parser = _VisibleBlocks()
        parser.feed(html)
        parser.close()
        if parser.current is not None or parser.blocks != [
            (b.key, b.text) for b in expected
        ]:
            raise ReportParityError("web content differs from ReportResult")
        # Also reject hidden/style/content edits: only the deterministic web renderer
        # is accepted, not an arbitrary HTML document with matching data attributes.
        if html != render_html(payload):
            raise ReportParityError("web structure or presentation was modified")
        reader = PdfReader(BytesIO(pdf), strict=True)
        if (
            not 1 <= len(reader.pages) <= 120
            or reader.metadata.title != payload["report_id"]
            or reader.metadata.subject != payload["report_version"]
        ):
            raise ReportParityError("PDF report identity differs")
        contents = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            footer = f"SalesLab | {payload['report_id']} | {number}"
            if text.count(footer) != 1:
                raise ReportParityError("PDF page identity missing or duplicated")
            contents.append(text.replace(footer, "", 1))
        if _compact("".join(contents)) != _compact("".join(b.text for b in expected)):
            raise ReportParityError("PDF visible text differs from ReportResult")
        if pdf != render_pdf(payload):
            raise ReportParityError(
                "PDF presentation differs from the versioned renderer"
            )
        return {**manifest(payload), "page_count": len(reader.pages), "status": "pass"}
    except ReportParityError:
        raise
    except Exception as exc:
        raise ReportParityError("cannot verify rendered channels") from exc


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def render_channels(payload):
    html, pdf = render_html(payload), render_pdf(payload)
    parity = verify_channels(payload, html, pdf)
    return {
        "html": html,
        "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        "html_hash": sha256(html.encode()),
        "pdf_hash": sha256(pdf),
        "parity": parity,
    }


def verify_artifacts(payload, artifacts):
    try:
        RenderedArtifacts.model_validate(artifacts)
        pdf = base64.b64decode(artifacts["pdf_base64"], validate=True)
        if len(pdf) > 8_000_000 or len(artifacts["html"].encode()) > 4_000_000:
            raise ReportParityError("rendered artifacts exceed size limit")
        if (
            sha256(pdf) != artifacts["pdf_hash"]
            or sha256(artifacts["html"].encode()) != artifacts["html_hash"]
            or verify_channels(payload, artifacts["html"], pdf) != artifacts["parity"]
        ):
            raise ReportParityError("persisted artifact integrity mismatch")
    except ReportParityError:
        raise
    except Exception as exc:
        raise ReportParityError("invalid persisted artifacts") from exc
