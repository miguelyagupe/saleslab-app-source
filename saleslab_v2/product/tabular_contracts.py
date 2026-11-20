"""Explicit, bounded source selections; no inferred columns or metadata."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FIELDS = (
    "line",
    "amount",
    "period",
    "statement_type",
    "scope",
    "currency",
    "scale",
    "comparison_basis",
)


class ColumnBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    column: str | None = Field(default=None, pattern=r"^(?:[A-Z]|A[A-F])$")
    constant: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.column is None) == (self.constant is None):
            raise ValueError("choose one source column or confirmed constant")
        return self


class TableSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    sheet: int = Field(ge=1, le=8)
    header_row: int = Field(ge=1, le=1000)
    first_row: int = Field(ge=2, le=1000)
    last_row: int = Field(ge=2, le=1000)
    fields: dict[str, ColumnBinding] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def explicit_table(self):
        if not self.header_row < self.first_row <= self.last_row:
            raise ValueError("select a header followed by an explicit data interval")
        if set(self.fields) != set(FIELDS):
            raise ValueError("bind every canonical financial field")
        if any(self.fields[k].column is None for k in ("line", "amount")):
            raise ValueError("line and amount require source columns")
        columns = [v.column for v in self.fields.values() if v.column is not None]
        if len(columns) != len(set(columns)):
            raise ValueError("one source column cannot supply different fields")
        return self


class TabularImport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal["1"] = "1"
    inspection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    delimiter: Literal[";", ",", "\t"] | None = None
    tables: list[TableSelection] = Field(min_length=1, max_length=12)
    excluded_content_confirmed: Literal[True]

    @field_validator("excluded_content_confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit confirmation is required")
        return value
