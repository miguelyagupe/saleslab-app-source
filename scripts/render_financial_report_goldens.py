"""Reproduce synthetic F-08 HTML/PDF previews for visual inspection.

Run from repository root: python scripts/render_financial_report_goldens.py
Requires tests/requirements-report.txt for the raster contact sheets.
Does not update accepted regression baselines.
"""

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import pypdfium2
from PIL import Image, ImageDraw
from report_fixtures import report_fixture

from saleslab_v2.reports.render import render_channels


def main():
    output = ROOT / "tmp/pdfs/f08"
    output.mkdir(parents=True, exist_ok=True)
    for language in ("pt", "en", "es", "fr"):
        payload, _ = report_fixture(language, warning=True)
        artifacts = render_channels(payload)
        pdf = base64.b64decode(artifacts["pdf_base64"])
        (output / f"golden-{language}.pdf").write_bytes(pdf)
        (output / f"golden-{language}.html").write_text(artifacts["html"])
        with pypdfium2.PdfDocument(pdf) as document:
            thumbnails = []
            for number, page in enumerate(document, 1):
                bitmap = page.render(scale=1)
                image = bitmap.to_pil().convert("RGB")
                image.save(output / f"{language}-page-{number:02}.png")
                image.thumbnail((280, 400))
                thumbnails.append(image.copy())
                bitmap.close()
                page.close()
            sheet = Image.new(
                "RGB", (4 * 300, ((len(thumbnails) + 3) // 4) * 430), "#dae0e7"
            )
            draw = ImageDraw.Draw(sheet)
            for index, thumb in enumerate(thumbnails):
                x, y = (index % 4) * 300 + 10, (index // 4) * 430 + 20
                sheet.paste(thumb, (x, y))
                draw.text((x, y - 16), f"{language} / {index + 1}", fill="black")
            sheet.save(output / f"contact-{language}.png")
        print(f"{language}: {artifacts['parity']['page_count']} pages; parity passed")


if __name__ == "__main__":
    main()
