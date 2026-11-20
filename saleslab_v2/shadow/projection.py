"""One source-bound input for both runners; no caller-supplied legacy numbers."""

from decimal import Decimal

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.domain.normalization import canonical_decimal

from .contracts import InvalidShadow

CONCEPTS = {
    "revenue_current": ("revenue", "current"),
    "revenue_previous": ("revenue", "comparative"),
    "cogs": ("cost_of_sales", "current"),
    "opex": ("operating_costs", "current"),
    "net_result": ("net_income", "current"),
}


def shared_projection(case, normalization):
    observations = {
        (unpack(o["source_document_id"]), o["source_reference"]): unpack(o)
        for o in normalization["observations"]
    }
    fields, bindings, scope, periods, currencies = {}, [], None, {}, set()
    for name, ref in sorted(case.legacy_bindings):
        if ref.source_id is None:
            raise InvalidShadow(
                "legacy form binding must reference an original source row"
            )
        value = observations.get((str(ref.source_id), ref.key))
        concept, basis = CONCEPTS[name]
        if (
            value is None
            or value["normalization_status"] != "normalized"
            or value["value"] is None
            or value["issues"]
        ):
            raise InvalidShadow("legacy binding is missing, ambiguous or invalid")
        if (
            value["concept_id"] != concept
            or value["unit"] != "monetary"
            or value["period"]["comparison_basis"] != basis
        ):
            raise InvalidShadow(
                "legacy binding has a different concept, unit or comparison basis"
            )
        row_scope = [value["scope"], value["dimensions"]]
        if scope is not None and row_scope != scope:
            raise InvalidShadow("legacy form cannot represent mixed scopes")
        scope = row_scope
        identity = semantic_hash(value["period"])
        if basis in periods and periods[basis] != identity:
            raise InvalidShadow(
                "legacy form cannot represent multiple periods for one role"
            )
        periods[basis] = identity
        currencies.add(value["currency"])
        # The legacy parser uses comma decimals and dot grouping. Never pass a
        # canonical decimal dot which it could reinterpret as thousands.
        fields[name] = canonical_decimal(Decimal(value["value"])).replace(".", ",")
        fields["period_previous" if basis == "comparative" else "period_current"] = (
            value["period"]["label"]
        )
        bindings.append(
            {
                "field": name,
                "source_id": str(ref.source_id),
                "source_reference": ref.key,
                "observation_hash": semantic_hash(value),
                "value": canonical_decimal(Decimal(value["value"])),
            }
        )
    if len(currencies) > 1 or None in currencies:
        raise InvalidShadow("legacy form requires a single explicit currency")
    documents = unpack(case.payload()["request"])["sources"]
    # Full shared original rows remain available even when the legacy form has
    # no equivalent field (e.g. leasing); unsupported comparisons stay explicit.
    return {
        "request_hash": case.request.fingerprint,
        "currency": next(iter(currencies), None),
        "fields": fields,
        "bindings": bindings,
        "sources": documents,
        "report_language": case.request.language.report_language.value,
        "legacy_language": "pt",
        "source_hashes": sorted(b.document.content_hash for b in case.request.sources),
    }
