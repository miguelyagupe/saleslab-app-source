import inspect
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest
from uuid import UUID

from saleslab_v2.domain.enums import LanguageCode, NullReason
from saleslab_v2.domain.language import LocaleTag
from saleslab_v2.i18n.catalog import FINANCIAL_TERMS, TerminologyRegistry
from saleslab_v2.normalization.financial import (
    FinancialNormalizer,
    FinancialRow,
    SourceMetadata,
    validate_aggregation_scope,
)
from saleslab_v2.normalization.mapping import SemanticMapper
from saleslab_v2.normalization.numbers import LocaleNumberParser
from saleslab_v2.normalization.periods import PeriodNormalizer

ROOT = Path(__file__).parent
SOURCE_ID = UUID("11111111-1111-4111-8111-111111111111")
HASH = "a" * 64


class NumberParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = LocaleNumberParser()

    def test_locale_separators_spaces_nbsp_currency_percent_and_scale(self):
        cases = [
            ("1.234,56 €", "pt-PT", Decimal("1234.56"), "EUR"),
            ("USD 1,234.56", "en-GB", Decimal("1234.56"), "USD"),
            ("1 234,56 EUR", "fr-FR", Decimal("1234.56"), "EUR"),
            ("1\u00a0234,56", "fr-FR", Decimal("1234.56"), None),
            ("12,5%", "es-ES", Decimal("12.5"), None),
            ("2 millones", "es-ES", Decimal("2"), None),
        ]
        for text, locale, expected, currency in cases:
            with self.subTest(text=text):
                result = self.parser.parse(text, locale=locale)
                self.assertIs(type(result.value), Decimal)
                self.assertEqual(result.value, expected)
                self.assertEqual(result.currency, currency)
        self.assertTrue(self.parser.parse("12,5%", locale="pt-PT").is_percentage)
        self.assertEqual(
            self.parser.parse("2 millions", locale="fr-FR").scale, "millions"
        )

    def test_sign_zero_and_parentheses_are_explicit(self):
        self.assertEqual(
            self.parser.parse("-0,5", locale="pt-PT").value, Decimal("-0.5")
        )
        self.assertEqual(self.parser.parse("0", locale="en-GB").value, Decimal("0"))
        ambiguous = self.parser.parse("(25)", locale="en-GB")
        self.assertEqual(ambiguous.null_reason, NullReason.PARSE_ERROR)
        self.assertEqual(ambiguous.issues[0].code, "number.parentheses_sign_ambiguous")
        self.assertEqual(
            self.parser.parse(
                "(25)", locale="en-GB", accounting_parentheses=True
            ).value,
            Decimal("-25"),
        )

    def test_ambiguous_or_incompatible_numbers_fail(self):
        for text, locale in (("1.23.4,5", "pt-PT"), ("1,23,4.5", "en-GB")):
            with self.subTest(text=text):
                result = self.parser.parse(text, locale=locale)
                self.assertIsNone(result.value)
                self.assertEqual(result.null_reason, NullReason.PARSE_ERROR)
                self.assertTrue(result.issues)
        self.assertEqual(
            self.parser.parse("1.234", locale=None).issues[0].code,
            "number.locale_required",
        )

    def test_percentage_sign_must_be_a_single_suffix(self):
        valid = self.parser.parse("10%", locale="en-GB")
        self.assertEqual(valid.value, Decimal("10"))
        self.assertTrue(valid.is_percentage)
        for text in ("1%0", "%10", "10%%"):
            with self.subTest(text=text):
                result = self.parser.parse(text, locale="en-GB")
                self.assertIsNone(result.value)
                self.assertEqual(result.null_reason, NullReason.PARSE_ERROR)
                self.assertEqual(result.issues[-1].code, "number.invalid_percentage")

    def test_null_reasons_remain_distinct(self):
        values = {
            text: self.parser.parse(text, locale="pt-PT").null_reason
            for text in ("", "não aplicável", "não divulgado", "não encontrado", "abc")
        }
        self.assertEqual(
            set(values.values()),
            {
                NullReason.MISSING,
                NullReason.NOT_APPLICABLE,
                NullReason.REDACTED,
                NullReason.UNKNOWN,
                NullReason.PARSE_ERROR,
            },
        )


class MappingAndPeriodTests(unittest.TestCase):
    def test_versioned_deterministic_aliases_and_stable_ids(self):
        registry = TerminologyRegistry()
        self.assertEqual(registry.version, "financial-terminology-v1")
        self.assertEqual(
            len({entry.concept_id for entry in FINANCIAL_TERMS}), len(FINANCIAL_TERMS)
        )
        mapper = SemanticMapper(registry)
        results = [
            mapper.map(label, language=language, statement_type="income_statement")
            for language, label in (
                (LanguageCode.PT, "Receitas"),
                (LanguageCode.EN, "Revenue"),
                (LanguageCode.ES, "Ingresos"),
                (LanguageCode.FR, "Chiffre d’affaires"),
            )
        ]
        self.assertEqual({result.entry.concept_id for result in results}, {"revenue"})
        self.assertEqual(
            {result.reason_code for result in results}, {"mapping.alias_exact"}
        )

    def test_unknown_ambiguous_and_context_conflict_are_unresolved(self):
        mapper = SemanticMapper()
        self.assertEqual(
            mapper.map("Result", language=LanguageCode.EN).reason_code,
            "mapping.ambiguous_label",
        )
        self.assertEqual(
            mapper.map("Mystery", language=LanguageCode.EN).reason_code,
            "mapping.unknown_label",
        )
        self.assertEqual(
            mapper.map(
                "Revenue", language=LanguageCode.EN, statement_type="balance_sheet"
            ).reason_code,
            "mapping.statement_context_conflict",
        )

    def test_ebitda_is_reported_unverified_in_every_supported_language(self):
        mapper = SemanticMapper()
        for language in LanguageCode:
            with self.subTest(language=language):
                result = mapper.map(
                    "EBITDA", language=language, statement_type="income_statement"
                )
                self.assertEqual(result.entry.concept_id, "ebitda_reported_unverified")
                self.assertEqual(result.entry.semantic_type, "reported_unverified")
                self.assertEqual(result.entry.labels[language], "EBITDA")
                self.assertEqual(
                    result.reason_code, "mapping.ebitda_requires_confirmation"
                )
                self.assertTrue(result.issues)

    def test_period_kinds_fiscal_and_comparability(self):
        normalizer = PeriodNormalizer()
        month = normalizer.normalize("March 2026").period
        quarter = normalizer.normalize("Q1 2026").period
        year = normalizer.normalize("Year 2026").period
        ytd = normalizer.normalize("YTD September 2026").period
        fiscal = normalizer.normalize("FY 2026", fiscal_year_end_month=6).period
        self.assertEqual(
            [month.granularity, quarter.granularity, year.granularity, ytd.granularity],
            ["month", "quarter", "year", "ytd"],
        )
        self.assertEqual(
            (fiscal.start_date, fiscal.end_date), (date(2025, 7, 1), date(2026, 6, 30))
        )
        self.assertFalse(normalizer.comparable(month, quarter).comparable)
        self.assertFalse(normalizer.comparable(ytd, year).comparable)
        self.assertTrue(
            normalizer.comparable(
                quarter, normalizer.normalize("Q2 2026").period
            ).comparable
        )
        self.assertEqual(
            normalizer.normalize("FY 2026").issues[0].code,
            "period.fiscal_calendar_required",
        )

    def test_quarter_and_month_evidence_must_be_consistent(self):
        normalizer = PeriodNormalizer()
        for label in (
            "Q1 abril 2026",
            "Q1 April 2026",
            "T1 abril 2026",
            "T1 avril 2026",
        ):
            with self.subTest(label=label):
                result = normalizer.normalize(label)
                self.assertIsNone(result.period)
                self.assertEqual(
                    result.issues[0].code, "period.quarter_month_ambiguous"
                )
        for label in (
            "Q2 abril 2026",
            "Q2 April 2026",
            "T2 abril 2026",
            "T2 avril 2026",
        ):
            with self.subTest(label=label):
                self.assertIsNotNone(normalizer.normalize(label).period)
        self.assertIsNotNone(
            normalizer.normalize("FY Q1 juillet 2026", fiscal_year_end_month=6).period
        )
        fiscal_conflict = normalizer.normalize(
            "FY Q1 avril 2026", fiscal_year_end_month=6
        )
        self.assertIsNone(fiscal_conflict.period)
        self.assertEqual(
            fiscal_conflict.issues[0].code, "period.quarter_month_ambiguous"
        )


class FinancialNormalizerTests(unittest.TestCase):
    def source(self, language=LanguageCode.EN, locale=LocaleTag.EN_GB):
        return SourceMetadata(
            SOURCE_ID,
            HASH,
            language,
            locale,
            currency="EUR",
            accounting_parentheses=True,
        )

    def test_reviewed_golden_outputs_are_economically_language_invariant(self):
        fixture = json.loads(
            (ROOT / "fixtures/saleslab_v2/f02_financial_golden.json").read_text()
        )
        self.assertEqual(
            fixture["declared_concepts"],
            [entry.concept_id for entry in FINANCIAL_TERMS],
        )
        self.assertEqual(
            {
                language: tuple(labels)
                for language, labels in fixture["month_labels"].items()
            },
            {
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
            },
        )
        normalizer = FinancialNormalizer()
        expected = [tuple(item) for item in fixture["expected"]]
        semantic_outputs = []
        for language, data in fixture["locales"].items():
            rows = [
                FinancialRow(
                    name,
                    text,
                    "Year 2026",
                    statement,
                    "consolidated",
                    source_reference=source_reference,
                )
                for name, text, statement, source_reference in data["rows"]
            ]
            observations = normalizer.normalize(
                rows, self.source(LanguageCode(language), LocaleTag(data["locale"]))
            )
            actual = [
                (item.concept_id, str(item.value), item.statement_type)
                for item in observations
            ]
            self.assertEqual(actual, expected)
            semantic_outputs.append(
                [
                    (
                        o.concept_id,
                        o.value,
                        o.period.start_date,
                        o.period.end_date,
                        o.scope,
                    )
                    for o in observations
                ]
            )
        self.assertTrue(
            all(output == semantic_outputs[0] for output in semantic_outputs)
        )

    def test_preserves_original_source_hash_and_normalizes_scale(self):
        observation = FinancialNormalizer().normalize(
            [
                FinancialRow(
                    "Revenue",
                    "€ 1.5 million",
                    "Year 2026",
                    "income_statement",
                    "consolidated",
                    source_reference="sheet:1!cell:B2",
                )
            ],
            self.source(),
        )[0]
        self.assertEqual(observation.value, Decimal("1500000.0"))
        self.assertEqual(observation.scale, "millions")
        self.assertEqual(
            (observation.original_name, observation.original_text),
            ("Revenue", "€ 1.5 million"),
        )
        self.assertEqual(
            (observation.source_document_id, observation.source_content_hash),
            (SOURCE_ID, HASH),
        )
        self.assertEqual(observation.source_reference, "sheet:1!cell:B2")

    def test_missing_currency_is_not_inferred_from_language(self):
        source = SourceMetadata(
            SOURCE_ID,
            HASH,
            LanguageCode.PT,
            LocaleTag.PT_PT,
            accounting_parentheses=True,
        )
        observation = FinancialNormalizer().normalize(
            [
                FinancialRow(
                    "Receitas",
                    "10",
                    "Ano 2026",
                    "income_statement",
                    "consolidated",
                    source_reference="sheet:1!row:1",
                )
            ],
            source,
        )[0]
        self.assertIsNone(observation.currency)
        self.assertIn("currency.missing", {issue.code for issue in observation.issues})

    def test_scope_isolation_and_unsafe_aggregation_rejected(self):
        normalizer = FinancialNormalizer()
        source = replace(self.source(), scale="raw")
        rows = [
            FinancialRow(
                "Revenue",
                "10",
                "Year 2026",
                "income_statement",
                "consolidated",
                source_reference="sheet:1!row:1",
            ),
            FinancialRow(
                "Revenue",
                "4",
                "Year 2026",
                "income_statement",
                "segment",
                source_reference="sheet:1!row:2",
                dimensions=(("segment", "north"),),
            ),
        ]
        observations = normalizer.normalize(rows, source)
        with self.assertRaisesRegex(ValueError, "consolidated_segment"):
            validate_aggregation_scope(observations)
        with self.assertRaisesRegex(ValueError, "requires_matching_eliminations"):
            validate_aggregation_scope(observations[1:])
        eliminations = normalizer.normalize(
            [
                FinancialRow(
                    "Revenue",
                    "-1",
                    "Year 2026",
                    "income_statement",
                    "eliminations",
                    source_reference="sheet:1!row:3",
                    dimensions=(("segment", "north"),),
                )
            ],
            source,
        )
        validate_aggregation_scope(observations[1:] + eliminations)

        unrelated = normalizer.normalize(
            [
                FinancialRow(
                    "Capital expenditure",
                    "-1",
                    "Year 2025",
                    "cash_flow_statement",
                    "eliminations",
                    source_reference="sheet:1!row:4",
                    dimensions=(("segment", "north"),),
                )
            ],
            source,
        )
        with self.assertRaisesRegex(ValueError, "matching_eliminations"):
            validate_aggregation_scope(observations[1:] + unrelated)

    def test_duplicate_canonical_candidates_require_confirmation_with_provenance(self):
        observations = FinancialNormalizer().normalize(
            [
                FinancialRow(
                    name,
                    value,
                    "Year 2026",
                    "income_statement",
                    "consolidated",
                    scale="raw",
                    dimensions=dimensions,
                    source_reference=reference,
                )
                for name, value, dimensions, reference in (
                    ("Revenue", "10", (), "sheet:1!row:1"),
                    ("Sales", "20", (), "sheet:1!row:2"),
                    ("Revenue", "30", (("region", "north"),), "sheet:1!row:3"),
                )
            ],
            self.source(),
        )
        for candidate in observations[:2]:
            self.assertEqual(candidate.normalization_status, "needs_confirmation")
            issue = candidate.issues[-1]
            self.assertEqual(issue.code, "observation.duplicate_candidate")
            self.assertEqual(dict(issue.details)["candidate_count"], "2")
        self.assertEqual(
            observations[0].issues[-1].details, observations[1].issues[-1].details
        )
        self.assertEqual(
            [item.source_reference for item in observations],
            ["sheet:1!row:1", "sheet:1!row:2", "sheet:1!row:3"],
        )
        self.assertEqual(observations[2].normalization_status, "normalized")

    def test_duplicate_candidate_identity_canonicalizes_dimension_order(self):
        common = (("region", "north"), ("segment", "retail"))
        observations = FinancialNormalizer().normalize(
            [
                FinancialRow(
                    "Revenue",
                    value,
                    "Year 2026",
                    "income_statement",
                    "consolidated",
                    scale="raw",
                    dimensions=dimensions,
                    source_reference=f"sheet:1!row:{index}",
                )
                for index, (value, dimensions) in enumerate(
                    (("10", common), ("20", tuple(reversed(common)))), start=1
                )
            ],
            self.source(),
        )
        self.assertTrue(
            all(
                item.normalization_status == "needs_confirmation"
                for item in observations
            )
        )
        self.assertTrue(
            all(
                "observation.duplicate_candidate"
                in {issue.code for issue in item.issues}
                for item in observations
            )
        )

        distinct = FinancialNormalizer().normalize(
            [
                FinancialRow(
                    "Revenue",
                    "10",
                    "Year 2026",
                    "income_statement",
                    "consolidated",
                    scale="raw",
                    dimensions=(("region", region),),
                    source_reference=f"sheet:1!row:{index}",
                )
                for index, region in enumerate(("north", "south"), start=3)
            ],
            self.source(),
        )
        self.assertTrue(
            all(item.normalization_status == "normalized" for item in distinct)
        )

    def test_semantic_hash_is_deterministic_and_original_label_independent(self):
        normalizer = FinancialNormalizer()
        first = normalizer.normalize(
            [
                FinancialRow(
                    "Revenue",
                    "10",
                    "Year 2026",
                    "income_statement",
                    "consolidated",
                    source_reference="sheet:1!row:1",
                )
            ],
            self.source(),
        )[0]
        second = normalizer.normalize(
            [
                FinancialRow(
                    "Sales",
                    "10",
                    "Year 2026",
                    "income_statement",
                    "consolidated",
                    source_reference="sheet:1!row:1",
                )
            ],
            self.source(),
        )[0]
        self.assertEqual(first.semantic_hash, second.semantic_hash)
        self.assertEqual(len(first.semantic_hash), 64)

    def test_no_external_or_model_dependencies(self):
        import saleslab_v2.normalization.financial as financial
        import saleslab_v2.normalization.mapping as mapping
        import saleslab_v2.normalization.numbers as numbers

        source = "\n".join(
            inspect.getsource(module) for module in (financial, mapping, numbers)
        )
        for forbidden in ("anthropic", "openai", "requests", "httpx", "urllib"):
            self.assertNotIn(forbidden, source.casefold())


if __name__ == "__main__":
    unittest.main()
