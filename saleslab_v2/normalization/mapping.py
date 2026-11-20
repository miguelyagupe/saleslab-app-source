from dataclasses import dataclass
from decimal import Decimal

from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.domain.financial import NormalizationIssue
from saleslab_v2.i18n.catalog import TerminologyEntry, TerminologyRegistry


@dataclass(frozen=True)
class MappingResult:
    entry: TerminologyEntry | None
    confidence: Decimal
    reason_code: str
    issues: tuple[NormalizationIssue, ...] = ()


class SemanticMapper:
    def __init__(self, registry: TerminologyRegistry | None = None):
        self.registry = registry or TerminologyRegistry()

    def map(
        self, label: str, *, language: LanguageCode, statement_type: str | None = None
    ) -> MappingResult:
        entry = self.registry.lookup(label, language, statement_type)
        if entry:
            issues = ()
            if entry.reason_code != "mapping.alias_exact":
                issues = (NormalizationIssue(entry.reason_code, "original_name"),)
            return MappingResult(entry, entry.confidence, entry.reason_code, issues)
        unconstrained = self.registry.lookup(label, language)
        if unconstrained and statement_type:
            issue = NormalizationIssue(
                "mapping.statement_context_conflict", "original_name"
            )
        elif label.strip().casefold() in {"resultado", "result", "résultat"}:
            issue = NormalizationIssue("mapping.ambiguous_label", "original_name")
        else:
            issue = NormalizationIssue("mapping.unknown_label", "original_name")
        return MappingResult(None, Decimal("0"), issue.code, (issue,))
