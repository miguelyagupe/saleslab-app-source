"""Versioned critic-only source context; never authorizes new analyst claims."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.value_objects import Period
from saleslab_v2.normalization.periods import PeriodNormalizer
from saleslab_v2.reports.contracts import REPORT_LOCALES
from saleslab_v2.reports.presentation import LABELS, finding_text

LEGACY_CONTEXT_VERSION = "financial-critic-context-v2"
CONTEXT_VERSION = "financial-critic-context-v3"


def presentation_preview(draft, context):
    """Use the same value binding and formatting as the publication renderer."""
    language = context["language"]
    locale = REPORT_LOCALES[language]
    return {
        "render_version": "financial-render-v2",
        "locale": locale,
        "rounding_notice": LABELS[language]["rounding"],
        "findings": [
            {
                "finding_id": f["finding_id"],
                "statement": finding_text(f, locale, trace_rounding=True),
                "scope": deepcopy(f["scope"]),
            }
            for f in draft["findings"]
        ],
    }


def available_comparatives(stages):
    """Called only after full persisted source/stage/draft verification."""
    rows = unpack(stages["normalization"].payload["observations"])
    fields = (
        "concept_id",
        "value",
        "unit",
        "currency",
        "period",
        "scope",
        "dimensions",
        "source_document_id",
        "source_content_hash",
        "source_reference",
    )
    return [
        {key: deepcopy(row[key]) for key in fields}
        for row in rows
        if row["normalization_status"] == "normalized"
        and row["value"] is not None
        and not row["issues"]
        and row["period"]["comparison_basis"] == "comparative"
    ]


def comparative_directions(stages):
    """Exact ordering only, with unique, matching source bases and prior periods.

    No subtraction, ratios, rounding, derived metrics or capability changes.
    Ambiguous pairs are omitted rather than selecting a convenient source row.
    """
    rows = unpack(stages["normalization"].payload["observations"])
    valid = [
        row
        for row in rows
        if row["normalization_status"] == "normalized"
        and row["value"] is not None
        and not row["issues"]
    ]
    basis = ("concept_id", "unit", "currency", "scope", "dimensions")

    def key(row):
        return tuple(row[field] for field in basis) + (
            row["period"]["granularity"],
            row["period"].get("fiscal_year_end_month"),
        )

    def source(row):
        return {
            field: deepcopy(row[field])
            for field in (
                "value",
                "period",
                "source_document_id",
                "source_content_hash",
                "source_reference",
            )
        }

    results = []
    for current in valid:
        if current["period"]["comparison_basis"] != "current":
            continue
        matches = [row for row in valid if key(row) == key(current)]
        # Multiple current observations are ambiguous even when values agree.
        if sum(r["period"]["comparison_basis"] == "current" for r in matches) != 1:
            continue
        priors = [
            row
            for row in matches
            if row["period"]["comparison_basis"] == "comparative"
            and row["period"]["end_date"] < current["period"]["start_date"]
        ]
        if len(priors) != 1:
            continue
        prior = priors[0]
        try:
            periods = [
                Period(
                    **{
                        **row["period"],
                        "start_date": date.fromisoformat(row["period"]["start_date"]),
                        "end_date": date.fromisoformat(row["period"]["end_date"]),
                    }
                )
                for row in (current, prior)
            ]
        except (ValueError, TypeError):
            continue
        if not PeriodNormalizer().comparable(*periods).comparable:
            continue
        now, before = Decimal(current["value"]), Decimal(prior["value"])
        if not now.is_finite() or not before.is_finite():
            continue
        results.append(
            {
                **{field: deepcopy(current[field]) for field in basis},
                "current": source(current),
                "comparative": source(prior),
                "direction_current_vs_comparative": (
                    "increase"
                    if now > before
                    else "decrease" if now < before else "unchanged"
                ),
            }
        )
    return results
