"""Read-only reconciliation choices; catalog membership is not source evidence."""

import re

from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1

VERSION = "financial-month-end-reconciliation-catalog-v1"
MAX_REQUIRED_RECONCILIATIONS = 20
LANGUAGES = ("pt", "en", "es", "fr")
LABELS = {
    "REC-BS-001": ("Balanço", "Balance sheet", "Balance", "Bilan"),
    "REC-CF-001": (
        "Movimentos de caixa",
        "Cash movement",
        "Movimientos de efectivo",
        "Mouvements de trésorerie",
    ),
    "REC-PL-001": (
        "Ponte do resultado",
        "Profit and loss bridge",
        "Puente del resultado",
        "Rapprochement du résultat",
    ),
    "REC-EBITDA-001": (
        "Ponte de EBITDA",
        "EBITDA bridge",
        "Puente de EBITDA",
        "Rapprochement de l’EBITDA",
    ),
    "REC-DEBT-001": (
        "Movimentos da dívida",
        "Debt roll-forward",
        "Movimientos de deuda",
        "Mouvements de dette",
    ),
    "REC-LEASE-001": (
        "Movimentos de leasing",
        "Lease roll-forward",
        "Movimientos de arrendamientos",
        "Mouvements des locations",
    ),
    "REC-GROWTH-001": (
        "Ponte do crescimento reportado",
        "Reported growth bridge",
        "Puente del crecimiento publicado",
        "Rapprochement de la croissance publiée",
    ),
    "REC-DEBT-RATE-001": (
        "Composição da dívida por taxa contratual",
        "Contractual debt rate composition",
        "Composición de deuda por tipo contractual",
        "Composition de dette par taux contractuel",
    ),
    "REC-DEBT-MATURITY-001": (
        "Distribuição dos vencimentos da dívida",
        "Debt maturity distribution",
        "Distribución de vencimientos de deuda",
        "Répartition des échéances de dette",
    ),
    "REC-CAPEX-001": (
        "Classificação do capex pago",
        "Cash capex classification",
        "Clasificación del capex pagado",
        "Classification du capex payé",
    ),
    "REC-CAPEX-002": (
        "Capex versus aquisições nos fluxos de caixa",
        "Capex versus cash-flow purchases",
        "Capex frente a adquisiciones en flujos de caja",
        "Capex et acquisitions dans les flux de trésorerie",
    ),
    "REC-WC-CLOSING-001": (
        "Fundo de maneio: componentes finais",
        "Working capital: closing components",
        "Circulante: componentes finales",
        "Fonds de roulement : composantes finales",
    ),
    "REC-WC-OPENING-001": (
        "Fundo de maneio: componentes iniciais",
        "Working capital: opening components",
        "Circulante: componentes iniciales",
        "Fonds de roulement : composantes initiales",
    ),
    "REC-WC-CHANGE-001": (
        "Fundo de maneio: variação de saldos",
        "Working capital: balance change",
        "Circulante: variación de saldos",
        "Fonds de roulement : variation des soldes",
    ),
}


def reconciliation_catalog(registry=None):
    """Detached, deterministic labels and supported versions, without policy defaults.

    The existing close evaluator checks the actual result period/currency/scope.
    This discovery contract neither changes those checks nor enters saved hashes.
    Future registered IDs remain visible with an explicit canonical-label fallback.
    """
    registry = registry if registry is not None else FINANCIAL_RULES_V1
    grouped = {}
    for rule in registry.rules:
        if (
            not isinstance(rule.rule_id, str)
            or not re.fullmatch(r"REC-[A-Z0-9]+(?:-[A-Z0-9]+)*", rule.rule_id)
            or len(rule.rule_id) > 100
        ):
            raise ValueError("invalid reconciliation catalog identifier")
        if not isinstance(rule.version, str) or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,32}", rule.version
        ):
            raise ValueError("invalid reconciliation catalog version")
        grouped.setdefault(rule.rule_id, set()).add(rule.version)
    if not 1 <= len(grouped) <= 100:
        raise ValueError("reconciliation catalog requires 1–100 distinct rules")

    def version_key(version):
        return (0, int(version)) if version.isdecimal() else (1, version)

    rules = []
    for rule_id, versions in sorted(grouped.items()):
        if len(versions) > 20:
            raise ValueError(
                "reconciliation catalog supports at most 20 versions per rule"
            )
        labels = LABELS.get(rule_id)
        rules.append(
            {
                "rule_id": rule_id,
                "versions": sorted(versions, key=version_key),
                "labels": dict(zip(LANGUAGES, labels or (rule_id,) * 4)),
                "label_fallback": labels is None,
            }
        )
    return {
        "schema": VERSION,
        "selection": "explicit",
        "max_required_reconciliations": MAX_REQUIRED_RECONCILIATIONS,
        "result_period_requirement": "target_month_current_currency_scope",
        "rules": rules,
    }
