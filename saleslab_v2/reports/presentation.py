"""One ordered document for web and PDF; no channel-specific financial content."""

import json
from dataclasses import dataclass
from decimal import Decimal

from saleslab_v2.analyst.contracts import TOKEN
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.metrics.rounding import POLICIES

from .contracts import InvalidReport, ReportResult
from .requirements import HEADINGS, NOTICES, requirement_text

# Canonical technical IDs and original source/critic text are never translated by
# guessing. Their complete original content is retained in the evidence appendix.
LABELS = {
    "pt": {
        "financial_report": "Relatório financeiro",
        "findings": "Observações verificadas",
        "limitations": "Limitações e avisos",
        "missing": "Dados em falta",
        "appendix": "Evidência e contratos técnicos",
        "none": "Nenhum objeto disponível nesta versão.",
        "policy": "Relatório factual. Sem ranking de materialidade, previsões ou recomendações de investimento. A aprovação não certifica a precisão de um modelo em produção.",
        "technical": "O anexo preserva os identificadores canónicos e o texto original. Códigos técnicos sem tradução aprovada são apresentados explicitamente, sem tradução automática.",
        "warning": "Aviso da auditoria",
        "quality_warning": "Aviso sobre a qualidade dos dados",
        "gap": "Requisito em falta (código canónico)",
        "source": "Fonte",
        "rounding": "Os valores apresentados seguem o arredondamento definido para cada métrica. Os valores completos constam do anexo.",
    },
    "en": {
        "financial_report": "Financial report",
        "findings": "Verified observations",
        "limitations": "Limitations and warnings",
        "missing": "Missing data",
        "appendix": "Evidence and technical contracts",
        "none": "No object available in this version.",
        "policy": "Factual report. No materiality ranking, forecasts or investment recommendations. Approval does not certify production model accuracy.",
        "technical": "The appendix preserves canonical identifiers and original text. Technical codes without an approved translation are explicitly displayed without automatic translation.",
        "warning": "Audit warning",
        "quality_warning": "Data quality warning",
        "gap": "Missing requirement (canonical code)",
        "source": "Source",
        "rounding": "Displayed values follow each metric's defined rounding. Full values are retained in the appendix.",
    },
    "es": {
        "financial_report": "Informe financiero",
        "findings": "Observaciones verificadas",
        "limitations": "Limitaciones y advertencias",
        "missing": "Datos faltantes",
        "appendix": "Evidencia y contratos técnicos",
        "none": "Ningún objeto disponible en esta versión.",
        "policy": "Informe factual. Sin clasificación de materialidad, previsiones ni recomendaciones de inversión. La aprobación no certifica la precisión de un modelo en producción.",
        "technical": "El anexo conserva los identificadores canónicos y el texto original. Los códigos técnicos sin traducción aprobada se muestran explícitamente sin traducción automática.",
        "warning": "Advertencia de auditoría",
        "quality_warning": "Advertencia sobre la calidad de los datos",
        "gap": "Requisito faltante (código canónico)",
        "source": "Fuente",
        "rounding": "Los valores mostrados siguen el redondeo definido para cada métrica. Los valores completos se conservan en el anexo.",
    },
    "fr": {
        "financial_report": "Rapport financier",
        "findings": "Observations vérifiées",
        "limitations": "Limites et avertissements",
        "missing": "Données manquantes",
        "appendix": "Preuves et contrats techniques",
        "none": "Aucun objet disponible dans cette version.",
        "policy": "Rapport factuel. Sans classement de matérialité, prévisions ou recommandations d’investissement. L’approbation ne certifie pas la précision d’un modèle en production.",
        "technical": "L’annexe conserve les identifiants canoniques et le texte original. Les codes techniques sans traduction approuvée sont affichés explicitement, sans traduction automatique.",
        "warning": "Avertissement d’audit",
        "quality_warning": "Avertissement sur la qualité des données",
        "gap": "Exigence manquante (code canonique)",
        "source": "Source",
        "rounding": "Les valeurs affichées suivent l’arrondi défini pour chaque indicateur. Les valeurs complètes figurent en annexe.",
    },
}


@dataclass(frozen=True)
class Block:
    key: str
    kind: str
    text: str


def format_value(evidence, locale, *, trace_rounding=False):
    """No float, precision loss, implicit scale conversion or inferred units."""
    value = format(Decimal(evidence["value"]), "f")
    if trace_rounding and evidence.get("trace", {}).get("rounding_policy"):
        trace = evidence["trace"]
        policy = POLICIES.get(trace["rounding_policy"])
        if (
            policy is None
            or trace.get("rounding_version") != policy.version
            or Decimal(trace["exact_output"]) != Decimal(evidence["value"])
            or trace.get("rounded_output") is None
            or Decimal(trace["rounded_output"]) != policy.apply(Decimal(value))
        ):
            raise InvalidReport("display rounding differs from verified trace policy")
        value = format(policy.apply(Decimal(value)), "f")
    sign = "-" if value.startswith("-") else ""
    value = value.lstrip("-")
    integer, dot, fraction = value.partition(".")
    grouping, decimal = {
        "pt-PT": (" ", ","),
        "en-GB": (",", "."),
        "es-ES": (".", ","),
        "fr-FR": (" ", ","),
    }[locale]
    groups = []
    while integer:
        groups.append(integer[-3:])
        integer = integer[:-3]
    formatted = sign + grouping.join(reversed(groups))
    if dot:
        formatted += decimal + fraction
    unit = evidence["unit"]
    currency = evidence["currency"]
    display_unit = {"percentage": "%", "ratio": "x"}.get(unit, unit)
    return (
        formatted
        + " "
        + (currency if unit == "monetary" and currency else display_unit)
    )


def finding_text(finding, locale, *, trace_rounding=False):
    bindings = finding["value_bindings"]
    template = finding["statement_template"]
    if set(TOKEN.findall(template)) != set(bindings):
        raise InvalidReport("finding has unresolved or additional evidence bindings")
    result = TOKEN.sub(
        lambda m: format_value(
            bindings[m.group(1)], locale, trace_rounding=trace_rounding
        ),
        template,
    )
    if "{{" in result or "}}" in result:
        raise InvalidReport("unresolved evidence placeholder")
    return result


def document(payload):
    """Front matter for readers, complete lossless contract appendix for inspection."""
    payload = ReportResult.model_validate(payload).model_dump()
    labels = LABELS[payload["language"]]
    blocks = []

    def add(key, kind, text):
        blocks.append(Block(key, kind, text))

    add("title", "title", labels["financial_report"])
    add("identity", "meta", f"{payload['report_id']} / v{payload['report_version']}")
    add("date", "meta", payload["generated_at"])
    add("policy", "notice", labels["policy"])
    if payload["render_version"] != "financial-render-v1":
        add("rounding", "notice", labels["rounding"])
    add("findings", "heading", labels["findings"])
    for finding in payload["findings"]:
        key = "finding/" + finding["finding_id"]
        add(key, "subheading", finding["title"])
        add(
            key + "/statement",
            "body",
            finding_text(
                finding,
                payload["locale"],
                trace_rounding=payload["render_version"] != "financial-render-v1",
            ),
        )
        periods = finding["scope"]["period"]
        add(
            key + "/period",
            "meta",
            f"{periods['start_date']} / {periods['end_date']} | {periods['comparison_basis']}",
        )
        add(key + "/evidence", "meta", " / ".join(finding["evidence_ids"]))
    add("limitations", "heading", labels["limitations"])
    if not payload["limitations"]:
        add("limitations/none", "body", labels["none"])
    for item in payload["limitations"]:
        detail = item["detail"]
        label = labels[
            {"audit_warning": "warning", "quality_issue": "quality_warning"}[
                item["kind"]
            ]
        ]
        add(
            "limitation/" + item["limitation_id"],
            "notice",
            label
            + ": "
            + detail.get("message", detail.get("message_key", detail.get("code", ""))),
        )
        if detail.get("remediation"):
            add("remediation/" + item["limitation_id"], "body", detail["remediation"])
    reader_version = payload["render_version"] in {
        "financial-render-v3",
        "financial-render-v4",
    }
    add(
        "missing",
        "heading",
        HEADINGS[payload["language"]] if reader_version else labels["missing"],
    )
    if reader_version and payload["missing_data_requests"]:
        add("missing/context", "notice", NOTICES[payload["language"]])
    if not payload["missing_data_requests"]:
        add("missing/none", "body", labels["none"])
    for item in payload["missing_data_requests"]:
        add(
            "missing/" + item["request_id"],
            "body",
            (
                requirement_text(item["requirement_code"], payload["language"])
                if reader_version
                else labels["gap"] + ": " + item["requirement_code"]
            ),
        )
    definitions = payload["methodology"].get("growth_definitions", [])
    if definitions:
        title = {
            "pt": "Definições de crescimento citadas e confirmadas pelo utilizador",
            "en": "User-confirmed growth definitions quoted from the source",
            "es": "Definiciones de crecimiento citadas y confirmadas por el usuario",
            "fr": "Définitions de croissance citées et confirmées par l’utilisateur",
        }[payload["language"]]
        add("growth-definitions", "heading", title)
        for index, definition in enumerate(definitions):
            add(
                "growth-definition/" + str(index),
                "body",
                definition["definition_quote"]
                + " · "
                + definition["location"]
                + " · "
                + definition["content_hash"],
            )
    debt_definitions = payload["methodology"].get("debt_definitions", [])
    if debt_definitions:
        title = {
            "pt": "Definições da composição e dos vencimentos da dívida citadas e confirmadas pelo utilizador",
            "en": "User-confirmed debt composition and maturity definitions quoted from the source",
            "es": "Definiciones de composición y vencimientos de deuda citadas y confirmadas por el usuario",
            "fr": "Définitions de composition et échéances de dette citées et confirmées par l’utilisateur",
        }[payload["language"]]
        add("debt-definitions", "heading", title)
        for index, definition in enumerate(debt_definitions):
            add(
                "debt-definition/" + str(index),
                "body",
                definition["definition_quote"]
                + " · "
                + definition["location"]
                + " · "
                + definition["content_hash"],
            )
    capex_definitions = payload["methodology"].get("capex_definitions", [])
    if capex_definitions:
        title = {
            "pt": "Definições de capex pago e compromissos por executar citadas e confirmadas pelo utilizador",
            "en": "User-confirmed cash capex and outstanding commitment definitions quoted from the source",
            "es": "Definiciones de capex pagado y compromisos pendientes citadas y confirmadas por el usuario",
            "fr": "Définitions de capex payé et d’engagements restant à exécuter citées et confirmées par l’utilisateur",
        }[payload["language"]]
        add("capex-definitions", "heading", title)
        for index, definition in enumerate(capex_definitions):
            add(
                "capex-definition/" + str(index),
                "body",
                definition["definition_quote"]
                + " · "
                + definition["location"]
                + " · "
                + definition["content_hash"],
            )
    working_capital_definitions = payload["methodology"].get(
        "working_capital_definitions", []
    )
    if working_capital_definitions:
        title = {
            "pt": "Perímetro operacional do fundo de maneio confirmado na fonte. Variações de saldos não são efeitos em caixa.",
            "en": "Source-confirmed operating working capital perimeter. Balance changes are not cash flow effects.",
            "es": "Perímetro operativo del circulante confirmado en la fuente. Las variaciones de saldos no son flujos de caja.",
            "fr": "Périmètre opérationnel du fonds de roulement confirmé à la source. Les variations de soldes ne sont pas des flux de trésorerie.",
        }[payload["language"]]
        add("working-capital-definitions", "heading", title)
        for index, definition in enumerate(working_capital_definitions):
            add(
                "working-capital-definition/" + str(index),
                "body",
                definition["definition_quote"]
                + " · "
                + definition["location"]
                + " · "
                + definition["content_hash"],
            )
    roic_sources = payload["methodology"].get("roic_sources", [])
    if roic_sources:
        title = {
            "pt": "ROIC do período: NOPAT reportado, pressupostos fiscais e capital investido médio confirmados na fonte. Não anualizado.",
            "en": "Period ROIC: source-confirmed reported NOPAT, tax assumptions and average invested capital. Not annualized.",
            "es": "ROIC del período: NOPAT publicado, supuestos fiscales y capital invertido medio confirmados en la fuente. No anualizado.",
            "fr": "ROIC de la période : NOPAT publié, hypothèses fiscales et capital investi moyen confirmés à la source. Non annualisé.",
        }[payload["language"]]
        add("roic-sources", "heading", title)
        for index, source in enumerate(roic_sources):
            for role, evidence in sorted(source["evidence"].items()):
                add(
                    "roic-source/" + str(index) + "/" + role,
                    "body",
                    evidence["quote"]
                    + " · "
                    + evidence["location"]
                    + " · "
                    + source["content_hash"],
                )
    add("appendix", "heading", labels["appendix"])
    add("technical", "notice", labels["technical"])

    compact = payload["render_version"] == "financial-render-v4"
    if compact:
        add(
            "technical/references",
            "notice",
            {
                "pt": "Conteúdo técnico repetido é apresentado uma vez. Cada referência indica o caminho do conteúdo idêntico já incluído neste anexo; o JSON conserva todos os campos originais.",
                "en": "Repeated technical content is shown once. Each reference identifies the path of identical content already included in this appendix; JSON retains every original field.",
                "es": "El contenido técnico repetido se muestra una vez. Cada referencia indica la ruta del contenido idéntico ya incluido en este anexo; el JSON conserva todos los campos originales.",
                "fr": "Le contenu technique répété est présenté une seule fois. Chaque référence indique le chemin du contenu identique déjà inclus dans cette annexe ; le JSON conserve tous les champs originaux.",
            }[payload["language"]],
        )
    seen = {}

    def visit(value, path):
        if compact and isinstance(value, (dict, list)) and value:
            identity = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if len(identity) >= 128:
                if identity in seen:
                    add(
                        path + "/reference",
                        "technical",
                        path + " | identical content: " + seen[identity],
                    )
                    return
                seen[identity] = path
        if (
            compact
            and isinstance(value, list)
            and all(not isinstance(item, (dict, list)) for item in value)
        ):
            add(
                path,
                "technical",
                path + " | " + json.dumps(value, ensure_ascii=False, sort_keys=True),
            )
            return
        # Group scalar fields for compact, readable pagination. Every key/value,
        # including empty arrays and nulls, remains visibly present in both channels.
        if isinstance(value, dict) and value:
            scalars = {
                k: v for k, v in value.items() if not isinstance(v, (list, dict))
            }
            if scalars:
                add(
                    path + "/scalars",
                    "technical",
                    path
                    + " | "
                    + json.dumps(scalars, ensure_ascii=False, sort_keys=True),
                )
            # PostgreSQL JSONB does not preserve insertion order. Top-level schema
            # order is restored by ReportResult; nested objects use canonical keys.
            items = value.items() if path == "ReportResult" else sorted(value.items())
            for key, item in items:
                if isinstance(item, (list, dict)):
                    segment = (
                        key.replace("~", "~0").replace("/", "~1") if compact else key
                    )
                    visit(item, path + "/" + segment)
        elif isinstance(value, list) and value:
            for index, item in enumerate(value):
                visit(item, path + "/" + str(index))
        else:
            add(
                path,
                "technical",
                path + " | " + json.dumps(value, ensure_ascii=False, sort_keys=True),
            )

    visit(payload, "ReportResult")
    if len(blocks) > 6000 or sum(len(b.text) for b in blocks) > 1_500_000:
        raise InvalidReport("report exceeds bounded renderer size")
    return blocks


def manifest(payload):
    return {
        "report_id": payload["report_id"],
        "report_version": payload["report_version"],
        "render_version": payload["render_version"],
        "report_hash": semantic_hash(payload),
        "blocks": [
            {"key": b.key, "kind": b.kind, "text_hash": semantic_hash(b.text)}
            for b in document(payload)
        ],
    }
