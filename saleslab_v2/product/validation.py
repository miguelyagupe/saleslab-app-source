"""Read-only projection of actual gates and planned reconciliation checks."""

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.reports.contracts import REPORT_LOCALES
from saleslab_v2.reports.presentation import format_value
from saleslab_v2.reports.requirements import requirement_text

from .reconciliation_preview import reconciliation_evidence

# Preview-only labels: do not extend the frozen publication renderer catalog.
INPUT_LABELS = {
    "identified_period": (
        "Período identificado",
        "Identified period",
        "Período identificado",
        "Période identifiée",
    ),
    "currency": (
        "Moeda confirmada",
        "Confirmed currency",
        "Moneda confirmada",
        "Devise confirmée",
    ),
    "scale": (
        "Escala confirmada",
        "Confirmed scale",
        "Escala confirmada",
        "Échelle confirmée",
    ),
    "scope": (
        "Perímetro identificado",
        "Identified scope",
        "Perímetro identificado",
        "Périmètre identifié",
    ),
    "metric_definition": (
        "Definição da métrica",
        "Metric definition",
        "Definición de la métrica",
        "Définition de l’indicateur",
    ),
    "mapped_inputs": (
        "Rubricas confirmadas",
        "Confirmed account mappings",
        "Partidas confirmadas",
        "Rubriques confirmées",
    ),
    "non_zero_denominator": (
        "Denominador diferente de zero",
        "Non-zero denominator",
        "Denominador distinto de cero",
        "Dénominateur non nul",
    ),
    "two_comparable_periods": (
        "Dois períodos comparáveis",
        "Two comparable periods",
        "Dos períodos comparables",
        "Deux périodes comparables",
    ),
    "consistent_granularity": (
        "Períodos com granularidade compatível",
        "Compatible period granularity",
        "Períodos con granularidad compatible",
        "Granularité compatible des périodes",
    ),
    "consistent_scope": (
        "Mesmo perímetro de análise",
        "Consistent analysis scope",
        "Mismo perímetro de análisis",
        "Périmètre d’analyse cohérent",
    ),
    "comparison": (
        "Comparação sustentada pelos dados",
        "Data-supported comparison",
        "Comparación sustentada por los datos",
        "Comparaison étayée par les données",
    ),
}


def input_label(code, language):
    labels = INPUT_LABELS.get(code)
    return (
        f"{labels[("pt", "en", "es", "fr").index(language)]} ({code})"
        if labels
        else requirement_text(code, language)
    )


def validation_preview(request, evaluated, language, original_rows=()):
    quality = unpack(canonicalize(evaluated.payloads[3]))
    results = {r.rule_id: r for r in evaluated.reconciliations}
    errors = {e["rule"]: e["code"] for e in evaluated.payloads[2]["errors"]}
    checks = []
    for step in request.reconciliations:
        result = results.get(step.rule_id)
        item = {
            "rule_id": step.rule_id,
            "status": "not_evaluated",
            "expected": None,
            "observed": None,
            "absolute_delta": None,
            "display_expected": None,
            "display_observed": None,
            "display_delta": None,
            "currency": None,
            "source_references": [],
            "result_hash": None,
            "reconciliation_evidence": None,
            "reason_codes": [errors.get(step.rule_id, "pipeline.mapping_required")],
        }
        if result is not None:
            raw = unpack(canonicalize(result.payload()))
            item.update(
                {
                    key: raw[key]
                    for key in (
                        "status",
                        "expected",
                        "observed",
                        "absolute_delta",
                        "currency",
                        "source_references",
                        "result_hash",
                        "reason_codes",
                    )
                }
            )
            for key, display in (
                ("expected", "display_expected"),
                ("observed", "display_observed"),
                ("absolute_delta", "display_delta"),
            ):
                item[display] = format_value(
                    {
                        "value": item[key],
                        "unit": result.unit,
                        "currency": result.currency,
                    },
                    REPORT_LOCALES[language],
                )
            item["reconciliation_evidence"] = reconciliation_evidence(
                result, original_rows, language
            )
        checks.append(item)
    return {
        "quality_level": quality["coverage"]["quality_level"],
        "reconciliations": checks,
        "capabilities": [
            {
                "capability": d["capability"],
                "status": d["decision_status"],
                "reason_codes": d["reason_codes"],
                "requirements": [
                    {"code": code, "label": input_label(code, language)}
                    for code in d["requirements_to_unlock"]
                ],
            }
            for d in quality["decisions"]
        ],
    }
