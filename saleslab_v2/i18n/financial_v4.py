"""Pinned explicit profitability inputs; generic EBITDA remains unverified."""

from copy import deepcopy

from .catalog import TerminologyRegistry, _entry
from .financial_v3 import FINANCIAL_TERMS_V3

FINANCIAL_TERMS_V4 = (
    *deepcopy(FINANCIAL_TERMS_V3),
    _entry(
        "cost_of_sales",
        "income_statement",
        "monetary_flow",
        "debit",
        ("custo das vendas",),
        ("cost of sales",),
        ("coste de ventas",),
        ("coût des ventes",),
    ),
    _entry(
        "depreciation_and_amortization",
        "income_statement",
        "monetary_flow",
        "debit",
        ("depreciações e amortizações",),
        ("depreciation and amortization",),
        ("depreciaciones y amortizaciones",),
        ("dépréciations et amortissements",),
    ),
    _entry(
        "ebitda_adjustments",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("ajustes líquidos explícitos ao EBITDA",),
        ("explicit net EBITDA adjustments",),
        ("ajustes netos explícitos al EBITDA",),
        ("ajustements nets explicites de l’EBITDA",),
    ),
    _entry(
        "reported_statutory_ebitda",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("EBITDA estatutário reportado (EBIT + D&A)",),
        ("reported statutory EBITDA (EBIT + D&A)",),
        ("EBITDA estatutario publicado (EBIT + D&A)",),
        ("EBITDA statutaire publié (EBIT + D&A)",),
    ),
    _entry(
        "reported_adjusted_ebitda",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("EBITDA ajustado reportado com ponte explícita",),
        ("reported adjusted EBITDA with explicit bridge",),
        ("EBITDA ajustado publicado con puente explícito",),
        ("EBITDA ajusté publié avec rapprochement explicite",),
    ),
    _entry(
        "other_operating_income_expense",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("outros rendimentos e gastos operacionais líquidos",),
        ("net other operating income and expense",),
        ("otros ingresos y gastos operativos netos",),
        ("autres produits et charges opérationnels nets",),
    ),
    _entry(
        "net_finance_cost",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("gasto financeiro líquido",),
        ("net finance cost",),
        ("gasto financiero neto",),
        ("coût financier net",),
    ),
    _entry(
        "income_tax",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("imposto sobre o rendimento",),
        ("income tax",),
        ("impuesto sobre beneficios",),
        ("impôt sur le résultat",),
    ),
)


class FinancialTerminologyV4(TerminologyRegistry):
    version = "financial-terminology-v4"

    def __init__(self):
        super().__init__(FINANCIAL_TERMS_V4)
