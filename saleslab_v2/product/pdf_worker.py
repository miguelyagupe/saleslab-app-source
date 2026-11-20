"""Resource-bounded text decoder. No application settings or provider imports."""

# ruff: noqa: BLE001 -- sanitize decoder exceptions at the isolated process boundary.

import base64
import io
import json
import logging
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 6))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    logging.disable(logging.CRITICAL)
    import pypdf

    request = json.loads(sys.stdin.buffer.read(7 * 1024 * 1024))
    content = base64.b64decode(request["content"], validate=True)
    if len(content) > 5 * 1024 * 1024:
        return {"error": "bounds"}
    reader = pypdf.PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted:
        return {"error": "encrypted"}
    count = len(reader.pages)
    numbers = request["pages"]
    if (
        not 1 <= count <= 300
        or not 1 <= len(numbers) <= 8
        or any(type(n) is not int or not 1 <= n <= count for n in numbers)
    ):
        return {"error": "pages"}
    pages, total, stream_total = [], 0, 0
    for number in numbers:
        page = reader.pages[number - 1]
        if page.rotation % 360:
            return {"error": "rotation"}
        stream = page.get_contents()
        size = len(stream.get_data()) if stream is not None else 0
        stream_total += size
        if size > 4 * 1024 * 1024 or stream_total > 16 * 1024 * 1024:
            return {"error": "bounds"}
        unsupported = []

        def inspect_text(text, cm, tm, font, size, unsupported=unsupported):
            if text.strip() and (
                font is None
                or any(abs(float(m[i])) > 1e-8 for m in (cm, tm) for i in (1, 2))
            ):
                unsupported.append(True)

        page.extract_text(visitor_text=inspect_text)
        if unsupported:
            return {"error": "orientation"}
        text = page.extract_text(
            extraction_mode="layout", layout_mode_space_vertically=False
        )
        if not text or not text.strip():
            return {"error": "no_text"}
        if "\ufffd" in text or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
            return {"error": "encoding"}
        lines = text.splitlines()
        total += len(text.encode("utf-8"))
        if (
            total > 200000
            or len(lines) > 1000
            or any(len(line) > 2000 for line in lines)
        ):
            return {"error": "bounds"}
        pages.append({"page": number, "lines": lines})
    return {"engine": "pypdf:" + pypdf.__version__, "page_count": count, "pages": pages}


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        result = {"error": "invalid"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
