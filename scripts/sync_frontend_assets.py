"""Bind the HTML script URL to its content; use --check in CI."""

import argparse
import re
from hashlib import sha256
from pathlib import Path


def sync(root, *, check=False):
    digest = sha256((root / "financial-v2.js").read_bytes()).hexdigest()
    path = root / "index.html"
    html = path.read_text(encoding="utf-8")
    pattern = r'(<script\s+src=")financial-v2\.js(?:\?v=[a-f0-9]+)?("></script>)'
    expected, count = re.subn(pattern, rf"\g<1>financial-v2.js?v={digest}\g<2>", html)
    if count != 1:
        raise ValueError("Expected exactly one financial script reference.")
    if check:
        return html == expected
    if html != expected:
        path.write_text(expected, encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if not sync(args.root, check=args.check):
            print(
                "Frontend asset URL is stale. Run python scripts/sync_frontend_assets.py."
            )
            return 1
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
