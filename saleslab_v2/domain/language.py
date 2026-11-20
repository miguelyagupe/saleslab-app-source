from dataclasses import dataclass
from enum import Enum

from .enums import LanguageCode


class LocaleTag(str, Enum):
    PT_PT = "pt-PT"
    EN_GB = "en-GB"
    ES_ES = "es-ES"
    FR_FR = "fr-FR"


@dataclass(frozen=True)
class LanguageContext:
    source_languages: tuple[LanguageCode, ...]
    analysis_language: LanguageCode
    report_language: LanguageCode
    format_locale: LocaleTag
    accounting_context: str | None = None

    def semantic_dict(self) -> dict[str, object]:
        """Semantic identity excludes presentation-only locale."""
        return {
            "source_languages": [item.value for item in self.source_languages],
            "analysis_language": self.analysis_language.value,
            "report_language": self.report_language.value,
            "accounting_context": self.accounting_context,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.semantic_dict(), "format_locale": self.format_locale.value}
