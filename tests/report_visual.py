"""Raster baselines at a coarse ink grid, robust to antialiasing noise."""

import hashlib

import pypdfium2
from PIL import Image


def page_fingerprints(pdf):
    results = []
    with pypdfium2.PdfDocument(pdf) as document:
        for page in document:
            bitmap = page.render(scale=1)
            image = bitmap.to_pil().convert("L")
            # Small grid detects page/paragraph movement or clipping while not
            # comparing platform-specific subpixel rasterization of every glyph.
            grid = image.resize((32, 48), Image.Resampling.BOX)
            ink = bytes(1 if p < 246 else 0 for p in grid.tobytes())
            results.append(hashlib.sha256(ink).hexdigest())
            bitmap.close()
            page.close()
    return results
