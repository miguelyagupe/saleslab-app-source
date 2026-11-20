"""PDF text preparation is separately versioned from accepted tabular imports."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .tabular_contracts import TableSelection


class PdfPageLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    page: int = Field(ge=1, le=300)
    cuts: list[int] = Field(default_factory=list, max_length=31)

    @field_validator("cuts")
    @classmethod
    def ordered_boundaries(cls, value):
        if any(
            type(v) is not int or not 1 <= v <= 2000 for v in value
        ) or value != sorted(set(value)):
            raise ValueError("use distinct increasing character boundaries")
        return value


class PdfLayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    pages: list[PdfPageLayout] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def ordered_pages(self):
        pages = [p.page for p in self.pages]
        if pages != sorted(set(pages)):
            raise ValueError("select distinct pages in their original order")
        return self


class PdfTableSelection(TableSelection):
    header_row: int = Field(ge=0, le=1000)
    first_row: int = Field(ge=1, le=1000)
    last_row: int = Field(ge=1, le=1000)


class PdfImport(PdfLayoutRequest):
    version: Literal["1"] = "1"
    inspection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tables: list[PdfTableSelection] = Field(min_length=1, max_length=12)
    excluded_content_confirmed: Literal[True]
    review_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirmed_rows: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("excluded_content_confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit confirmation is required")
        return value

    @field_validator("confirmed_rows")
    @classmethod
    def canonical_confirmations(cls, value):
        if len(value) != len(set(value)) or any(not 1 <= len(v) <= 40 for v in value):
            raise ValueError("confirm distinct extracted source references")
        return sorted(value)
