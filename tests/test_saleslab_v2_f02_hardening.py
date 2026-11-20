from dataclasses import replace
from datetime import date
from decimal import Decimal
import unittest
from uuid import UUID

from saleslab_v2.domain.enums import LanguageCode, NullReason
from saleslab_v2.domain.financial import NormalizationIssue, NormalizedObservation
from saleslab_v2.domain.language import LocaleTag
from saleslab_v2.domain.value_objects import Period
from saleslab_v2.i18n.catalog import (
    FINANCIAL_TERMS,
    TerminologyEntry,
    TerminologyRegistry,
)
from saleslab_v2.normalization.financial import (
    FinancialNormalizer,
    FinancialRow,
    SourceMetadata,
)
from saleslab_v2.normalization.numbers import LocaleNumberParser
from saleslab_v2.normalization.periods import PeriodNormalizer

SOURCE_ID = UUID("22222222-2222-4222-8222-222222222222")
HASH = "b" * 64
MONTH_LABELS = {
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


class PeriodHardeningTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = PeriodNormalizer()

    def test_all_twelve_months_in_all_four_languages_use_boundaries(self):
        for language, names in MONTH_LABELS.items():
            for expected_month, name in enumerate(names, 1):
                with self.subTest(language=language, month=name):
                    period = self.normalizer.normalize(f"{name} 2026").period
                    self.assertEqual(
                        (period.start_date.month, period.end_date.month),
                        (expected_month, expected_month),
                    )
                    self.assertEqual(period.granularity, "month")
        self.assertIsNone(self.normalizer.normalize("maybe 2026").period)
        self.assertIsNone(self.normalizer.normalize("marchandise 2026").period)

    def test_unrecognized_and_semester_labels_never_become_years(self):
        for label in (
            "H1 2026",
            "S1 2026",
            "Mystery 2026",
            "Period 2026",
            "Smarch 2026",
        ):
            with self.subTest(label=label):
                result = self.normalizer.normalize(label)
                self.assertIsNone(result.period)
                self.assertTrue(result.issues)
        self.assertEqual(
            self.normalizer.normalize("H1 2026").issues[0].code,
            "period.semester_unsupported",
        )

    def test_conflicting_quarters_are_ambiguous_but_duplicates_are_consistent(self):
        ambiguous = self.normalizer.normalize("Q1 Q2 2026")
        self.assertIsNone(ambiguous.period)
        self.assertEqual(ambiguous.issues[0].code, "period.quarter_ambiguous")
        duplicate = self.normalizer.normalize("Q1 Q1 2026")
        self.assertEqual(duplicate.period.granularity, "quarter")
        self.assertEqual(duplicate.period.start_date, date(2026, 1, 1))

    def test_general_four_digit_financial_years_are_validated(self):
        for label in ("Year 1999", "FY 1999", "Q4 1999"):
            with self.subTest(label=label):
                kwargs = {"fiscal_year_end_month": 12} if label.startswith("FY") else {}
                self.assertIsNotNone(self.normalizer.normalize(label, **kwargs).period)
        invalid = self.normalizer.normalize("Year 0000")
        self.assertIsNone(invalid.period)
        self.assertEqual(invalid.issues[0].code, "period.invalid_year")

    def test_label_year_evidence_is_resolved_before_argument_fallback(self):
        for label, year, code in (
            ("Q1 2025 2026", 2027, "period.year_ambiguous"),
            ("Q1 2025", 2026, "period.year_conflict"),
        ):
            result = self.normalizer.normalize(label, year=year)
            self.assertIsNone(result.period)
            self.assertEqual(result.issues[0].code, code)
        self.assertEqual(
            self.normalizer.normalize("Q1", year=1999).period.start_date,
            date(1999, 1, 1),
        )
        self.assertEqual(
            self.normalizer.normalize("Q1 1999", year=1999).period.start_date,
            date(1999, 1, 1),
        )

    def test_calendar_and_fiscal_quarter_ytd_dates(self):
        expected_calendar = {
            1: (date(2026, 1, 1), date(2026, 3, 31)),
            2: (date(2026, 1, 1), date(2026, 6, 30)),
            3: (date(2026, 1, 1), date(2026, 9, 30)),
            4: (date(2026, 1, 1), date(2026, 12, 31)),
        }
        for quarter, expected in expected_calendar.items():
            period = self.normalizer.normalize(f"Q{quarter} YTD 2026").period
            self.assertEqual((period.start_date, period.end_date), expected)
            self.assertEqual(period.granularity, "ytd")
        fiscal_q2 = self.normalizer.normalize(
            "FY 2026 Q2 YTD", fiscal_year_end_month=6
        ).period
        self.assertEqual(
            (fiscal_q2.start_date, fiscal_q2.end_date),
            (date(2025, 7, 1), date(2025, 12, 31)),
        )
        fiscal_q1 = self.normalizer.normalize(
            "FY 2026 Q1", fiscal_year_end_month=6
        ).period
        self.assertEqual(
            (fiscal_q1.start_date, fiscal_q1.end_date),
            (date(2025, 7, 1), date(2025, 9, 30)),
        )

    def test_fiscal_context_and_end_month_validation_are_explicit(self):
        self.assertEqual(
            self.normalizer.normalize("Q1 2026", fiscal_year_end_month=6)
            .issues[0]
            .code,
            "period.quarter_basis_ambiguous",
        )
        for invalid in (0, 13):
            self.assertEqual(
                self.normalizer.normalize("FY 2026", fiscal_year_end_month=invalid)
                .issues[0]
                .code,
                "period.invalid_fiscal_year_end_month",
            )

    def test_comparability_uses_period_structure_not_absolute_days(self):
        february = self.normalizer.normalize("February 2026").period
        march = self.normalizer.normalize("March 2026").period
        self.assertTrue(self.normalizer.comparable(february, march).comparable)
        self.assertTrue(
            self.normalizer.comparable(
                self.normalizer.normalize("Q1 2026").period,
                self.normalizer.normalize("Q2 2026").period,
            ).comparable
        )
        self.assertTrue(
            self.normalizer.comparable(
                self.normalizer.normalize("Year 2024").period,
                self.normalizer.normalize("Year 2025").period,
            ).comparable
        )
        self.assertFalse(
            self.normalizer.comparable(
                self.normalizer.normalize("Q1 YTD 2026").period,
                self.normalizer.normalize("Q2 YTD 2026").period,
            ).comparable
        )
        fiscal = self.normalizer.normalize("FY 2026", fiscal_year_end_month=6).period
        civil = self.normalizer.normalize("Year 2026").period
        self.assertFalse(self.normalizer.comparable(fiscal, civil).comparable)
        fiscal_march = self.normalizer.normalize(
            "FY 2026", fiscal_year_end_month=3
        ).period
        self.assertFalse(self.normalizer.comparable(fiscal, fiscal_march).comparable)


class NumberAndResolutionHardeningTests(unittest.TestCase):
    def setUp(self):
        self.parser = LocaleNumberParser()
        self.normalizer = FinancialNormalizer()

    def source(self, *, currency="EUR", scale="raw"):
        return SourceMetadata(
            SOURCE_ID,
            HASH,
            LanguageCode.EN,
            LocaleTag.EN_GB,
            currency=currency,
            scale=scale,
            accounting_parentheses=True,
        )

    def row(self, text, **changes):
        values = dict(
            name="Revenue",
            text=text,
            period_label="Year 2026",
            statement_type="income_statement",
            scope="consolidated",
            source_reference="sheet:1!row:1",
        )
        values.update(changes)
        return FinancialRow(**values)

    def test_official_locale_allowlist_precision_and_grouping(self):
        self.assertEqual(
            self.parser.parse("1,234.56789", locale=LocaleTag.EN_GB).value,
            Decimal("1234.56789"),
        )
        self.assertEqual(
            self.parser.parse("1\u00a0234,56789", locale=LocaleTag.FR_FR).value,
            Decimal("1234.56789"),
        )
        self.assertEqual(
            self.parser.parse("1 234,56789", locale=LocaleTag.PT_PT).value,
            Decimal("1234.56789"),
        )
        self.assertIsNone(self.parser.parse("12,34.5", locale=LocaleTag.EN_GB).value)
        self.assertIsNone(self.parser.parse("1,23,456.7", locale=LocaleTag.EN_GB).value)
        self.assertEqual(
            self.parser.parse("1,234.5", locale="en-US").issues[0].code,
            "number.locale_required",
        )

    def test_ambiguous_dollar_requires_context(self):
        ambiguous = self.parser.parse("$ 10.25", locale=LocaleTag.EN_GB)
        self.assertEqual(ambiguous.value, Decimal("10.25"))
        self.assertIsNone(ambiguous.currency)
        self.assertEqual(ambiguous.issues[0].code, "currency.ambiguous_symbol")
        self.assertEqual(
            self.parser.parse(
                "$ 10.25", locale=LocaleTag.EN_GB, currency_hint="CAD"
            ).currency,
            "CAD",
        )
        observation = self.normalizer.normalize(
            [self.row("$ 10.25")], self.source(currency="EUR")
        )[0]
        self.assertIsNone(observation.currency)
        self.assertEqual(observation.normalization_status, "needs_confirmation")
        explicit_conflict = self.normalizer.normalize(
            [self.row("USD EUR 10.25")], self.source(currency="EUR")
        )[0]
        self.assertIsNone(explicit_conflict.currency)

    def test_explicit_dollar_codes_disambiguate_only_compatible_evidence(self):
        for code in ("USD", "CAD", "AUD", "NZD"):
            with self.subTest(code=code):
                parsed = self.parser.parse(f"{code} $10.25", locale="en-GB")
                self.assertEqual(parsed.currency, code)
                self.assertFalse(parsed.issues)
        incompatible = self.parser.parse("EUR $10.25", locale="en-GB")
        self.assertEqual(incompatible.currency, "EUR")
        self.assertIn(
            "currency.ambiguous_symbol", {issue.code for issue in incompatible.issues}
        )
        unresolved = self.normalizer.normalize(
            [self.row("EUR $10.25")], self.source(currency=None)
        )[0]
        self.assertIsNone(unresolved.currency)
        self.assertEqual(unresolved.normalization_status, "needs_confirmation")
        conflicting = self.parser.parse("USD CAD $10.25", locale="en-GB")
        self.assertIsNone(conflicting.currency)
        self.assertIn("currency.conflict", {issue.code for issue in conflicting.issues})
        hinted = self.parser.parse("USD $10.25", locale="en-GB", currency_hint="CAD")
        self.assertIsNone(hinted.currency)
        self.assertIn("currency.conflict", {issue.code for issue in hinted.issues})

    def test_currency_conflicts_compare_text_row_and_source(self):
        for row_currency, source_currency in (
            ("USD", "EUR"),
            ("EUR", "USD"),
            ("GBP", "EUR"),
        ):
            observation = self.normalizer.normalize(
                [self.row("USD 10", currency=row_currency)],
                self.source(currency=source_currency),
            )[0]
            self.assertIsNone(observation.currency)
            self.assertIn(
                "currency.conflict", {issue.code for issue in observation.issues}
            )
            self.assertEqual(observation.normalization_status, "needs_confirmation")

    def test_offline_iso_currency_recognition_and_unknown_code_rejection(self):
        for code in ("JPY", "CHF", "CNY", "XOF"):
            with self.subTest(code=code):
                parsed = self.parser.parse(f"{code} 1,000", locale=LocaleTag.EN_GB)
                self.assertEqual(parsed.value, Decimal("1000"))
                self.assertEqual(parsed.currency, code)
                self.assertFalse(parsed.issues)
        unknown = self.parser.parse("ZZZ 1,000", locale=LocaleTag.EN_GB)
        self.assertEqual(unknown.value, Decimal("1000"))
        self.assertIsNone(unknown.currency)
        self.assertEqual(unknown.issues[0].code, "currency.unknown")
        conflict = self.parser.parse("JPY CHF 1,000", locale=LocaleTag.EN_GB)
        self.assertIsNone(conflict.currency)
        self.assertIn("currency.conflict", {issue.code for issue in conflict.issues})

    def test_unknown_text_currency_is_not_replaced_by_metadata(self):
        for row_currency, source_currency in (
            ("EUR", None),
            (None, "EUR"),
            (None, None),
        ):
            with self.subTest(
                row_currency=row_currency, source_currency=source_currency
            ):
                observation = self.normalizer.normalize(
                    [self.row("ZZZ 10", currency=row_currency)],
                    self.source(currency=source_currency),
                )[0]
                self.assertIsNone(observation.currency)
                self.assertIn(
                    "currency.unknown", {issue.code for issue in observation.issues}
                )
                self.assertEqual(observation.normalization_status, "needs_confirmation")

        known = self.normalizer.normalize(
            [self.row("JPY 10")], self.source(currency="JPY")
        )[0]
        self.assertEqual(known.currency, "JPY")

    def test_scale_conflicts_do_not_expand_value(self):
        observation = self.normalizer.normalize(
            [self.row("10 million", scale="thousands")], self.source(scale="raw")
        )[0]
        self.assertEqual(observation.value, Decimal("10"))
        self.assertIsNone(observation.scale)
        self.assertIn("scale.conflict", {issue.code for issue in observation.issues})

    def test_percentage_and_null_never_inherit_currency_or_scale(self):
        percentage = self.normalizer.normalize(
            [self.row("12.345%")], self.source(currency="EUR", scale="millions")
        )[0]
        missing = self.normalizer.normalize(
            [self.row("")], self.source(currency="EUR", scale="millions")
        )[0]
        self.assertEqual(percentage.value, Decimal("12.345"))
        self.assertIsNone(percentage.currency)
        self.assertIsNone(percentage.scale)
        self.assertEqual(percentage.unit, "percentage")
        self.assertEqual(percentage.normalization_status, "needs_confirmation")
        self.assertIn(
            "unit.percentage_monetary_conflict",
            {issue.code for issue in percentage.issues},
        )
        self.assertNotIn(
            "currency.missing", {issue.code for issue in percentage.issues}
        )
        self.assertEqual(missing.null_reason, NullReason.MISSING)
        self.assertIsNone(missing.currency)
        self.assertIsNone(missing.scale)

    def test_percentage_ratio_operands_are_explicit_validated_and_hashed(self):
        missing = self.normalizer.normalize(
            [self.row("25%")], self.source(currency=None, scale=None)
        )[0]
        self.assertFalse(missing.ratio_complete)
        self.assertIn("ratio.operands_required", {i.code for i in missing.issues})

        complete = self.normalizer.normalize(
            [self.row("25%", numerator=Decimal("1"), denominator=Decimal("4"))],
            self.source(currency=None, scale=None),
        )[0]
        self.assertTrue(complete.ratio_complete)
        self.assertEqual(
            (complete.numerator, complete.denominator),
            (Decimal("1"), Decimal("4")),
        )
        # Revenue remains protected from percentage consumption even with a valid base.
        self.assertEqual(complete.normalization_status, "needs_confirmation")
        self.assertIn(
            "unit.percentage_monetary_conflict", {i.code for i in complete.issues}
        )

        different_base = self.normalizer.normalize(
            [self.row("25%", numerator=Decimal("2"), denominator=Decimal("8"))],
            self.source(currency=None, scale=None),
        )[0]
        self.assertNotEqual(complete.semantic_hash, different_base.semantic_hash)
        zero = self.normalizer.normalize(
            [self.row("25%", numerator=Decimal("1"), denominator=Decimal("0"))],
            self.source(currency=None, scale=None),
        )[0]
        self.assertFalse(zero.ratio_complete)
        self.assertIn("ratio.denominator_zero", {i.code for i in zero.issues})

    def test_locale_specific_scales(self):
        cases = (
            ("2 mil", LocaleTag.PT_PT, "thousands"),
            ("2 million", LocaleTag.EN_GB, "millions"),
            ("2 millones", LocaleTag.ES_ES, "millions"),
            ("2 mille", LocaleTag.FR_FR, "thousands"),
            ("2 millions", LocaleTag.FR_FR, "millions"),
        )
        for text, locale, expected in cases:
            self.assertEqual(self.parser.parse(text, locale=locale).scale, expected)


class DomainAndTerminologyHardeningTests(unittest.TestCase):
    def observation(self):
        return NormalizedObservation(
            "revenue",
            "monetary_flow",
            Decimal("1"),
            None,
            "EUR",
            "raw",
            "monetary",
            "positive",
            "credit",
            Period(
                date(2026, 1, 1),
                date(2026, 12, 31),
                granularity="year",
                comparison_basis="current",
            ),
            "income_statement",
            "consolidated",
            "Revenue",
            "1",
            SOURCE_ID,
            HASH,
            "sheet:1!cell:B2",
            "en",
            "en-GB",
            Decimal("1"),
        )

    def test_observation_and_issue_invariants(self):
        base = self.observation()
        for changes in (
            {"source_content_hash": "z" * 64},
            {"confidence": Decimal("1.1")},
            {"currency": "eur"},
            {"dimensions": (("segment", "a"), ("segment", "b"))},
            {"parser_version": ""},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(base, **changes)
        for invalid_value in (1, "1", 1.0, True, object()):
            with self.subTest(value=invalid_value), self.assertRaises(TypeError):
                replace(base, value=invalid_value)
        self.assertEqual(replace(base, value=Decimal("2")).value, Decimal("2"))
        with self.assertRaises(ValueError):
            NormalizationIssue("", "value")
        with self.assertRaises(ValueError):
            NormalizationIssue("number.invalid", "")
        with self.assertRaises(ValueError):
            replace(base, source_reference=" ")

    def test_source_reference_participates_in_semantic_identity(self):
        base = self.observation()
        other = replace(base, source_reference="sheet:1!cell:B3")
        self.assertNotEqual(base.semantic_hash, other.semantic_hash)

    def test_dimension_order_is_canonical_in_semantic_identity(self):
        dimensions = (("region", "north"), ("segment", "retail"))
        first = replace(self.observation(), dimensions=dimensions)
        second = replace(self.observation(), dimensions=tuple(reversed(dimensions)))
        self.assertEqual(first.dimensions, second.dimensions)
        self.assertEqual(first.semantic_hash, second.semantic_hash)

    def test_every_declared_concept_has_complete_canonical_multilingual_aliases(self):
        registry = TerminologyRegistry()
        self.assertEqual(len(FINANCIAL_TERMS), 17)
        for entry in FINANCIAL_TERMS:
            self.assertEqual(set(entry.labels), set(LanguageCode))
            self.assertEqual(set(entry.aliases), set(LanguageCode))
            if entry.concept_id == "operating_profit_reported_unconfirmed":
                self.assertEqual(
                    entry.reason_code,
                    "mapping.operating_profit_requires_confirmation",
                )
                self.assertLess(entry.confidence, Decimal("1"))
            elif entry.concept_id == "ebitda_reported_unverified":
                self.assertEqual(
                    entry.reason_code,
                    "mapping.ebitda_requires_confirmation",
                )
                self.assertLess(entry.confidence, Decimal("1"))
            else:
                self.assertEqual(entry.reason_code, "mapping.alias_exact")
                self.assertEqual(entry.confidence, Decimal("1"))
            for language in LanguageCode:
                self.assertTrue(entry.labels[language])
                self.assertTrue(entry.aliases[language])
                for alias in entry.aliases[language]:
                    self.assertEqual(
                        registry.lookup(
                            alias, language, entry.statement_type
                        ).concept_id,
                        entry.concept_id,
                    )
                    wrong_statement = (
                        "balance_sheet"
                        if entry.statement_type != "balance_sheet"
                        else "income_statement"
                    )
                    self.assertIsNone(registry.lookup(alias, language, wrong_statement))

    def test_duplicate_alias_in_same_language_and_statement_is_rejected(self):
        first = FINANCIAL_TERMS[0]
        duplicate = TerminologyEntry(
            "other_revenue",
            "Other",
            {language: "Other" for language in LanguageCode},
            {
                **{language: ("other",) for language in LanguageCode},
                LanguageCode.EN: (first.aliases[LanguageCode.EN][0],),
            },
            first.statement_type,
            "monetary_flow",
            "credit",
        )
        with self.assertRaisesRegex(ValueError, "ambiguous terminology alias"):
            TerminologyRegistry((first, duplicate))

    def test_same_concept_duplicate_alias_requires_identical_metadata(self):
        first = FINANCIAL_TERMS[0]
        identical = replace(first)
        for entries in ((first, identical), (identical, first)):
            registry = TerminologyRegistry(entries)
            self.assertEqual(
                registry.lookup("Revenue", LanguageCode.EN, first.statement_type),
                first,
            )
        for field, value in (
            ("semantic_type", "reported_unverified"),
            ("expected_sign", "contextual"),
            ("confidence", Decimal("0.5")),
            ("reason_code", "mapping.different"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "conflicting terminology alias metadata"
            ):
                TerminologyRegistry((first, replace(first, **{field: value})))


class OperatingProfitDistinctionTests(unittest.TestCase):
    LABELS = {
        LanguageCode.PT: "Resultado operacional",
        LanguageCode.EN: "Operating profit",
        LanguageCode.ES: "Resultado operativo",
        LanguageCode.FR: "Résultat opérationnel",
    }
    LOCALES = {
        LanguageCode.PT: LocaleTag.PT_PT,
        LanguageCode.EN: LocaleTag.EN_GB,
        LanguageCode.ES: LocaleTag.ES_ES,
        LanguageCode.FR: LocaleTag.FR_FR,
    }

    def test_explicit_ebit_is_deterministic_in_all_languages(self):
        registry = TerminologyRegistry()
        for language in LanguageCode:
            with self.subTest(language=language):
                entry = registry.lookup("EBIT", language, "income_statement")
                self.assertEqual(entry.concept_id, "ebit")
                self.assertEqual(entry.reason_code, "mapping.alias_exact")
                self.assertEqual(entry.confidence, Decimal("1"))

    def test_ebitda_enters_confirmation_without_becoming_ebit(self):
        source = SourceMetadata(
            SOURCE_ID,
            HASH,
            LanguageCode.EN,
            LocaleTag.EN_GB,
            currency="EUR",
            accounting_parentheses=True,
        )
        observation = FinancialNormalizer().normalize(
            [
                FinancialRow(
                    "EBITDA",
                    "EUR 74",
                    "Year 2026",
                    "income_statement",
                    "consolidated",
                    source_reference="sheet:1!row:ebitda",
                    scale="raw",
                )
            ],
            source,
        )[0]
        self.assertEqual(observation.concept_id, "ebitda_reported_unverified")
        self.assertEqual(observation.semantic_type, "reported_unverified")
        self.assertEqual(observation.unit, "monetary")
        self.assertEqual(observation.normalization_status, "needs_confirmation")
        self.assertIn(
            "mapping.ebitda_requires_confirmation",
            {issue.code for issue in observation.issues},
        )

    def test_operating_profit_is_preserved_but_never_promoted_to_ebit(self):
        hashes = set()
        for language, label in self.LABELS.items():
            with self.subTest(language=language):
                source = SourceMetadata(
                    SOURCE_ID,
                    HASH,
                    language,
                    self.LOCALES[language],
                    currency="EUR",
                    accounting_parentheses=True,
                )
                observation = FinancialNormalizer().normalize(
                    [
                        FinancialRow(
                            label,
                            "EUR 74",
                            "Year 2026",
                            "income_statement",
                            "consolidated",
                            source_reference="sheet:1!row:1",
                            scale="raw",
                        )
                    ],
                    source,
                )[0]
                self.assertNotEqual(observation.concept_id, "ebit")
                self.assertEqual(
                    observation.concept_id,
                    "operating_profit_reported_unconfirmed",
                )
                self.assertEqual(
                    observation.semantic_type,
                    "reported_operating_profit_unconfirmed",
                )
                self.assertEqual(observation.normalization_status, "needs_confirmation")
                self.assertLess(observation.confidence, Decimal("1"))
                self.assertIn(
                    "mapping.operating_profit_requires_confirmation",
                    {issue.code for issue in observation.issues},
                )
                hashes.add(observation.semantic_hash)
        self.assertEqual(len(hashes), 1)

    def test_operating_profit_rejects_incompatible_statement_context(self):
        registry = TerminologyRegistry()
        for language, label in self.LABELS.items():
            self.assertIsNone(registry.lookup(label, language, "balance_sheet"))


if __name__ == "__main__":
    unittest.main()
