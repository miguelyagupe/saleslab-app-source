from dataclasses import dataclass
from datetime import date
import calendar
from decimal import Decimal
from typing import Any

from .enums import NullReason
from .normalization import require_finite_decimal

_PERIOD_GRANULARITIES = {"month", "quarter", "year", "ytd"}
_COMPARISON_BASES = {"current", "comparative", "budget", "forecast"}


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str
    scale: str = "raw"

    def __post_init__(self):
        require_finite_decimal(self.amount, "amount")
        if (
            len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or not self.currency.isupper()
        ):
            raise ValueError(
                "currency must be a structurally valid uppercase three-letter code"
            )
        if self.scale not in {"raw", "thousands", "millions"}:
            raise ValueError("invalid money scale")

    def to_dict(self) -> dict[str, str]:
        return {
            "amount": str(self.amount),
            "currency": self.currency,
            "scale": self.scale,
        }


@dataclass(frozen=True)
class Period:
    start_date: date
    end_date: date
    label: str | None = None
    granularity: str | None = None
    fiscal_year: int | None = None
    comparison_basis: str | None = None
    fiscal_year_end_month: int | None = None

    def __post_init__(self):
        if not isinstance(self.start_date, date) or not isinstance(self.end_date, date):
            raise TypeError("period dates must be date values")
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")
        if (
            self.granularity is not None
            and self.granularity not in _PERIOD_GRANULARITIES
        ):
            raise ValueError("invalid period granularity")
        if (
            self.comparison_basis is not None
            and self.comparison_basis not in _COMPARISON_BASES
        ):
            raise ValueError("invalid comparison_basis")
        if self.granularity is not None and self.comparison_basis is None:
            raise ValueError("structured periods require comparison_basis")
        if (
            self.fiscal_year_end_month is not None
            and not 1 <= self.fiscal_year_end_month <= 12
        ):
            raise ValueError("fiscal_year_end_month must be between 1 and 12")
        if (self.fiscal_year is None) != (self.fiscal_year_end_month is None):
            raise ValueError(
                "fiscal year and calendar metadata must be supplied together"
            )
        if self.granularity is not None and self.start_date.day != 1:
            raise ValueError("periods must start on the first day of a month")
        if (
            self.granularity is not None
            and self.end_date.day
            != calendar.monthrange(self.end_date.year, self.end_date.month)[1]
        ):
            raise ValueError("periods must end on the last day of a month")
        elapsed = (
            (self.end_date.year - self.start_date.year) * 12
            + self.end_date.month
            - self.start_date.month
        )
        if self.granularity == "month" and elapsed != 0:
            raise ValueError("month periods must span one month")
        if self.granularity == "quarter" and elapsed != 2:
            raise ValueError("quarter periods must span three months")
        if self.granularity == "year" and elapsed != 11:
            raise ValueError("year periods must span twelve months")
        if self.granularity == "ytd" and elapsed < 0:
            raise ValueError("YTD cutoff cannot precede its year boundary")
        if self.granularity == "ytd" and elapsed > 11:
            raise ValueError("YTD periods must remain within one financial year")
        if (
            self.fiscal_year is None
            and self.granularity in {"year", "ytd"}
            and self.start_date.month != 1
        ):
            raise ValueError("civil year and YTD periods must start in January")
        if self.fiscal_year is not None:
            expected_start_month = self.fiscal_year_end_month % 12 + 1
            expected_start_year = (
                self.fiscal_year if expected_start_month == 1 else self.fiscal_year - 1
            )
            fiscal_start = date(expected_start_year, expected_start_month, 1)
            fiscal_end = date(
                self.fiscal_year,
                self.fiscal_year_end_month,
                calendar.monthrange(self.fiscal_year, self.fiscal_year_end_month)[1],
            )
            if self.granularity in {"year", "ytd"} and self.start_date != fiscal_start:
                raise ValueError("fiscal periods must start at the fiscal boundary")
            if self.granularity == "year" and self.end_date != fiscal_end:
                raise ValueError("period end is inconsistent with fiscal year")
            if self.granularity == "ytd" and self.end_date > fiscal_end:
                raise ValueError("fiscal YTD cutoff exceeds its fiscal year")
            if self.granularity == "quarter":
                valid_quarters = []
                for quarter in range(4):
                    offset = expected_start_month - 1 + quarter * 3
                    quarter_start = date(
                        expected_start_year + offset // 12, offset % 12 + 1, 1
                    )
                    end_offset = offset + 2
                    end_year = expected_start_year + end_offset // 12
                    end_month = end_offset % 12 + 1
                    quarter_end = date(
                        end_year,
                        end_month,
                        calendar.monthrange(end_year, end_month)[1],
                    )
                    valid_quarters.append((quarter_start, quarter_end))
                if (self.start_date, self.end_date) not in valid_quarters:
                    raise ValueError("quarter is inconsistent with fiscal year")

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.start_date.isoformat(),
            self.end_date.isoformat(),
            self.granularity,
            self.fiscal_year,
            self.comparison_basis,
            self.fiscal_year_end_month,
        )


@dataclass(frozen=True)
class Unit:
    code: str
    family: str
    label: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return self.code, self.family


@dataclass(frozen=True)
class DimensionRef:
    dimension_type: str
    dimension_id: str
    label: str | None = None
    parent_ids: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, str]:
        return self.dimension_type, self.dimension_id


@dataclass(frozen=True)
class TypedValue:
    data_type: str
    value: Any = None
    null_reason: NullReason | None = None
    original_text: str | None = None

    def __post_init__(self):
        if (self.value is None) == (self.null_reason is None):
            raise ValueError("exactly one of value and null_reason is required")
