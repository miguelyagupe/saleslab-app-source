"""Strict LocaleTag-aware Decimal parsing without binary floating point."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from saleslab_v2.domain.enums import NullReason
from saleslab_v2.domain.financial import NormalizationIssue
from saleslab_v2.domain.language import LocaleTag

from .currencies import ISO_4217_CODES

PARSER_VERSION = "locale-number-v2"
_UNAMBIGUOUS_SYMBOLS = {"€": "EUR", "£": "GBP"}
_DOLLAR_CURRENCIES = {"USD", "CAD", "AUD", "NZD"}
_SCALES = {
    LocaleTag.PT_PT: {
        "mil": "thousands",
        "milhares": "thousands",
        "milhão": "millions",
        "milhões": "millions",
    },
    LocaleTag.EN_GB: {
        "k": "thousands",
        "thousand": "thousands",
        "thousands": "thousands",
        "m": "millions",
        "mn": "millions",
        "million": "millions",
        "millions": "millions",
    },
    LocaleTag.ES_ES: {
        "mil": "thousands",
        "miles": "thousands",
        "millón": "millions",
        "millones": "millions",
    },
    LocaleTag.FR_FR: {
        "mille": "thousands",
        "milliers": "thousands",
        "million": "millions",
        "millions": "millions",
    },
}
_NULLS = {
    "": NullReason.MISSING,
    "-": NullReason.MISSING,
    "—": NullReason.MISSING,
    "n/a": NullReason.NOT_APPLICABLE,
    "na": NullReason.NOT_APPLICABLE,
    "não aplicável": NullReason.NOT_APPLICABLE,
    "no aplicable": NullReason.NOT_APPLICABLE,
    "non applicable": NullReason.NOT_APPLICABLE,
    "não divulgado": NullReason.REDACTED,
    "not disclosed": NullReason.REDACTED,
    "no divulgado": NullReason.REDACTED,
    "non publié": NullReason.REDACTED,
    "não encontrado": NullReason.UNKNOWN,
    "not found": NullReason.UNKNOWN,
    "no encontrado": NullReason.UNKNOWN,
    "introuvable": NullReason.UNKNOWN,
}


@dataclass(frozen=True)
class NumberParseResult:
    value: Decimal | None
    null_reason: NullReason | None = None
    currency: str | None = None
    scale: str | None = None
    is_percentage: bool = False
    sign: str | None = None
    issues: tuple[NormalizationIssue, ...] = ()


class LocaleNumberParser:
    version = PARSER_VERSION

    def parse(
        self,
        text: str | None,
        *,
        locale: LocaleTag | str | None,
        accounting_parentheses: bool | None = None,
        currency_hint: str | None = None,
    ) -> NumberParseResult:
        if accounting_parentheses is not None and not isinstance(
            accounting_parentheses, bool
        ):
            raise TypeError("accounting_parentheses must be bool or None")
        raw = "" if text is None else str(text).strip()
        null_reason = _NULLS.get(raw.casefold())
        if null_reason:
            return NumberParseResult(None, null_reason=null_reason)
        try:
            locale_tag = locale if isinstance(locale, LocaleTag) else LocaleTag(locale)
        except (TypeError, ValueError):
            return self._failure("number.locale_required")
        issues: list[NormalizationIssue] = []
        value_text = raw.replace("\u00a0", " ").replace("\u202f", " ").strip()
        currencies: set[str] = set()
        for match in tuple(re.finditer(r"(?<![A-Z])([A-Z]{3})(?![A-Z])", value_text)):
            token = match.group(1)
            if token in ISO_4217_CODES:
                currencies.add(token)
            else:
                issues.append(
                    NormalizationIssue(
                        "currency.unknown", "currency", (("code", token),)
                    )
                )
            value_text = re.sub(
                rf"(?<![A-Z]){re.escape(match.group(1))}(?![A-Z])",
                "",
                value_text,
            )
        for symbol, code in _UNAMBIGUOUS_SYMBOLS.items():
            if symbol in value_text:
                currencies.add(code)
                value_text = value_text.replace(symbol, "")
        if "$" in value_text:
            value_text = value_text.replace("$", "")
            if currency_hint in _DOLLAR_CURRENCIES:
                currencies.add(currency_hint)
            elif len(currencies) == 1 and currencies <= _DOLLAR_CURRENCIES:
                # An explicit compatible ISO code in the value disambiguates the
                # otherwise ambiguous symbol (for example, ``CAD $10``).
                pass
            else:
                issues.append(
                    NormalizationIssue("currency.ambiguous_symbol", "currency")
                )
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        if len(currencies) > 1:
            issues.append(NormalizationIssue("currency.conflict", "currency"))
        value_text = value_text.strip()
        percentage = "%" in value_text
        if percentage:
            if value_text.count("%") != 1:
                return self._failure("number.invalid_percentage", issues)
            if value_text.endswith("%"):
                value_text = value_text[:-1].strip()
            elif value_text.startswith("(") and value_text.endswith("%)"):
                value_text = value_text[:-2].strip() + ")"
            else:
                return self._failure("number.invalid_percentage", issues)
        parentheses = value_text.startswith("(") and value_text.endswith(")")
        if "(" in value_text or ")" in value_text:
            if not parentheses:
                return self._failure("number.invalid_parentheses", issues)
            if accounting_parentheses is None:
                return self._failure("number.parentheses_sign_ambiguous", issues)
            value_text = value_text[1:-1].strip()
        scale = None
        for alias, resolved in sorted(
            _SCALES[locale_tag].items(), key=lambda item: -len(item[0])
        ):
            match = re.search(rf"(?i)(?:\s+|(?<=\d)){re.escape(alias)}\s*$", value_text)
            if match:
                scale = resolved
                value_text = value_text[: match.start()].strip()
                break
        negative, positive = value_text.startswith("-"), value_text.startswith("+")
        if negative or positive:
            value_text = value_text[1:].strip()
        if parentheses and (negative or positive):
            return self._failure("number.conflicting_sign", issues)
        if not re.fullmatch(r"[0-9][0-9., ]*", value_text):
            return self._failure("number.invalid", issues)
        decimal_sep = "." if locale_tag is LocaleTag.EN_GB else ","
        locale_group_sep = (
            ","
            if locale_tag is LocaleTag.EN_GB
            else ("." if locale_tag in {LocaleTag.PT_PT, LocaleTag.ES_ES} else " ")
        )
        if value_text.count(decimal_sep) > 1:
            return self._failure("number.ambiguous_separator", issues)
        integer, separator, fraction = value_text.partition(decimal_sep)
        if separator and not fraction:
            return self._failure("number.invalid", issues)
        allowed_group_separators = {locale_group_sep, " "}
        incompatible = {".", ","} - {decimal_sep, locale_group_sep}
        if any(item in value_text for item in incompatible):
            return self._failure("number.incompatible_separator", issues)
        used = [item for item in allowed_group_separators if item and item in integer]
        if len(used) > 1:
            return self._failure("number.incompatible_separator", issues)
        if used:
            groups = integer.split(used[0])
            if not 1 <= len(groups[0]) <= 3 or any(
                len(group) != 3 for group in groups[1:]
            ):
                return self._failure("number.invalid_grouping", issues)
        canonical = integer.replace(" ", "").replace(locale_group_sep, "")
        if separator:
            canonical += "." + fraction
        try:
            value = Decimal(canonical)
        except InvalidOperation:
            return self._failure("number.invalid", issues)
        if negative or (parentheses and accounting_parentheses):
            value = -value
        return NumberParseResult(
            value,
            currency=currency,
            scale=scale,
            is_percentage=percentage,
            sign="negative" if value < 0 else "zero" if value == 0 else "positive",
            issues=tuple(issues),
        )

    @staticmethod
    def _failure(
        code: str, existing: list[NormalizationIssue] | None = None
    ) -> NumberParseResult:
        return NumberParseResult(
            None,
            null_reason=NullReason.PARSE_ERROR,
            issues=tuple(existing or ()) + (NormalizationIssue(code, "value"),),
        )
