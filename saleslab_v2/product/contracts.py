"""Bounded upload decisions; identity comes from authentication, never the form."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from saleslab_v2.normalization.currencies import ISO_4217_CODES

from .pdf_contracts import PdfImport
from .roic_contracts import RoicSourceChoices
from .tabular_contracts import TabularImport

VERSION = "financial-upload-v1"
PLANNING_VERSIONS = (
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "13",
)
NORMALIZATION_VERSIONS = ("1", "2")


class InvalidUpload(ValueError):
    pass


class UploadChoices(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_language: Literal["pt", "en", "es", "fr"]
    format_locale: Literal["pt-PT", "en-GB", "es-ES", "fr-FR"]
    report_language: Literal["pt", "en", "es", "fr"]
    target_period: str = Field(default="", max_length=100)
    rounding_tolerance: str = Field(default="0", max_length=64)
    cash_rounding_tolerance: str | None = Field(default=None, max_length=64)
    profitability_rounding_tolerance: str | None = Field(default=None, max_length=64)
    capital_rounding_tolerance: str | None = Field(default=None, max_length=64)
    growth_rounding_tolerance: str | None = Field(default=None, max_length=64)
    working_capital_definitions: (
        dict[str, Annotated[str, Field(min_length=10, max_length=2000)]] | None
    ) = Field(default=None, max_length=500)
    working_capital_rounding_tolerance: str | None = Field(default=None, max_length=64)
    roic_sources: RoicSourceChoices | None = None
    capex_definitions: (
        dict[str, Annotated[str, Field(min_length=10, max_length=2000)]] | None
    ) = Field(default=None, max_length=500)
    capex_rounding_tolerance: str | None = Field(default=None, max_length=64)
    debt_definitions: (
        dict[str, Annotated[str, Field(min_length=10, max_length=2000)]] | None
    ) = Field(default=None, max_length=500)
    debt_rounding_tolerance: str | None = Field(default=None, max_length=64)
    growth_definitions: (
        dict[str, Annotated[str, Field(min_length=10, max_length=2000)]] | None
    ) = Field(default=None, max_length=500)
    mappings: dict[str, str] = Field(default_factory=dict, max_length=500)
    # Absence means the original plan, including already persisted queue inputs.
    planning_version: Literal[
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13"
    ] = "1"
    accounting_parentheses: bool | None = None
    fiscal_year_end_month: int | None = Field(default=None, ge=1, le=12)
    source_currency: str | None = Field(default=None, max_length=3)
    source_scale: Literal["raw", "thousands", "millions"] | None = None
    normalization_version: Literal["1", "2"] = "1"
    tabular_import: TabularImport | None = None
    pdf_import: PdfImport | None = None

    @model_validator(mode="after")
    def one_source_preparation(self):
        if self.planning_version != "13" and (
            self.working_capital_definitions is not None
            or self.working_capital_rounding_tolerance is not None
        ):
            raise ValueError(
                "working capital disclosure choices require planning version 13"
            )
        if self.roic_sources is not None and self.planning_version not in {"12", "13"}:
            raise ValueError("ROIC source choices require planning version 12")
        if (
            self.planning_version in {"9", "10", "11", "12", "13"}
            and self.normalization_version != "2"
        ):
            raise ValueError(
                "planning version 9 requires exact normalization version 2"
            )
        if self.planning_version not in {"9", "10", "11", "12", "13"} and (
            self.growth_definitions is not None
            or self.growth_rounding_tolerance is not None
        ):
            raise ValueError("growth choices require planning version 9")
        if self.planning_version not in {"10", "11", "12", "13"} and (
            self.debt_definitions is not None
            or self.debt_rounding_tolerance is not None
        ):
            raise ValueError("debt schedule choices require planning version 10")
        if self.planning_version not in {"11", "12", "13"} and (
            self.capex_definitions is not None
            or self.capex_rounding_tolerance is not None
        ):
            raise ValueError("capex classification choices require planning version 11")
        if self.tabular_import is not None and self.pdf_import is not None:
            raise ValueError("choose one source preparation contract")
        return self

    def contract_payload(self, *, exclude=()):
        payload = self.model_dump(exclude=set(exclude))
        if self.normalization_version == "1":
            payload.pop("normalization_version", None)
        if self.planning_version == "1":
            payload.pop("planning_version", None)
        if self.accounting_parentheses is None:
            payload.pop("accounting_parentheses", None)
        if self.fiscal_year_end_month is None:
            payload.pop("fiscal_year_end_month", None)
        for field in (
            "working_capital_definitions",
            "working_capital_rounding_tolerance",
            "roic_sources",
            "source_currency",
            "source_scale",
            "cash_rounding_tolerance",
            "profitability_rounding_tolerance",
            "capital_rounding_tolerance",
            "growth_rounding_tolerance",
            "growth_definitions",
            "debt_definitions",
            "capex_definitions",
            "capex_rounding_tolerance",
            "debt_rounding_tolerance",
            "tabular_import",
            "pdf_import",
        ):
            if getattr(self, field) is None:
                payload.pop(field, None)
        return payload

    @field_validator("source_currency")
    @classmethod
    def validate_source_currency(cls, value):
        if value is not None and value not in ISO_4217_CODES:
            raise ValueError("use a supported monetary ISO currency code")
        return value

    @field_validator(
        "rounding_tolerance",
        "cash_rounding_tolerance",
        "profitability_rounding_tolerance",
        "capital_rounding_tolerance",
        "growth_rounding_tolerance",
        "debt_rounding_tolerance",
        "capex_rounding_tolerance",
        "working_capital_rounding_tolerance",
    )
    @classmethod
    def validate_tolerance(cls, value):
        if value is None:
            return value
        try:
            number = Decimal(value)
            if (
                not number.is_finite()
                or number < 0
                or number.adjusted() > 63
                or number.as_tuple().exponent < -63
                or len(format(number, "f")) > 64
            ):
                raise ValueError("invalid rounding tolerance")
        except Exception as exc:
            raise ValueError("use a finite nonnegative rounding tolerance") from exc
        return value


class GenerateChoices(UploadChoices):
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_key: Annotated[str, Field(pattern=r"^[0-9a-fA-F-]{36}$")]

    @field_validator("attempt_key")
    @classmethod
    def validate_attempt(cls, value):
        return str(UUID(value))
