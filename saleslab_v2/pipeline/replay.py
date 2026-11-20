"""Rehydrate the persisted semantic request for independent deterministic audit."""

from decimal import Decimal
from uuid import UUID

from saleslab_v2.domain.enums import Capability, LanguageCode, SourceFormat, TrustLevel
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.normalization.financial import FinancialRow, SourceMetadata
from saleslab_v2.reconciliation.contracts import TolerancePolicy

from .contracts import (
    PIPELINE_VERSION,
    FinancialRequest,
    InputRef,
    MetricStep,
    ReconciliationStep,
    SourceBatch,
)


def _decode(value):
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if len(value) == 1:
        if "$uuid" in value:
            return UUID(value["$uuid"])
        if "$decimal" in value:
            return Decimal(value["$decimal"])
        if "$enum" in value:
            return value["$enum"]
    return {k: _decode(v) for k, v in value.items()}


def restore_request(payload, *, run):
    value = _decode(payload)
    if value["version"] != PIPELINE_VERSION:
        raise ValueError("unsupported persisted pipeline version")
    batches = []
    for batch in value["sources"]:
        document = dict(batch["document"])
        document["source_format"] = SourceFormat(document["source_format"])
        document["trust_level"] = TrustLevel(document["trust_level"])
        document["languages"] = tuple(LanguageCode(x) for x in document["languages"])
        document.update(uploaded_at=run.created_at, created_at=run.created_at)
        metadata = dict(batch["metadata"])
        metadata["source_language"] = LanguageCode(metadata["source_language"])
        metadata["format_locale"] = LocaleTag(metadata["format_locale"])
        rows = [
            FinancialRow(
                **{**r, "dimensions": tuple(tuple(d) for d in r["dimensions"])}
            )
            for r in batch["rows"]
        ]
        batches.append(
            SourceBatch(
                SourceDocument(**document), SourceMetadata(**metadata), tuple(rows)
            )
        )

    def bindings(step):
        return tuple((role, InputRef(**ref)) for role, ref in step["inputs"])

    metrics = tuple(
        MetricStep(step["key"], step["metric_id"], bindings(step), step["version"])
        for step in value["metrics"]
    )
    reconciliations = tuple(
        ReconciliationStep(
            step["rule_id"],
            bindings(step),
            TolerancePolicy(**step["policy"]),
            step["version"],
        )
        for step in value["reconciliations"]
    )
    language = value["language"]
    request = FinancialRequest(
        run.workspace_id,
        run.created_by_user_id,
        run.idempotency_key,
        LanguageContext(
            tuple(LanguageCode(x) for x in language["source_languages"]),
            LanguageCode(language["analysis_language"]),
            LanguageCode(language["report_language"]),
            LocaleTag(run.language_context["format_locale"]),
            language["accounting_context"],
        ),
        tuple(batches),
        metrics,
        reconciliations,
        tuple(Capability(c) for c in value["capabilities"]),
        value["profile"],
        (
            tuple(value["analyst_metric_keys"])
            if value.get("analyst_metric_keys") is not None
            else None
        ),
    )
    return request
