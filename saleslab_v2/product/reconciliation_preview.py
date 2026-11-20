"""Project executed reconciliation evidence without resolving or recalculating inputs."""

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.i18n.financial_v7 import DEBT_LABELS
from saleslab_v2.i18n.financial_v8 import CAPEX_LABELS
from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1
from saleslab_v2.reports.contracts import REPORT_LOCALES
from saleslab_v2.reports.presentation import format_value

LABELS = {
    **DEBT_LABELS,
    **CAPEX_LABELS,
    "lfl": (
        "Contributo monetário LFL",
        "Monetary LFL contribution",
        "Contribución monetaria comparable",
        "Contribution monétaire comparable",
    ),
    "expansion_acquisition": (
        "Contributo de expansão/aquisições",
        "Expansion/acquisition contribution",
        "Contribución de expansión/adquisiciones",
        "Contribution d’expansion/acquisitions",
    ),
    "fx": (
        "Contributo cambial",
        "FX contribution",
        "Contribución de divisas",
        "Contribution de change",
    ),
    "other": (
        "Outros contributos",
        "Other contributions",
        "Otras contribuciones",
        "Autres contributions",
    ),
    "reported": (
        "Variação da receita reportada",
        "Reported revenue change",
        "Variación de ingresos publicados",
        "Variation du chiffre d’affaires publié",
    ),
    "opening": ("Saldo inicial", "Opening balance", "Saldo inicial", "Solde initial"),
    "closing": ("Saldo final", "Closing balance", "Saldo final", "Solde final"),
    "drawdowns": (
        "Novos empréstimos",
        "Debt drawdowns",
        "Disposiciones de deuda",
        "Tirages de dette",
    ),
    "repayments": (
        "Reembolsos de empréstimos",
        "Debt repayments",
        "Reembolsos de deuda",
        "Remboursements de dette",
    ),
    "additions": (
        "Adições de leasing",
        "Lease additions",
        "Altas de arrendamientos",
        "Nouvelles locations",
    ),
    "modifications": (
        "Modificações líquidas de leasing",
        "Net lease modifications",
        "Modificaciones netas de arrendamientos",
        "Modifications nettes de locations",
    ),
    "interest": (
        "Juros de leasing",
        "Lease interest",
        "Intereses de arrendamientos",
        "Intérêts locatifs",
    ),
    "payments": (
        "Pagamentos de leasing",
        "Lease payments",
        "Pagos de arrendamientos",
        "Paiements locatifs",
    ),
    "costs": (
        "Custos operacionais",
        "Operating costs",
        "Costes operativos",
        "Charges d’exploitation",
    ),
    "other_operating": (
        "Outros resultados operacionais líquidos",
        "Net other operating income/expense",
        "Otros resultados operativos netos",
        "Autres résultats opérationnels nets",
    ),
    "ebit": ("EBIT da fonte", "Source EBIT", "EBIT de la fuente", "EBIT de la source"),
    "finance": (
        "Gasto financeiro líquido",
        "Net finance cost",
        "Gasto financiero neto",
        "Coût financier net",
    ),
    "tax": (
        "Imposto sobre o rendimento",
        "Income tax",
        "Impuesto sobre beneficios",
        "Impôt sur le résultat",
    ),
    "net_income": ("Resultado líquido", "Net income", "Resultado neto", "Résultat net"),
    "da": (
        "Depreciações e amortizações",
        "Depreciation and amortization",
        "Depreciaciones y amortizaciones",
        "Dépréciations et amortissements",
    ),
    "statutory_ebitda": (
        "EBITDA estatutário reportado",
        "Reported statutory EBITDA",
        "EBITDA estatutario publicado",
        "EBITDA statutaire publié",
    ),
    "adjustments": (
        "Ajustes líquidos EBITDA",
        "Net EBITDA adjustments",
        "Ajustes netos EBITDA",
        "Ajustements nets EBITDA",
    ),
    "adjusted_ebitda": (
        "EBITDA ajustado reportado",
        "Reported adjusted EBITDA",
        "EBITDA ajustado publicado",
        "EBITDA ajusté publié",
    ),
    "revenue": ("Receita", "Revenue", "Ingresos", "Chiffre d’affaires"),
    "assets": ("Ativo total", "Total assets", "Activo total", "Total de l’actif"),
    "liabilities": (
        "Passivo total",
        "Total liabilities",
        "Pasivo total",
        "Total du passif",
    ),
    "equity": ("Capital próprio", "Equity", "Patrimonio neto", "Capitaux propres"),
    "opening_cash": (
        "Caixa inicial",
        "Opening cash",
        "Efectivo inicial",
        "Trésorerie initiale",
    ),
    "closing_cash": (
        "Caixa final",
        "Closing cash",
        "Efectivo final",
        "Trésorerie finale",
    ),
    "cfo": (
        "Fluxo operacional",
        "Operating cash flow",
        "Flujo operativo",
        "Flux opérationnel",
    ),
    "cfi": (
        "Fluxo de investimento",
        "Investing cash flow",
        "Flujo de inversión",
        "Flux d’investissement",
    ),
    "cff": (
        "Fluxo de financiamento",
        "Financing cash flow",
        "Flujo de financiación",
        "Flux de financement",
    ),
    "fx_other": (
        "Efeitos cambiais e outros movimentos",
        "FX and other movements",
        "Cambio y otros movimientos",
        "Change et autres mouvements",
    ),
}


def reconciliation_evidence(result, original_rows, language):
    raw = unpack(canonicalize(result.payload()))
    rule = FINANCIAL_RULES_V1.get(result.rule_id, result.rule_version)
    originals = {row["location"]: row for row in original_rows}
    locale = REPORT_LOCALES[language]
    index = ("pt", "en", "es", "fr").index(language)

    def display(value):
        return format_value(
            {"value": value, "unit": result.unit, "currency": result.currency}, locale
        )

    inputs = [
        {
            **operand,
            "label": (
                LABELS[operand["role"]][index]
                if operand["role"] in LABELS
                else operand["role"]
            ),
            "display_value": format_value(operand, locale),
            "sources": [
                dict(originals[ref])
                for ref in operand["source_references"]
                if ref in originals
            ],
        }
        for operand in raw["input_references"]
    ]
    equations = []
    definitions = {e.equation_id: e for e in rule.equations}
    for equation in raw["equations"]:
        definition = definitions[equation["equation_id"]]
        terms = [
            (
                role
                if coefficient == 1
                else f"-{role}" if coefficient == -1 else f"{coefficient}*{role}"
            )
            for role, coefficient in definition.expected_terms
        ]
        equations.append(
            {
                **equation,
                "expected_terms": [
                    {"role": role, "coefficient": coefficient}
                    for role, coefficient in definition.expected_terms
                ],
                "observed_role": definition.observed_role,
                "canonical_expression": (
                    "+".join(terms).replace("+-", "-") + "=" + definition.observed_role
                ),
                **{
                    f"display_{key}": display(equation[key])
                    for key in (
                        "expected",
                        "observed",
                        "signed_delta",
                        "absolute_delta",
                    )
                },
            }
        )
    return {
        "rule_version": raw["rule_version"],
        "engine_version": raw["engine_version"],
        "policy": {
            **raw["policy_payload"],
            "display_rounding_absolute": display(
                raw["policy_payload"]["rounding_absolute"]
            ),
        },
        "policy_fingerprint": raw["policy_fingerprint"],
        "input_fingerprint": raw["input_fingerprint"],
        "equations": equations,
        "inputs": inputs,
    }
