"""Synthetic statements only; shared by unit and live database pipeline tests."""

from dataclasses import replace
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from saleslab_v2.domain.enums import Capability, LanguageCode, SourceFormat, TrustLevel
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.i18n.catalog import FINANCIAL_TERMS
from saleslab_v2.normalization.financial import FinancialRow, SourceMetadata
from saleslab_v2.pipeline.contracts import (
    FinancialRequest,
    InputRef,
    MetricStep,
    ReconciliationStep,
    SourceBatch,
)
from saleslab_v2.reconciliation.contracts import TolerancePolicy

WORKSPACE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def request(
    workspace_id=WORKSPACE,
    user_id=USER,
    *,
    language="en",
    scale="raw",
    key="pipeline-test"
):
    lang = LanguageCode(language)
    locale = {
        "pt": LocaleTag.PT_PT,
        "en": LocaleTag.EN_GB,
        "es": LocaleTag.ES_ES,
        "fr": LocaleTag.FR_FR,
    }[language]
    source_id = uuid5(NAMESPACE_URL, "pipeline-source:" + str(workspace_id))
    document = SourceDocument(
        workspace_id,
        "synthetic.csv",
        SourceFormat.CSV,
        "a" * 64,
        "fixture-v1",
        TrustLevel.UNTRUSTED,
        (lang,),
        source_id=source_id,
    )
    metadata = SourceMetadata(
        source_id, document.content_hash, lang, locale, "EUR", scale
    )
    labels = {t.concept_id: t.labels[lang] for t in FINANCIAL_TERMS}
    rows = tuple(
        FinancialRow(
            labels[concept],
            value,
            "2026",
            statement,
            "consolidated",
            source_reference=ref,
        )
        for concept, value, statement, ref in (
            ("total_assets", "100", "balance_sheet", "assets"),
            ("total_liabilities", "60", "balance_sheet", "liabilities"),
            ("equity", "40", "balance_sheet", "equity"),
            ("revenue", "1000", "income_statement", "revenue"),
            ("net_income", "100", "income_statement", "net_income"),
        )
    )
    metric = MetricStep(
        "margin",
        "net_margin_pct",
        (
            ("net_income", InputRef("net_income", source_id)),
            ("revenue", InputRef("revenue", source_id)),
        ),
    )
    policy = TolerancePolicy(
        "pipeline-test",
        "1",
        "monetary",
        "EUR",
        "raw",
        Decimal("0.01"),
        Decimal("0.1"),
        Decimal("1"),
        Decimal("0.01"),
        Decimal("0.05"),
        "synthetic base-currency values",
    )
    reconciliation = ReconciliationStep(
        "REC-BS-001",
        tuple(
            (role, InputRef(role, source_id))
            for role in ("assets", "liabilities", "equity")
        ),
        policy,
    )
    return FinancialRequest(
        workspace_id,
        user_id,
        key,
        LanguageContext((lang,), LanguageCode.EN, LanguageCode.PT, locale),
        (SourceBatch(document, metadata, rows),),
        (metric,),
        (reconciliation,),
        (Capability.CALCULATE,),
    )


def change_row(value, reference, **changes):
    batch = value.sources[0]
    rows = tuple(
        replace(row, **changes) if row.source_reference == reference else row
        for row in batch.rows
    )
    return replace(value, sources=(replace(batch, rows=rows),))


def with_growth(value):
    batch = value.sources[0]
    current = next(r for r in batch.rows if r.source_reference == "revenue")
    prior = replace(
        current,
        text="800",
        period_label="2025",
        comparison_basis="comparative",
        source_reference="revenue_prior",
    )
    sid = batch.document.source_id
    revenue = MetricStep(
        "current_revenue", "revenue", (("reported_revenue", InputRef("revenue", sid)),)
    )
    growth = MetricStep(
        "growth",
        "revenue_growth_reported_pct",
        (
            ("revenue_current", InputRef("current_revenue")),
            ("revenue_prior", InputRef("revenue_prior", sid)),
        ),
    )
    return replace(
        value,
        sources=(replace(batch, rows=(*batch.rows, prior)),),
        metrics=(*value.metrics, growth, revenue),
        requested_capabilities=(Capability.CALCULATE, Capability.COMPARE),
    )
