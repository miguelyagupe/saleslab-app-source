"""Prevent new HTML releases from reusing a stale browser/CDN script URL."""

import json
import re
import subprocess
import sys
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync_frontend_assets.py"


class FrontendReleaseTests(unittest.TestCase):
    def test_committed_script_url_matches_exact_javascript_bytes(self):
        html = (ROOT / "index.html").read_text()
        digest = sha256((ROOT / "financial-v2.js").read_bytes()).hexdigest()
        matches = re.findall(
            r'<script src="financial-v2\.js\?v=([a-f0-9]{64})"></script>', html
        )
        self.assertEqual(matches, [digest])

    def test_edits_invalidate_the_url_and_sync_is_idempotent(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "index.html").write_text(
                '<p>Keep this</p><script src="financial-v2.js"></script>'
            )
            asset = root / "financial-v2.js"
            asset.write_text("// first release\n")
            command = [sys.executable, str(SCRIPT), "--root", str(root)]
            self.assertEqual(
                subprocess.run(
                    command + ["--check"], capture_output=True, check=False
                ).returncode,
                1,
            )
            subprocess.run(command, check=True, capture_output=True)
            first = (root / "index.html").read_bytes()
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((root / "index.html").read_bytes(), first)
            self.assertIn(b"Keep this", first)
            asset.write_text("// second release\n")
            self.assertEqual(
                subprocess.run(
                    command + ["--check"], capture_output=True, check=False
                ).returncode,
                1,
            )
            self.assertEqual((root / "index.html").read_bytes(), first)
            subprocess.run(command, check=True, capture_output=True)
            second = (root / "index.html").read_text()
            self.assertIn(sha256(asset.read_bytes()).hexdigest(), second)
            self.assertNotEqual(second.encode(), first)
            subprocess.run(command + ["--check"], check=True, capture_output=True)

    def test_ambiguous_script_references_fail_without_modifying_html(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            original = '<script src="financial-v2.js"></script>' * 2
            (root / "index.html").write_text(original)
            (root / "financial-v2.js").write_text("// asset")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root)],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual((root / "index.html").read_text(), original)

    def test_entry_points_revalidate_without_removing_existing_security_headers(self):
        config = json.loads((ROOT / "vercel.json").read_text())
        rules = {
            r["source"]: {h["key"].lower(): h["value"] for h in r["headers"]}
            for r in config["headers"]
        }
        self.assertEqual(
            rules["/(.*)"]["cross-origin-opener-policy"], "same-origin-allow-popups"
        )
        for path in ("/", "/index.html", "/financial-v2.js"):
            self.assertIn("must-revalidate", rules[path]["cache-control"])
            self.assertIn("max-age=0", rules[path]["cache-control"])
