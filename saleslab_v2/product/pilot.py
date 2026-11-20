"""A curated public-data acceptance case; never authorizes rollout or model use."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.pipeline.evaluator import evaluate

from .contracts import UploadChoices
from .ingestion import TERMS, parse_upload
from .planning import prepare_upload

DATASET_ID = "jm-public-2024-v1"
FILENAME = "jm-2024.csv"
SOURCE_SHA256 = "b69c33f1ddb98b55e48947cc248dc8d04e14fab5280e5bf01012403298dc3cf2"
SOURCES = [
    {
        "id": "income",
        "title": "Consolidated income statement 2024/2023",
        "url": "https://reports.jeronimomartins.com/annual-report/2024/financial-statements/consolidated-financial-statements/consolidated-income-statement-by-functions.html",
    },
    {
        "id": "cash_flow",
        "title": "Consolidated cash flow statement 2024/2023",
        "url": "https://reports.jeronimomartins.com/annual-report/2024/financial-statements/consolidated-financial-statements/consolidated-cash-flow-statement.html",
    },
]
# Independent source facts, before scale conversion; CSV lines retain these figures.
FACTS = (
    ("csv:2", "income", "Sales and services rendered", "2024", "33464"),
    ("csv:3", "income", "Profit before non-controlling interests", "2024", "606"),
    ("csv:4", "cash_flow", "Cash flow from operating activities", "2024", "1654"),
    ("csv:5", "income", "Sales and services rendered", "2023", "30608"),
    ("csv:6", "income", "Profit before non-controlling interests", "2023", "773"),
    ("csv:7", "cash_flow", "Cash flow from operating activities", "2023", "2025"),
)
# Rational truth is independent of the engine's Decimal implementation. The tiny
# division tolerance accommodates its documented 100-digit arithmetic, not materiality.
GOLDENS = {
    "revenue": (33464000000, 1, "monetary", "EUR"),
    "net_margin_pct": (60600, 33464, "percentage", None),
    "cfo": (1654000000, 1, "monetary", "EUR"),
    "revenue_growth_reported_pct": (285600, 30608, "percentage", None),
}


def sample():
    content = (
        Path(__file__)
        .with_name("samples")
        .joinpath(FILENAME)
        .read_text(encoding="utf-8")
    )
    if sha256(content.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError("The public pilot dataset differs from its reviewed version.")
    return {
        "dataset_id": DATASET_ID,
        "filename": FILENAME,
        "content": content,
        "sha256": sha256(content.encode()).hexdigest(),
        "choices": UploadChoices(
            source_language="en",
            format_locale="en-GB",
            report_language="pt",
            target_period="2024",
            mappings={
                f[0]: ("revenue", "net_income", "cfo")[i % 3]
                for i, f in enumerate(FACTS)
            },
        ).model_dump(
            exclude={
                "accounting_parentheses",
                "fiscal_year_end_month",
                "source_currency",
                "source_scale",
                "normalization_version",
            }
        ),
        "entity": "Jerónimo Martins · consolidado · 2024/2023",
        "sources": deepcopy(SOURCES),
        "source_facts": [
            {
                **dict(
                    zip(
                        (
                            "location",
                            "source",
                            "published_label",
                            "period",
                            "published_millions_eur",
                        ),
                        row,
                    )
                ),
                "mapped_label": TERMS[
                    ("revenue", "net_income", "cfo")[i % 3]
                ].canonical_label,
            }
            for i, row in enumerate(FACTS)
        ],
        "curation": "Six selected public figures manually transcribed with original published labels and explicit proposed mappings. Consolidated net income includes non-controlling interests (606/773), not profit attributable to parent shareholders (599/756). Operating cash flow is 1654/2025, not management cash flow (-62/345). This is not PDF extraction or complete-statement coverage.",
        "basis_note": "O resultado líquido consolidado inclui interesses não controladores; o caixa é operacional após impostos. São bases diferentes do lucro atribuível aos acionistas da empresa e do indicador de cash flow da gestão.",
        "verified_on": "2026-10-22",
        "semantic_quality": "not_certified",
        "rollout_authorized": False,
    }


def check_metrics(observations):
    rows = list(observations)
    if len(rows) != len(GOLDENS) or any(
        not isinstance(r, dict) or not isinstance(r.get("metric_id"), str) for r in rows
    ):
        return {"pass": False, "checks": []}
    by_id = {r["metric_id"]: r for r in rows}
    checks = []
    for metric_id, (numerator, denominator, unit, currency) in GOLDENS.items():
        row = by_id.get(metric_id)
        passed = False
        if row and isinstance(row.get("value"), str) and len(row["value"]) <= 256:
            try:
                value = Decimal(row["value"])
                tolerance = Fraction(0) if denominator == 1 else Fraction(1, 10**90)
                passed = (
                    value.is_finite()
                    and -200 <= value.as_tuple().exponent <= 200
                    and abs(Fraction(value) - Fraction(numerator, denominator))
                    <= tolerance
                    and row.get("unit") == unit
                    and row.get("currency") == currency
                )
            except (ValueError, TypeError, ArithmeticError):
                passed = False
        checks.append(
            {
                "metric_id": metric_id,
                "pass": bool(passed),
                "actual": row.get("value") if row else None,
                "expected_ratio": f"{numerator}/{denominator}",
                "unit": unit,
                "currency": currency,
            }
        )
    return {
        "pass": len(rows) == len(by_id) == len(GOLDENS)
        and set(by_id) == set(GOLDENS)
        and all(c["pass"] for c in checks),
        "checks": checks,
    }


def offline_acceptance():
    case = sample()
    workspace_id, user_id = uuid4(), uuid4()
    choices = UploadChoices(**case["choices"])
    parsed = parse_upload(
        workspace_id, case["filename"], case["content"].encode(), choices
    )
    request, preview = prepare_upload(
        parsed, choices, user_id, "public-data-acceptance"
    )
    evaluation = evaluate(request, uuid4(), datetime.now(UTC))
    metrics = check_metrics(
        unpack(canonicalize(asdict(o))) for _, o, _ in evaluation.metrics
    )
    facts = {f[0]: f for f in FACTS}
    facts_match = len(evaluation.normalized) == len(FACTS) and all(
        o.source_reference in facts
        and o.value == Decimal(facts[o.source_reference][4]) * 1000000
        and o.currency == "EUR"
        and o.unit == "monetary"
        and o.scope == "consolidated"
        and o.period.label == facts[o.source_reference][3]
        and o.period.comparison_basis
        == ("current" if o.period.label == "2024" else "comparative")
        for o in evaluation.normalized
    )
    decisions = {
        d.capability.value: d.decision_status.value for d in evaluation.decisions
    }
    bounded = (
        decisions.get("calculate") == "allowed"
        and decisions.get("compare") == "allowed"
        and decisions.get("forecast") == "blocked"
        and decisions.get("recommend_material") == "blocked"
    )
    return {
        "dataset_id": DATASET_ID,
        "source_sha256": case["sha256"],
        "status": (
            "pass"
            if preview["ready"] and facts_match and metrics["pass"] and bounded
            else "fail"
        ),
        "metrics": metrics,
        "source_facts_match": facts_match,
        "capabilities": decisions,
        "coverage": "selected_figures_only",
        "provider_calls": 0,
        "semantic_quality": "not_certified",
        "rollout_authorized": False,
    }
