"""Monthly presentation completeness and execution without changing saved evidence."""

import ast
import json
import subprocess
from pathlib import Path

import pytest

from saleslab_v2.product.month_end_catalog import LABELS as RULE_LABELS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "financial-v2.js").read_text()
START = SOURCE.index("  const closeMessages = ")
CATALOG, _ = json.JSONDecoder().raw_decode(
    SOURCE[START + len("  const closeMessages = ") :]
)
PRESENTATION = SOURCE[START : SOURCE.index("\n  function download(", START)]


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
def test_monthly_copy_covers_current_reasons_rules_and_every_language(language):
    messages = CATALOG[language]
    assert messages.keys() == CATALOG["pt"].keys()
    assert all(isinstance(v, str) and v.strip() for v in messages.values())
    # Every explicit monthly reason emitted by the evaluator needs a translation.
    tree = ast.parse((ROOT / "saleslab_v2/month_end/evaluator.py").read_text())
    reasons = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value.startswith("month_end.")
    }
    for reason in reasons - {"month_end.reconciliation_"}:
        assert "reason." + reason.removeprefix("month_end.") in messages
    for key, labels in RULE_LABELS.items():
        assert messages[key] == labels[("pt", "en", "es", "fr").index(language)]
    assert messages["satisfied"] != messages["confirmed_by_user"]


def execute(payload):
    # Exercise the actual renderer on a tiny text-only DOM. Real-browser coverage
    # separately checks downloads, safe markup and responsive layout.
    script = (
        """
const node = (tag, text = "") => ({tag, text, children: [], attrs: {},
 append(...children) { this.children.push(...children); },
 replaceChildren(...children) { this.children = children; },
 setAttribute(k,v) { this.attrs[k]=v; }});
"""
        + PRESENTATION
        + """
const payload = JSON.parse(process.argv[1]);
const before = JSON.stringify(payload); const root=node("div");
renderClose(payload, root);
const flatten = n => [n.text, ...n.children.map(flatten)].join(" ");
console.log(JSON.stringify({text:flatten(root),lang:root.lang,aria:root.attrs["aria-label"],unchanged:before===JSON.stringify(payload),unknown:closeReason("__proto__",payload.result.language)}));
"""
    )
    value = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(value.stdout)


def payload(language, *, saved=True, reason="month_end.zero_comparison_base"):
    return {
        "filename": "<img src=x onerror=alert(1)>",
        "source_hash": "a" * 64,
        "persisted": saved,
        "upload_choices": {"report_language": "pt"},
        "result": {
            "language": language,
            "preparation_readiness": "incomplete",
            "policy": {
                "policy_id": "synthetic",
                "version": "1",
                "materiality_basis": "SOURCE POLICY",
            },
            "checklist": [
                {
                    "item_id": "statement/balance_sheet",
                    "category": "statement",
                    "title": "ORIGINAL TITLE",
                    "status": "confirmed_by_user",
                    "reason_code": reason,
                    "evidence": [{"location": "csv:2", "content_hash": "b" * 64}],
                }
            ],
            "comparisons": [
                {
                    "key": "SOURCE KEY",
                    "relation": "month_on_month",
                    "current_value": "-123.456789012345678901",
                    "prior_value": "0",
                    "delta": "-123.456789012345678901",
                    "percentage": None,
                    "material": True,
                }
            ],
            "missing_data_requests": [{"reason_code": reason}],
        },
    }


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
@pytest.mark.parametrize("saved", [True, False])
def test_renderer_uses_saved_language_and_preserves_exact_values_and_sources(
    language, saved
):
    rendered = execute(payload(language, saved=saved))
    text = CATALOG[language]
    for key in (
        "incomplete",
        "limits",
        "confirmed_by_user",
        "balance_sheet",
        "reason.zero_comparison_base",
        "percentage_unavailable",
        "threshold_reached",
        "month_on_month",
        "missing",
        "saved" if saved else "unsaved",
    ):
        assert text[key] in rendered["text"]
    assert "-123.456789012345678901" in rendered["text"]
    assert "SOURCE POLICY" in rendered["text"] and "csv:2" in rendered["text"]
    assert "ORIGINAL TITLE" in rendered["text"]
    assert "__proto__" in rendered["unknown"]
    assert rendered["lang"] == language and rendered["aria"] == text["result_label"]
    assert rendered["unchanged"]
    if language != "pt":
        assert CATALOG["pt"]["limits"] not in rendered["text"]


@pytest.mark.parametrize("language", ["unsupported", "__proto__", "constructor"])
def test_unknown_language_and_reason_have_visible_fallback_and_keep_canonical_codes(
    language,
):
    rendered = execute(payload(language, reason="future.rule"))
    assert rendered["lang"] == "en"
    assert CATALOG["en"]["language_fallback"] in rendered["text"]
    assert CATALOG["en"]["fallback_reason"] in rendered["text"]
    assert "future.rule" in rendered["text"] and language in rendered["text"]
    assert rendered["unchanged"]
