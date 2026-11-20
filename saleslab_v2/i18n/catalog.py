from dataclasses import dataclass
from decimal import Decimal
import unicodedata

from saleslab_v2.domain.enums import LanguageCode


class MissingCriticalTranslation(LookupError):
    pass


class TranslationCatalog:
    def __init__(
        self,
        entries: dict[str, dict[LanguageCode, str]] | None = None,
        *,
        strict: bool = True,
    ):
        self.entries = entries or {}
        self.strict = strict

    def resolve(
        self, concept_id: str, language: LanguageCode, *, critical: bool = False
    ) -> str | None:
        value = self.entries.get(concept_id, {}).get(language)
        if value is None and critical and self.strict:
            raise MissingCriticalTranslation(
                f"missing critical translation: {concept_id}/{language.value}"
            )
        return value


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold().strip())
    return " ".join(
        "".join(c for c in normalized if not unicodedata.combining(c)).split()
    )


@dataclass(frozen=True)
class TerminologyEntry:
    concept_id: str
    canonical_label: str
    labels: dict[LanguageCode, str]
    aliases: dict[LanguageCode, tuple[str, ...]]
    statement_type: str
    semantic_type: str
    expected_sign: str
    confidence: Decimal = Decimal("1")
    reason_code: str = "mapping.alias_exact"


def _entry(
    concept_id,
    statement,
    semantic,
    sign,
    pt,
    en,
    es,
    fr,
    *,
    confidence=Decimal("1"),
    reason_code="mapping.alias_exact",
):
    labels = {
        LanguageCode.PT: pt[0],
        LanguageCode.EN: en[0],
        LanguageCode.ES: es[0],
        LanguageCode.FR: fr[0],
    }
    aliases = {
        LanguageCode.PT: tuple(pt),
        LanguageCode.EN: tuple(en),
        LanguageCode.ES: tuple(es),
        LanguageCode.FR: tuple(fr),
    }
    return TerminologyEntry(
        concept_id,
        en[0],
        labels,
        aliases,
        statement,
        semantic,
        sign,
        confidence,
        reason_code,
    )


FINANCIAL_TERMS = (
    _entry(
        "revenue",
        "income_statement",
        "monetary_flow",
        "credit",
        ("receitas", "vendas"),
        ("revenue", "sales"),
        ("ingresos", "ventas"),
        ("chiffre d’affaires", "ventes"),
    ),
    _entry(
        "operating_costs",
        "income_statement",
        "monetary_flow",
        "debit",
        ("custos operacionais",),
        ("operating costs",),
        ("costes operativos",),
        ("charges d’exploitation",),
    ),
    _entry(
        "ebit",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("EBIT",),
        ("EBIT",),
        ("EBIT",),
        ("EBIT",),
    ),
    _entry(
        "ebitda_reported_unverified",
        "income_statement",
        "reported_unverified",
        "contextual",
        ("EBITDA",),
        ("EBITDA",),
        ("EBITDA",),
        ("EBITDA",),
        confidence=Decimal("0.7"),
        reason_code="mapping.ebitda_requires_confirmation",
    ),
    _entry(
        "operating_profit_reported_unconfirmed",
        "income_statement",
        "reported_operating_profit_unconfirmed",
        "contextual",
        ("resultado operacional",),
        ("operating profit",),
        ("resultado operativo",),
        ("résultat opérationnel",),
        confidence=Decimal("0.7"),
        reason_code="mapping.operating_profit_requires_confirmation",
    ),
    _entry(
        "net_income",
        "income_statement",
        "monetary_flow",
        "contextual",
        ("resultado líquido",),
        ("net income",),
        ("resultado neto",),
        ("résultat net",),
    ),
    _entry(
        "cash_and_equivalents",
        "balance_sheet",
        "monetary_stock",
        "debit",
        ("caixa e equivalentes",),
        ("cash and cash equivalents",),
        ("efectivo y equivalentes",),
        ("trésorerie et équivalents",),
    ),
    _entry(
        "trade_receivables",
        "balance_sheet",
        "monetary_stock",
        "debit",
        ("clientes", "contas a receber"),
        ("trade receivables",),
        ("cuentas por cobrar",),
        ("créances clients",),
    ),
    _entry(
        "inventory",
        "balance_sheet",
        "monetary_stock",
        "debit",
        ("inventários",),
        ("inventory",),
        ("inventarios",),
        ("stocks",),
    ),
    _entry(
        "trade_payables",
        "balance_sheet",
        "monetary_stock",
        "credit",
        ("fornecedores",),
        ("trade payables",),
        ("proveedores",),
        ("dettes fournisseurs",),
    ),
    _entry(
        "total_assets",
        "balance_sheet",
        "monetary_stock",
        "debit",
        ("total do ativo",),
        ("total assets",),
        ("total activos",),
        ("total de l’actif",),
    ),
    _entry(
        "total_liabilities",
        "balance_sheet",
        "monetary_stock",
        "credit",
        ("total do passivo",),
        ("total liabilities",),
        ("total pasivos",),
        ("total du passif",),
    ),
    _entry(
        "equity",
        "balance_sheet",
        "monetary_stock",
        "credit",
        ("capital próprio",),
        ("equity",),
        ("patrimonio neto",),
        ("capitaux propres",),
    ),
    _entry(
        "cfo",
        "cash_flow_statement",
        "monetary_flow",
        "contextual",
        ("fluxo de caixa operacional",),
        ("cash flow from operations",),
        ("flujo de efectivo operativo",),
        ("flux de trésorerie opérationnel",),
    ),
    _entry(
        "cfi",
        "cash_flow_statement",
        "monetary_flow",
        "contextual",
        ("fluxo de caixa de investimento",),
        ("cash flow from investing",),
        ("flujo de efectivo de inversión",),
        ("flux de trésorerie d’investissement",),
    ),
    _entry(
        "cff",
        "cash_flow_statement",
        "monetary_flow",
        "contextual",
        ("fluxo de caixa de financiamento",),
        ("cash flow from financing",),
        ("flujo de efectivo de financiación",),
        ("flux de trésorerie de financement",),
    ),
    _entry(
        "capex",
        "cash_flow_statement",
        "monetary_flow",
        "outflow",
        ("aquisições de ativos fixos",),
        ("purchases of property plant and equipment",),
        ("adquisiciones de inmovilizado",),
        ("acquisitions d’immobilisations",),
    ),
)


class TerminologyRegistry:
    """Immutable versioned aliases; canonical IDs are never translated."""

    version = "financial-terminology-v1"

    def __init__(self, entries=FINANCIAL_TERMS):
        self.entries = tuple(entries)
        required_languages = set(LanguageCode)
        index = {}
        unconstrained = {}
        for entry in self.entries:
            if (
                set(entry.labels) != required_languages
                or set(entry.aliases) != required_languages
            ):
                raise ValueError(
                    f"terminology languages incomplete: {entry.concept_id}"
                )
            if (
                not Decimal("0") <= entry.confidence <= Decimal("1")
                or not entry.reason_code
            ):
                raise ValueError(f"invalid terminology metadata: {entry.concept_id}")
            for language, aliases in entry.aliases.items():
                if not aliases:
                    raise ValueError(
                        f"terminology aliases missing: {entry.concept_id}/{language.value}"
                    )
                for alias in aliases:
                    key = (language, _key(alias), entry.statement_type)
                    if key in index and index[key].concept_id != entry.concept_id:
                        raise ValueError(
                            f"ambiguous terminology alias: {language.value}/{alias}/{entry.statement_type}"
                        )
                    if key in index and index[key] != entry:
                        raise ValueError(
                            "conflicting terminology alias metadata: "
                            f"{language.value}/{alias}/{entry.statement_type}/{entry.concept_id}"
                        )
                    index[key] = entry
                    loose_key = (language, _key(alias))
                    unconstrained.setdefault(loose_key, []).append(entry)
        self._index = index
        self._unconstrained = unconstrained

    def lookup(
        self, label: str, language: LanguageCode, statement_type: str | None = None
    ) -> TerminologyEntry | None:
        if statement_type:
            return self._index.get((language, _key(label), statement_type))
        candidates = self._unconstrained.get((language, _key(label)), ())
        unique = {entry.concept_id: entry for entry in candidates}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def presentation_label(self, concept_id: str, language: LanguageCode) -> str | None:
        return next(
            (
                entry.labels.get(language)
                for entry in self.entries
                if entry.concept_id == concept_id
            ),
            None,
        )
