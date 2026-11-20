"""Source-bound ROIC decisions, validated before numerical planning."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=10, max_length=2000)]
Location = Annotated[str, Field(min_length=1, max_length=200)]


class RoicSourceChoices(BaseModel):
    """Require reported NOPAT and an explicit average, never infer tax or capital."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    nopat_location: Location
    nopat_definition: Text
    tax_assumptions_location: Location
    tax_assumptions: Text
    average_capital_location: Location
    average_capital_definition: Text

    @model_validator(mode="after")
    def distinct_operands(self):
        if self.nopat_location == self.average_capital_location:
            raise ValueError("NOPAT and average invested capital require distinct rows")
        for value in (
            self.nopat_definition,
            self.tax_assumptions,
            self.average_capital_definition,
        ):
            if value != value.strip():
                raise ValueError("Source quotations must not contain outer whitespace")
        return self
