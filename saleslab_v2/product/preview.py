"""Read-only projection of deterministic calculations; never a published report."""

from dataclasses import asdict

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.reports.contracts import REPORT_LOCALES
from saleslab_v2.reports.presentation import format_value

from .working_capital_labels import WORKING_CAPITAL_METRIC_LABELS

LABELS = {
    **WORKING_CAPITAL_METRIC_LABELS,
    "roic": (
        "ROIC do período · não anualizado",
        "Period ROIC · not annualized",
        "ROIC del período · no anualizado",
        "ROIC de la période · non annualisé",
    ),
    "capex_cash_total": (
        "Total confirmado de aquisições de ativos fixos pagas",
        "Confirmed total cash purchases of fixed assets",
        "Total confirmado de adquisiciones pagadas de inmovilizado",
        "Total confirmé des acquisitions d’immobilisations payées",
    ),
    "capex_cash_maintenance": (
        "Aquisições de ativos fixos pagas para manutenção",
        "Maintenance cash purchases of fixed assets",
        "Adquisiciones pagadas de inmovilizado para mantenimiento",
        "Acquisitions d’immobilisations payées pour maintien",
    ),
    "capex_cash_growth": (
        "Aquisições de ativos fixos pagas para crescimento",
        "Growth cash purchases of fixed assets",
        "Adquisiciones pagadas de inmovilizado para crecimiento",
        "Acquisitions d’immobilisations payées pour croissance",
    ),
    "capex_commitments_outstanding": (
        "Compromissos de aquisição de ativos fixos por executar · não são pagamentos",
        "Outstanding fixed-asset purchase commitments · not cash already paid",
        "Compromisos pendientes de adquisición de inmovilizado · no son pagos",
        "Engagements d’acquisition d’immobilisations restant à exécuter · pas des paiements",
    ),
    "capex_maintenance_share_pct": (
        "Parcela de manutenção no capex pago confirmado",
        "Maintenance share of confirmed cash capex",
        "Proporción de mantenimiento del capex pagado confirmado",
        "Part du maintien dans le capex payé confirmé",
    ),
    "capex_growth_share_pct": (
        "Parcela de crescimento no capex pago confirmado",
        "Growth share of confirmed cash capex",
        "Proporción de crecimiento del capex pagado confirmado",
        "Part de croissance dans le capex payé confirmé",
    ),
    "fixed_variable_mix": (
        "Dívida a taxa fixa antes de hedge · % do principal elegível sem leasing",
        "Fixed-rate debt before hedging · % of eligible principal excluding leases",
        "Deuda a tipo fijo antes de cobertura · % del principal elegible sin arrendamientos",
        "Dette à taux fixe avant couverture · % du principal éligible hors locations",
    ),
    "variable_rate_share_pct": (
        "Dívida a taxa variável antes de hedge · % do principal elegível sem leasing",
        "Variable-rate debt before hedging · % of eligible principal excluding leases",
        "Deuda a tipo variable antes de cobertura · % del principal elegible sin arrendamientos",
        "Dette à taux variable avant couverture · % du principal éligible hors locations",
    ),
    "maturity_concentration": (
        "Dívida vencível nos doze meses após a data de reporte · % do principal elegível sem leasing",
        "Debt due within twelve months after the reporting date · % of eligible principal excluding leases",
        "Deuda con vencimiento en los doce meses tras la fecha de reporte · % del principal elegible sin arrendamientos",
        "Dette exigible dans les douze mois après la clôture · % du principal éligible hors locations",
    ),
    "revenue_growth_cc_pct": (
        "crescimento calculado a câmbio constante",
        "Calculated constant currency growth",
        "Crecimiento calculado a cambio constante",
        "Croissance calculée à change constant",
    ),
    "revenue_growth_lfl_pct": (
        "crescimento calculado like-for-like",
        "Calculated like-for-like growth",
        "Crecimiento comparable calculado",
        "Croissance comparable calculée",
    ),
    "revenue_change_reported": (
        "variação monetária da receita reportada",
        "Monetary reported revenue change",
        "Variación monetaria de ingresos publicados",
        "Variation monétaire du chiffre d’affaires publié",
    ),
    "growth_lfl_contribution": (
        "contributo monetário LFL na ponte reportada",
        "Monetary LFL contribution in the reported bridge",
        "Contribución monetaria comparable en el puente publicado",
        "Contribution monétaire comparable dans le rapprochement publié",
    ),
    "growth_expansion_acquisition_contribution": (
        "contributo monetário de expansão e aquisições",
        "Monetary expansion/acquisition contribution",
        "Contribución monetaria de expansión y adquisiciones",
        "Contribution monétaire d’expansion et acquisitions",
    ),
    "growth_fx_contribution": (
        "contributo monetário cambial",
        "Monetary FX contribution",
        "Contribución monetaria de divisas",
        "Contribution monétaire de change",
    ),
    "growth_other_contribution": (
        "outros contributos monetários",
        "Other monetary contributions",
        "Otras contribuciones monetarias",
        "Autres contributions monétaires",
    ),
    "cash_and_cash_equivalents": (
        "caixa e equivalentes elegíveis sem restrições",
        "eligible unrestricted cash and equivalents",
        "efectivo y equivalentes elegibles sin restricciones",
        "trésorerie et équivalents éligibles non restreints",
    ),
    "current_operating_assets": (
        "ativos operacionais correntes excluindo caixa e ativos não operacionais",
        "current operating assets excluding cash and non-operating assets",
        "activos operativos corrientes sin efectivo ni activos no operativos",
        "actifs opérationnels courants hors trésorerie et actifs non opérationnels",
    ),
    "current_operating_liabilities": (
        "passivos operacionais correntes excluindo dívida e leasing",
        "current operating liabilities excluding debt and leases",
        "pasivos operativos corrientes sin deuda ni arrendamientos",
        "passifs opérationnels courants hors dette et locations",
    ),
    "average_trade_receivables": (
        "saldo médio explícito de clientes no período",
        "explicit average trade receivables for the period",
        "saldo medio explícito de clientes del período",
        "créances clients moyennes explicites de la période",
    ),
    "average_inventory": (
        "saldo médio explícito de inventários no período",
        "explicit average inventory for the period",
        "saldo medio explícito de inventario del período",
        "stocks moyens explicites de la période",
    ),
    "average_trade_payables": (
        "saldo médio explícito de fornecedores no período",
        "explicit average trade payables for the period",
        "saldo medio explícito de proveedores del período",
        "dettes fournisseurs moyennes explicites de la période",
    ),
    "purchases": (
        "compras explícitas do período para prazo de fornecedores",
        "explicit period purchases for payable days",
        "compras explícitas del período para plazo de proveedores",
        "achats explicites de la période pour délai fournisseurs",
    ),
    "interest_expense": (
        "gastos brutos de juros explícitos",
        "explicit gross interest expense",
        "gasto bruto explícito de intereses",
        "charges brutes d’intérêts explicites",
    ),
    "lease_liabilities": (
        "passivos de leasing",
        "lease liabilities",
        "pasivos por arrendamientos",
        "passifs locatifs",
    ),
    "debt_drawdowns": (
        "novos empréstimos em montante positivo",
        "debt drawdowns as positive amounts",
        "disposiciones de deuda en importes positivos",
        "tirages de dette en montants positifs",
    ),
    "debt_repayments": (
        "reembolsos de empréstimos em montante positivo",
        "debt repayments as positive amounts",
        "reembolsos de deuda en importes positivos",
        "remboursements de dette en montants positifs",
    ),
    "debt_fx_other": (
        "efeitos cambiais e outros movimentos explícitos da dívida",
        "explicit debt foreign exchange and other movements",
        "efectos cambiarios y otros movimientos explícitos de deuda",
        "effets de change et autres mouvements explicites de dette",
    ),
    "lease_additions": (
        "adições de leasing em montante positivo",
        "lease additions as positive amounts",
        "altas de arrendamientos en importes positivos",
        "nouveaux contrats de location en montants positifs",
    ),
    "lease_modifications": (
        "modificações líquidas explícitas de leasing",
        "explicit net lease modifications",
        "modificaciones netas explícitas de arrendamientos",
        "modifications nettes explicites de locations",
    ),
    "lease_interest": (
        "juros de leasing em montante positivo",
        "lease interest as positive amounts",
        "intereses de arrendamientos en importes positivos",
        "intérêts locatifs en montants positifs",
    ),
    "lease_payments": (
        "pagamentos de leasing em montante positivo",
        "lease payments as positive amounts",
        "pagos de arrendamientos en importes positivos",
        "paiements locatifs en montants positifs",
    ),
    "lease_fx_other": (
        "efeitos cambiais e outros movimentos explícitos de leasing",
        "explicit lease foreign exchange and other movements",
        "efectos cambiarios y otros movimientos explícitos de arrendamientos",
        "effets de change et autres mouvements explicites de locations",
    ),
    "gross_debt": (
        "Dívida bruta sem leasing",
        "Gross debt excluding leases",
        "Deuda bruta sin arrendamientos",
        "Dette brute hors locations",
    ),
    "opening_gross_debt": (
        "Dívida bruta inicial sem leasing",
        "Opening gross debt excluding leases",
        "Deuda bruta inicial sin arrendamientos",
        "Dette brute initiale hors locations",
    ),
    "opening_lease_liabilities": (
        "Passivos iniciais de leasing",
        "Opening lease liabilities",
        "Pasivos iniciales de arrendamientos",
        "Passifs locatifs initiaux",
    ),
    "quick_assets": (
        "Ativos elegíveis de liquidez reduzida calculados",
        "Calculated eligible quick assets",
        "Activos elegibles de liquidez reducida calculados",
        "Actifs éligibles de liquidité réduite calculés",
    ),
    "quick_ratio": (
        "Liquidez reduzida · componentes explícitos",
        "Quick ratio · explicit components",
        "Liquidez reducida · componentes explícitos",
        "Liquidité réduite · composantes explicites",
    ),
    "net_working_capital": (
        "Fundo de maneio operacional · sem caixa/dívida",
        "Operating working capital · excluding cash/debt",
        "Capital circulante operativo · sin efectivo/deuda",
        "Fonds de roulement opérationnel · hors trésorerie/dette",
    ),
    "net_debt": (
        "Dívida líquida sem leasing",
        "Net debt excluding leases",
        "Deuda neta sin arrendamientos",
        "Dette nette hors locations",
    ),
    "net_debt_incl_leases": (
        "Dívida líquida com leasing",
        "Net debt including leases",
        "Deuda neta con arrendamientos",
        "Dette nette avec locations",
    ),
    "days_in_period": (
        "Dias reais do período",
        "Actual calendar days in period",
        "Días reales del período",
        "Jours calendaires réels de la période",
    ),
    "dso": (
        "Prazo médio de recebimento · saldo médio explícito",
        "Receivable days · explicit average balance",
        "Plazo de cobro · saldo medio explícito",
        "Délai clients · solde moyen explicite",
    ),
    "dio": (
        "Prazo de inventários · saldo médio explícito",
        "Inventory days · explicit average balance",
        "Plazo de inventario · saldo medio explícito",
        "Délai des stocks · solde moyen explicite",
    ),
    "dpo": (
        "Prazo médio de pagamento · compras explícitas",
        "Payable days · explicit purchases",
        "Plazo de pago · compras explícitas",
        "Délai fournisseurs · achats explicites",
    ),
    "cash_conversion_cycle": (
        "Ciclo de conversão de caixa",
        "Cash conversion cycle",
        "Ciclo de conversión de efectivo",
        "Cycle de conversion de trésorerie",
    ),
    "net_debt_to_ebitda": (
        "Dívida líquida / EBITDA do período",
        "Net debt / period EBITDA",
        "Deuda neta / EBITDA del período",
        "Dette nette / EBITDA de la période",
    ),
    "interest_coverage": (
        "Cobertura de juros brutos",
        "Gross interest coverage",
        "Cobertura de intereses brutos",
        "Couverture des intérêts bruts",
    ),
    "days_for_dso": (
        "Dias reais do período · DSO",
        "Actual calendar days in period · DSO",
        "Días reales del período · DSO",
        "Jours calendaires réels de la période · DSO",
    ),
    "days_for_dio": (
        "Dias reais do período · DIO",
        "Actual calendar days in period · DIO",
        "Días reales del período · DIO",
        "Jours calendaires réels de la période · DIO",
    ),
    "days_for_dpo": (
        "Dias reais do período · DPO",
        "Actual calendar days in period · DPO",
        "Días reales del período · DPO",
        "Jours calendaires réels de la période · DPO",
    ),
    "net_debt_to_ebitda_statutory": (
        "Dívida líquida sem leasing / EBITDA estatutário do período",
        "Net debt excluding leases / EBITDA statutory for the period",
        "Deuda neta sin arrendamientos / EBITDA estatutario del período",
        "Dette nette hors locations / EBITDA statutaire de la période",
    ),
    "net_debt_to_ebitda_adjusted": (
        "Dívida líquida sem leasing / EBITDA ajustado do período",
        "Net debt excluding leases / EBITDA adjusted for the period",
        "Deuda neta sin arrendamientos / EBITDA ajustado del período",
        "Dette nette hors locations / EBITDA ajusté de la période",
    ),
    "net_debt_incl_leases_to_ebitda_statutory": (
        "Dívida líquida com leasing / EBITDA estatutário do período",
        "Net debt including leases / EBITDA statutory for the period",
        "Deuda neta con arrendamientos / EBITDA estatutario del período",
        "Dette nette avec locations / EBITDA statutaire de la période",
    ),
    "net_debt_incl_leases_to_ebitda_adjusted": (
        "Dívida líquida com leasing / EBITDA ajustado do período",
        "Net debt including leases / EBITDA adjusted for the period",
        "Deuda neta con arrendamientos / EBITDA ajustado del período",
        "Dette nette avec locations / EBITDA ajusté de la période",
    ),
    "interest_coverage_ebit_confirmed": (
        "Cobertura de juros brutos · EBIT",
        "Gross interest coverage · EBIT",
        "Cobertura de intereses brutos · EBIT",
        "Couverture des intérêts bruts · EBIT",
    ),
    "interest_coverage_ebitda_statutory": (
        "Cobertura de juros brutos · EBITDA estatutário",
        "Gross interest coverage · statutory EBITDA",
        "Cobertura de intereses brutos · EBITDA estatutario",
        "Couverture des intérêts bruts · EBITDA statutaire",
    ),
    "interest_coverage_ebitda_adjusted": (
        "Cobertura de juros brutos · EBITDA ajustado",
        "Gross interest coverage · adjusted EBITDA",
        "Cobertura de intereses brutos · EBITDA ajustado",
        "Couverture des intérêts bruts · EBITDA ajusté",
    ),
    "cost_of_sales": (
        "Custo das vendas",
        "Cost of sales",
        "Coste de ventas",
        "Coût des ventes",
    ),
    "operating_costs": (
        "Custos operacionais",
        "Operating costs",
        "Costes operativos",
        "Charges d’exploitation",
    ),
    "ebit_confirmed": (
        "EBIT explícito da fonte",
        "Explicit source EBIT",
        "EBIT explícito de la fuente",
        "EBIT explicite de la source",
    ),
    "depreciation_and_amortization": (
        "Depreciações e amortizações",
        "Depreciation and amortization",
        "Depreciaciones y amortizaciones",
        "Dépréciations et amortissements",
    ),
    "ebitda_adjustments": (
        "Ajustes líquidos ao EBITDA",
        "Net EBITDA adjustments",
        "Ajustes netos al EBITDA",
        "Ajustements nets de l’EBITDA",
    ),
    "other_operating_income_expense": (
        "Outros rendimentos/gastos operacionais líquidos",
        "Net other operating income/expense",
        "Otros ingresos/gastos operativos netos",
        "Autres produits/charges opérationnels nets",
    ),
    "net_finance_cost": (
        "Gasto financeiro líquido",
        "Net finance cost",
        "Gasto financiero neto",
        "Coût financier net",
    ),
    "income_tax": (
        "Imposto sobre o rendimento",
        "Income tax",
        "Impuesto sobre beneficios",
        "Impôt sur le résultat",
    ),
    "gross_profit": (
        "Resultado bruto calculado",
        "Calculated gross profit",
        "Resultado bruto calculado",
        "Résultat brut calculé",
    ),
    "gross_margin_pct": ("Margem bruta", "Gross margin", "Margen bruto", "Marge brute"),
    "ebitda_statutory": (
        "EBITDA estatutário calculado",
        "Calculated statutory EBITDA",
        "EBITDA estatutario calculado",
        "EBITDA statutaire calculé",
    ),
    "ebitda_adjusted": (
        "EBITDA ajustado calculado",
        "Calculated adjusted EBITDA",
        "EBITDA ajustado calculado",
        "EBITDA ajusté calculé",
    ),
    "reported_ebitda_statutory": (
        "EBITDA estatutário reportado · EBIT + D&A",
        "Reported statutory EBITDA · EBIT + D&A",
        "EBITDA estatutario publicado · EBIT + D&A",
        "EBITDA statutaire publié · EBIT + D&A",
    ),
    "reported_ebitda_adjusted": (
        "EBITDA ajustado reportado · ponte explícita",
        "Reported adjusted EBITDA · explicit bridge",
        "EBITDA ajustado publicado · puente explícito",
        "EBITDA ajusté publié · rapprochement explicite",
    ),
    "ebitda_margin_pct": (
        "Margem EBITDA",
        "EBITDA margin",
        "Margen EBITDA",
        "Marge EBITDA",
    ),
    "cash_conversion_pct": (
        "Conversão de caixa",
        "Cash conversion",
        "Conversión de efectivo",
        "Conversion de trésorerie",
    ),
    "ebitda_margin_statutory_pct": (
        "Margem EBITDA estatutário calculado",
        "Calculated statutory EBITDA margin",
        "Margen EBITDA estatutario calculado",
        "Marge EBITDA statutaire calculé",
    ),
    "ebitda_margin_adjusted_pct": (
        "Margem EBITDA ajustado calculado",
        "Calculated adjusted EBITDA margin",
        "Margen EBITDA ajustado calculado",
        "Marge EBITDA ajusté calculé",
    ),
    "cash_conversion_statutory_pct": (
        "Conversão CFO / EBITDA estatutário calculado",
        "CFO / calculated statutory EBITDA conversion",
        "Conversión CFO / EBITDA estatutario calculado",
        "Conversion CFO / EBITDA statutaire calculé",
    ),
    "cash_conversion_adjusted_pct": (
        "Conversão CFO / EBITDA ajustado calculado",
        "CFO / calculated adjusted EBITDA conversion",
        "Conversión CFO / EBITDA ajustado calculado",
        "Conversion CFO / EBITDA ajusté calculé",
    ),
    "adjustments_to_statutory_ebitda_pct": (
        "Ajustes / EBITDA estatutário calculado",
        "Adjustments / calculated statutory EBITDA",
        "Ajustes / EBITDA estatutario calculado",
        "Ajustements / EBITDA statutaire calculé",
    ),
    "cash_fx_other": (
        "Efeitos cambiais e outros movimentos de caixa",
        "Cash foreign exchange and other movements",
        "Efectos de cambio y otros movimientos de efectivo",
        "Effets de change et autres mouvements de trésorerie",
    ),
    "revenue": ("Receita", "Revenue", "Ingresos", "Chiffre d’affaires"),
    "current_ratio": (
        "Liquidez corrente",
        "Current ratio",
        "Liquidez corriente",
        "Ratio de liquidité générale",
    ),
    "net_margin_pct": ("Margem líquida", "Net margin", "Margen neto", "Marge nette"),
    "ebit_margin_pct": ("Margem EBIT", "EBIT margin", "Margen EBIT", "Marge EBIT"),
    "free_cash_flow": (
        "Fluxo de caixa livre · CFO e ativos fixos",
        "Free cash flow · CFO and fixed assets",
        "Flujo de caja libre · CFO y activos fijos",
        "Flux de trésorerie disponible · CFO et immobilisations",
    ),
    "cfo": (
        "Fluxo de caixa operacional",
        "Operating cash flow",
        "Flujo de caja operativo",
        "Flux de trésorerie opérationnel",
    ),
    "cfi": (
        "Fluxo de caixa de investimento",
        "Investing cash flow",
        "Flujo de efectivo de inversión",
        "Flux de trésorerie d’investissement",
    ),
    "cff": (
        "Fluxo de caixa de financiamento",
        "Financing cash flow",
        "Flujo de efectivo de financiación",
        "Flux de trésorerie de financement",
    ),
    "revenue_growth_reported_pct": (
        "Crescimento reportado da receita",
        "Reported revenue growth",
        "Crecimiento reportado de los ingresos",
        "Croissance publiée du chiffre d’affaires",
    ),
}


def calculation_evidence(trace, original_rows, locale):
    """Project actual trace operands and imported rows without recalculating."""
    originals = {row["location"]: row for row in original_rows}
    payload = unpack(canonicalize(trace.payload))
    inputs = []
    for operand in payload["inputs"]:
        inputs.append(
            {
                **operand,
                "display_value": format_value(operand, locale),
                "sources": [
                    dict(originals[reference])
                    for reference in operand["source_references"]
                    if reference in originals
                ],
            }
        )
    return {
        "canonical_expression": payload["canonical_expression"],
        "metric_definition_version": payload["metric_definition_version"],
        "exact_output": payload["exact_output"],
        "display_exact_output": format_value(
            {
                "value": payload["exact_output"],
                "unit": payload["output_unit"],
                "currency": payload["output_currency"],
            },
            locale,
        ),
        "rounded_output": payload["rounded_output"],
        "rounding_policy": payload["rounding_policy"],
        "rounding_version": payload["rounding_version"],
        "division_policy": payload["division_policy"],
        "trace_hash": trace.trace_hash,
        "inputs": inputs,
    }


def metric_preview(request, evaluated, language, original_rows):
    calculated = {key: (value, trace) for key, value, trace in evaluated.metrics}
    errors = {e["step"]: e["code"] for e in evaluated.payloads[1]["errors"]}
    output = []
    for step in request.metrics:
        row = {
            "key": step.key,
            "metric_id": step.metric_id,
            "label": LABELS.get(step.key, LABELS[step.metric_id])[
                ("pt", "en", "es", "fr").index(language)
            ],
            "status": "unavailable",
            "display_value": None,
            "exact_value": None,
            "period": None,
            "source_references": [],
            "calculation_trace_id": None,
            "calculation_evidence": None,
            "reason_codes": [errors.get(step.key, "pipeline.mapping_required")],
        }
        if step.key in calculated:
            observation, trace = calculated[step.key]
            evidence = unpack(canonicalize(asdict(observation)))
            evidence["trace"] = unpack(canonicalize(asdict(trace)))
            row.update(
                status="calculated",
                display_value=format_value(
                    evidence, REPORT_LOCALES[language], trace_rounding=True
                ),
                exact_value=evidence["value"],
                period=evidence["period"],
                source_references=evidence["source_references"],
                calculation_trace_id=evidence["calculation_trace_id"],
                calculation_evidence=calculation_evidence(
                    trace, original_rows, REPORT_LOCALES[language]
                ),
                reason_codes=[],
            )
        output.append(row)
    return output
