"""Explicit calendar and fiscal period normalization."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
import re

from saleslab_v2.domain.financial import NormalizationIssue
from saleslab_v2.domain.value_objects import Period

MONTHS = {
    "pt": (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
    "en": (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ),
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    "fr": (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
}
_MONTH_LOOKUP = {
    name: number
    for names in MONTHS.values()
    for number, name in enumerate(names, start=1)
}
_YEAR_LABEL = re.compile(r"^(?:(?:ano|year|año|année)\s+)?(\d{4})$", re.IGNORECASE)
_FISCAL_MARKER = re.compile(
    r"(?<!\w)(?:fy|fiscal|exerc[ií]cio|ejercicio|exercice)(?!\w)", re.IGNORECASE
)
_YTD_MARKER = re.compile(
    r"(?<!\w)(?:ytd|acumulado|acumulada|cumulé|cumulée)(?!\w)", re.IGNORECASE
)
_SEMESTER = re.compile(r"(?<!\w)(?:h[12]|s[12]|semestre)(?!\w)", re.IGNORECASE)
_QUARTER = re.compile(
    r"(?<!\w)(?:q|t)([1-4])(?!\w)|(?<!\w)([1-4])(?:º|er|e)?\s+(?:quarter|trimestre)(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PeriodResult:
    period: Period | None
    issues: tuple[NormalizationIssue, ...] = ()


@dataclass(frozen=True)
class ComparabilityResult:
    comparable: bool
    issues: tuple[NormalizationIssue, ...] = ()


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _fiscal_start(fiscal_year: int, end_month: int) -> date:
    start_month = end_month % 12 + 1
    return date(fiscal_year - (1 if start_month > 1 else 0), start_month, 1)


class PeriodNormalizer:
    version = "period-normalizer-v2"

    def normalize(
        self,
        label: str,
        *,
        year: int | None = None,
        fiscal_year_end_month: int | None = None,
        comparison_basis: str = "current",
    ) -> PeriodResult:
        text = " ".join(label.casefold().split())
        if fiscal_year_end_month is not None and not 1 <= fiscal_year_end_month <= 12:
            return self._failure("period.invalid_fiscal_year_end_month")
        detected = {int(item) for item in re.findall(r"(?<!\d)(\d{4})(?!\d)", text)}
        if len(detected) > 1:
            return self._failure("period.year_ambiguous")
        label_year = next(iter(detected)) if detected else None
        if label_year is not None and year is not None and label_year != year:
            return self._failure("period.year_conflict")
        detected_year = label_year if label_year is not None else year
        if detected_year is None:
            return self._failure("period.year_required")
        try:
            date(detected_year, 1, 1)
        except ValueError:
            return self._failure("period.invalid_year")
        if _SEMESTER.search(text):
            return self._failure("period.semester_unsupported")
        fiscal = bool(_FISCAL_MARKER.search(text))
        ytd = bool(_YTD_MARKER.search(text))
        quarter_matches = tuple(_QUARTER.finditer(text))
        quarters = {int(match.group(1) or match.group(2)) for match in quarter_matches}
        if len(quarters) > 1:
            return self._failure("period.quarter_ambiguous")
        quarter = next(iter(quarters)) if quarters else None
        month_matches = [
            number
            for name, number in _MONTH_LOOKUP.items()
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text)
        ]
        if len(month_matches) > 1:
            return self._failure("period.multiple_months")
        month = month_matches[0] if month_matches else None
        if fiscal and fiscal_year_end_month is None:
            return self._failure("period.fiscal_calendar_required")
        if quarter is not None:
            if fiscal_year_end_month is not None and not fiscal:
                return self._failure("period.quarter_basis_ambiguous")
            if fiscal:
                start = _fiscal_start(detected_year, fiscal_year_end_month)
                quarter_start = _add_months(start, (quarter - 1) * 3)
                end_first = _add_months(start, quarter * 3 - 1)
                end = _month_end(end_first.year, end_first.month)
                period_start = start if ytd else quarter_start
            else:
                start_month = (quarter - 1) * 3 + 1
                period_start = date(detected_year, 1 if ytd else start_month, 1)
                end = _month_end(detected_year, quarter * 3)
                quarter_start = date(detected_year, start_month, 1)
            if month is not None:
                quarter_months = {
                    _add_months(quarter_start, offset).month for offset in range(3)
                }
                if month not in quarter_months:
                    return self._failure("period.quarter_month_ambiguous")
            return PeriodResult(
                Period(
                    period_start,
                    end,
                    label,
                    "ytd" if ytd else "quarter",
                    detected_year if fiscal else None,
                    comparison_basis,
                    fiscal_year_end_month if fiscal else None,
                )
            )
        if month:
            if fiscal:
                return self._failure("period.fiscal_month_basis_ambiguous")
            if ytd and fiscal_year_end_month is not None:
                return self._failure("period.ytd_basis_ambiguous")
            return PeriodResult(
                Period(
                    date(detected_year, 1 if ytd else month, 1),
                    _month_end(detected_year, month),
                    label,
                    "ytd" if ytd else "month",
                    None,
                    comparison_basis,
                )
            )
        if ytd:
            return self._failure("period.ytd_cutoff_required")
        if fiscal:
            start = _fiscal_start(detected_year, fiscal_year_end_month)
            return PeriodResult(
                Period(
                    start,
                    _month_end(detected_year, fiscal_year_end_month),
                    label,
                    "year",
                    detected_year,
                    comparison_basis,
                    fiscal_year_end_month,
                )
            )
        if _YEAR_LABEL.fullmatch(text):
            return PeriodResult(
                Period(
                    date(detected_year, 1, 1),
                    date(detected_year, 12, 31),
                    label,
                    "year",
                    None,
                    comparison_basis,
                )
            )
        return self._failure("period.granularity_ambiguous")

    def comparable(self, left: Period, right: Period) -> ComparabilityResult:
        if left.granularity != right.granularity:
            return self._comparison_failure("period.incompatible_granularity")
        left_fiscal, right_fiscal = (
            left.fiscal_year is not None,
            right.fiscal_year is not None,
        )
        if left_fiscal != right_fiscal:
            return self._comparison_failure("period.fiscal_calendar_mismatch")
        if left_fiscal and left.fiscal_year_end_month != right.fiscal_year_end_month:
            return self._comparison_failure("period.fiscal_calendar_mismatch")
        if left.granularity == "month":
            structure_matches = True
        elif left.granularity == "quarter":
            structure_matches = (
                ((left.end_date.month - left.start_date.month) % 12)
                == ((right.end_date.month - right.start_date.month) % 12)
                == 2
            )
        elif left.granularity == "year":
            structure_matches = (
                (left.end_date.month - left.start_date.month) % 12
                == (right.end_date.month - right.start_date.month) % 12
                == 11
            )
        elif left.granularity == "ytd":
            left_cutoff = (
                (left.end_date.year - left.start_date.year) * 12
                + left.end_date.month
                - left.start_date.month
            )
            right_cutoff = (
                (right.end_date.year - right.start_date.year) * 12
                + right.end_date.month
                - right.start_date.month
            )
            structure_matches = left_cutoff == right_cutoff
            if not structure_matches:
                return self._comparison_failure("period.ytd_cutoff_mismatch")
        else:
            structure_matches = False
        if not structure_matches:
            return self._comparison_failure("period.incompatible_duration")
        accepted_bases = {left.comparison_basis, right.comparison_basis}
        if len(accepted_bases) > 1 and accepted_bases != {"current", "comparative"}:
            return self._comparison_failure("period.comparison_basis_mismatch")
        return ComparabilityResult(True)

    @staticmethod
    def _failure(code: str) -> PeriodResult:
        return PeriodResult(None, (NormalizationIssue(code, "period"),))

    @staticmethod
    def _comparison_failure(code: str) -> ComparabilityResult:
        return ComparabilityResult(False, (NormalizationIssue(code, "period"),))
