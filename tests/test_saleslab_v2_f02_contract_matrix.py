"""Adversarial executable matrix for the closed F-02 contract."""

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
import unittest
from uuid import UUID

from saleslab_v2.domain.enums import NullReason
from saleslab_v2.domain.financial import NormalizationIssue, NormalizedObservation
from saleslab_v2.domain.normalization import canonical_decimal, exact_power_of_ten
from saleslab_v2.domain.value_objects import Money, Period
from saleslab_v2.normalization.financial import SourceMetadata
from saleslab_v2.normalization.numbers import LocaleNumberParser
from saleslab_v2.normalization.periods import PeriodNormalizer
from saleslab_v2.domain.enums import LanguageCode
from saleslab_v2.domain.language import LocaleTag


class F02ContractMatrixTests(unittest.TestCase):
    def observation(self, **changes):
        values = dict(
            concept_id="gross_margin",
            semantic_type="percentage_ratio",
            value=Decimal("25"),
            null_reason=None,
            currency=None,
            scale=None,
            unit="percentage",
            numerator=Decimal("1"),
            denominator=Decimal("4"),
            sign="positive",
            expected_sign=None,
            period=Period(
                date(2026, 1, 1),
                date(2026, 12, 31),
                granularity="year",
                comparison_basis="current",
            ),
            statement_type="income_statement",
            scope="consolidated",
            original_name="Gross margin",
            original_text="25%",
            source_document_id=UUID("11111111-1111-4111-8111-111111111111"),
            source_content_hash="a" * 64,
            source_reference="sheet:1!B2",
            source_language="en",
            format_locale="en-GB",
            confidence=Decimal("1"),
        )
        values.update(changes)
        return NormalizedObservation(**values)

    def test_decimal_scaling_and_hashing_ignore_all_active_contexts(self):
        raw = Decimal("123456789012345678901234567890.00")
        expected = Decimal("123456789012345678901234567890000000.00")
        hashes = set()
        for precision in (6, 28, 50, 100):
            for rounding in ("ROUND_DOWN", "ROUND_UP", "ROUND_HALF_EVEN"):
                with localcontext() as context:
                    context.prec = precision
                    context.rounding = rounding
                    scaled = exact_power_of_ten(raw, 6)
                    self.assertEqual(scaled, expected)
                    hashes.add(
                        replace(
                            self.observation(), value=Decimal("25.00")
                        ).semantic_hash
                    )
        self.assertEqual(len(hashes), 1)
        self.assertEqual(canonical_decimal(Decimal("1.00")), "1")
        self.assertEqual(canonical_decimal(Decimal("-0")), "0")

    def test_every_decimal_field_rejects_nonfinite_and_non_decimal(self):
        for field in ("value", "confidence", "numerator", "denominator"):
            for invalid in (
                Decimal("NaN"),
                Decimal("sNaN"),
                Decimal("Infinity"),
                Decimal("-Infinity"),
                1,
                True,
                "1",
                1.0,
                object(),
            ):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(
                    (TypeError, ValueError)
                ):
                    replace(self.observation(), **{field: invalid})
        for invalid in (Decimal("NaN"), Decimal("Infinity"), 1, "1", True):
            with self.assertRaises((TypeError, ValueError)):
                Money(invalid, "EUR")

    def test_normalized_percentage_requires_exact_complete_ratio(self):
        for changes in (
            {"numerator": None, "denominator": None},
            {"denominator": Decimal("0")},
            {"numerator": Decimal("2"), "denominator": Decimal("4")},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.observation(), **changes)
        huge = "9" * 500
        self.assertTrue(
            replace(
                self.observation(),
                value=Decimal("100"),
                numerator=Decimal(huge),
                denominator=Decimal(huge),
            ).ratio_complete
        )

    def test_closed_states_and_null_quantitative_metadata(self):
        with self.assertRaises(ValueError):
            replace(self.observation(), normalization_status="needs_confirmation")
        with self.assertRaises(ValueError):
            replace(self.observation(), issues=(NormalizationIssue("x", "value"),))
        null = replace(
            self.observation(),
            value=None,
            null_reason=NullReason.MISSING,
            unit=None,
            numerator=None,
            denominator=None,
            sign=None,
        )
        self.assertIsNone(null.value)
        with self.assertRaises(ValueError):
            replace(null, currency="EUR")

    def test_fiscal_metadata_without_ytd_marker_is_ambiguous(self):
        result = PeriodNormalizer().normalize("YTD March 2026", fiscal_year_end_month=6)
        self.assertIsNone(result.period)
        self.assertEqual(result.issues[0].code, "period.ytd_basis_ambiguous")

    def test_reserved_currency_policy_is_blocking(self):
        with self.assertRaises(ValueError):
            SourceMetadata(
                UUID("11111111-1111-4111-8111-111111111111"),
                "a" * 64,
                LanguageCode.EN,
                LocaleTag.EN_GB,
                currency="XXX",
            )

    def test_accounting_parentheses_requires_an_actual_boolean(self):
        valid = (None, False, True)
        for convention in valid:
            source = SourceMetadata(
                UUID("11111111-1111-4111-8111-111111111111"),
                "a" * 64,
                LanguageCode.EN,
                LocaleTag.EN_GB,
                accounting_parentheses=convention,
            )
            self.assertIs(source.accounting_parentheses, convention)
        for invalid in ("false", 0, 1, [], {}, object()):
            with self.subTest(invalid=invalid), self.assertRaises(TypeError):
                SourceMetadata(
                    UUID("11111111-1111-4111-8111-111111111111"),
                    "a" * 64,
                    LanguageCode.EN,
                    LocaleTag.EN_GB,
                    accounting_parentheses=invalid,
                )
        parser = LocaleNumberParser()
        self.assertEqual(
            parser.parse(
                "(10)", locale=LocaleTag.EN_GB, accounting_parentheses=True
            ).value,
            Decimal("-10"),
        )
        self.assertEqual(
            parser.parse(
                "(10)", locale=LocaleTag.EN_GB, accounting_parentheses=False
            ).value,
            Decimal("10"),
        )
        ambiguous = parser.parse(
            "(10)", locale=LocaleTag.EN_GB, accounting_parentheses=None
        )
        self.assertIsNone(ambiguous.value)
        self.assertEqual(
            [issue.code for issue in ambiguous.issues],
            ["number.parentheses_sign_ambiguous"],
        )
        for invalid in ("false", "true", 0, 1, [], {}, object()):
            with self.subTest(parser_invalid=invalid), self.assertRaises(TypeError):
                parser.parse(
                    "(10)",
                    locale=LocaleTag.EN_GB,
                    accounting_parentheses=invalid,
                )

    def test_observation_uses_the_canonical_monetary_allowlist(self):
        monetary = replace(
            self.observation(),
            concept_id="revenue",
            semantic_type="monetary_flow",
            value=Decimal("10"),
            unit="monetary",
            numerator=None,
            denominator=None,
            currency="EUR",
            scale="raw",
        )
        self.assertEqual(monetary.currency, "EUR")
        for invalid in ("ZZZ", "BOV", "XXX", "XTS", "XAU"):
            with self.subTest(currency=invalid), self.assertRaises(ValueError):
                replace(monetary, currency=invalid)

    def test_fiscal_quarter_matrix_and_ytd_boundaries(self):
        for end_month in range(1, 13):
            fiscal_start_month = end_month % 12 + 1
            fiscal_start_year = 2025 if fiscal_start_month != 1 else 2026
            for quarter in range(4):
                offset = (fiscal_start_month - 1) + quarter * 3
                start = date(fiscal_start_year + offset // 12, offset % 12 + 1, 1)
                end_offset = offset + 2
                end_year = fiscal_start_year + end_offset // 12
                end_month_value = end_offset % 12 + 1
                import calendar

                end = date(
                    end_year,
                    end_month_value,
                    calendar.monthrange(end_year, end_month_value)[1],
                )
                Period(
                    start,
                    end,
                    granularity="quarter",
                    fiscal_year=2026,
                    fiscal_year_end_month=end_month,
                    comparison_basis="current",
                )
        with self.assertRaises(ValueError):
            Period(
                date(2024, 1, 1),
                date(2024, 3, 31),
                granularity="quarter",
                fiscal_year=2026,
                fiscal_year_end_month=6,
                comparison_basis="current",
            )
        Period(
            date(2024, 1, 1),
            date(2024, 2, 29),
            granularity="ytd",
            comparison_basis="current",
        )
        with self.assertRaises(ValueError):
            Period(
                date(2025, 1, 1),
                date(2026, 12, 31),
                granularity="ytd",
                comparison_basis="current",
            )
        Period(
            date(2025, 7, 1),
            date(2026, 2, 28),
            granularity="ytd",
            fiscal_year=2026,
            fiscal_year_end_month=6,
            comparison_basis="current",
        )
        with self.assertRaises(ValueError):
            Period(
                date(2025, 7, 1),
                date(2026, 7, 31),
                granularity="ytd",
                fiscal_year=2026,
                fiscal_year_end_month=6,
                comparison_basis="current",
            )

    def test_economic_hash_excludes_only_source_scale(self):
        thousand = replace(
            self.observation(),
            concept_id="revenue",
            semantic_type="monetary_flow",
            value=Decimal("1000"),
            currency="EUR",
            scale="thousands",
            unit="monetary",
            numerator=None,
            denominator=None,
        )
        raw = replace(thousand, scale="raw")
        self.assertEqual(thousand.economic_identity_hash, raw.economic_identity_hash)
        self.assertNotEqual(thousand.semantic_hash, raw.semantic_hash)

    def test_audit_and_economic_hashes_have_distinct_provenance_semantics(self):
        first = self.observation()
        second = replace(
            first,
            source_reference="sheet:2!C9",
            source_language="fr",
            format_locale="fr-FR",
            original_name="Marge brute",
        )
        self.assertNotEqual(first.semantic_hash, second.semantic_hash)
        self.assertEqual(first.economic_identity_hash, second.economic_identity_hash)


if __name__ == "__main__":
    unittest.main()
