import json
from datetime import datetime, timezone
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

from saleslab_v2.config import V2Settings
from saleslab_v2.domain.enums import (
    LanguageCode,
    ModuleType,
    NullReason,
    SourceFormat,
    TrustLevel,
    WorkflowStatus,
)
from saleslab_v2.domain.idempotency import IdempotencyScope, canonicalize, semantic_hash
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun, Failure, StageResult
from saleslab_v2.domain.enums import StageStatus
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.domain.value_objects import (
    DimensionRef,
    Money,
    Period,
    TypedValue,
    Unit,
)
from saleslab_v2.i18n.catalog import MissingCriticalTranslation, TranslationCatalog


class FoundationTests(unittest.TestCase):
    def context(self, locale=LocaleTag.PT_PT):
        return LanguageContext(
            (LanguageCode.PT, LanguageCode.EN),
            LanguageCode.PT,
            LanguageCode.EN,
            locale,
            "IFRS",
        )

    def make_run(self):
        return AnalysisRun(
            uuid4(),
            uuid4(),
            ModuleType.FINANCIAL,
            "1",
            "1",
            self.context(),
            (uuid4(),),
            ("a" * 64,),
            "analysis",
            "key",
        )

    def test_config_safe_defaults_and_parsing(self):
        self.assertEqual(V2Settings.from_mapping({}), V2Settings())
        parsed = V2Settings.from_mapping(
            {"SALESLAB_V2_ENABLED": "true", "SALESLAB_V2_INTERNAL_WORKSPACES": "a, b"}
        )
        self.assertTrue(parsed.enabled)
        self.assertEqual(parsed.internal_workspaces, {"a", "b"})
        with self.assertRaises(ValueError):
            V2Settings.from_mapping({"SALESLAB_V2_ENABLED": "yes"})

    def test_enum_json_round_trip_and_unknown_rejected(self):
        self.assertEqual(
            ModuleType(json.loads(json.dumps(ModuleType.SALES.value))), ModuleType.SALES
        )
        with self.assertRaises(ValueError):
            ModuleType("invented")

    def test_money_decimal_scale_and_structural_currency(self):
        value = Money(Decimal("1.2300"), "EUR", "thousands")
        self.assertEqual(value.to_dict()["amount"], "1.2300")
        with self.assertRaises(TypeError):
            Money(1.2, "EUR")
        with self.assertRaises(ValueError):
            Money(Decimal("1"), "eur")
        with self.assertRaises(ValueError):
            Money(Decimal("1"), "EUR", "billions")

    def test_period_typed_value_and_display_identity(self):
        self.assertEqual(
            Period(date(2026, 1, 1), date(2026, 1, 1)).start_date, date(2026, 1, 1)
        )
        with self.assertRaises(ValueError):
            Period(date(2026, 2, 1), date(2026, 1, 1))
        self.assertEqual(
            TypedValue("decimal", null_reason=NullReason.MISSING).null_reason,
            NullReason.MISSING,
        )
        for kwargs in ({}, {"value": 1, "null_reason": NullReason.UNKNOWN}):
            with self.assertRaises(ValueError):
                TypedValue("decimal", **kwargs)
        self.assertEqual(
            Unit("pct", "ratio", "Percent").identity, Unit("pct", "ratio", "%").identity
        )
        self.assertEqual(
            DimensionRef("product", "1", "A").identity,
            DimensionRef("product", "1", "B").identity,
        )

    def test_language_locale_are_separate_and_hash_is_semantic(self):
        self.assertEqual(
            {item.value for item in LanguageCode}, {"pt", "en", "es", "fr"}
        )
        self.assertEqual(
            {item.value for item in LocaleTag}, {"pt-PT", "en-GB", "es-ES", "fr-FR"}
        )
        first, second = self.context(LocaleTag.PT_PT), self.context(LocaleTag.EN_GB)
        self.assertNotEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            semantic_hash(first.semantic_dict()), semantic_hash(second.semantic_dict())
        )

    def test_strict_i18n_and_multilingual_source(self):
        with self.assertRaises(MissingCriticalTranslation):
            TranslationCatalog(strict=True).resolve(
                "revenue", LanguageCode.FR, critical=True
            )
        source = SourceDocument(
            uuid4(),
            "x.csv",
            SourceFormat.CSV,
            "0" * 64,
            "1",
            TrustLevel.DECLARED,
            (LanguageCode.PT, LanguageCode.EN),
        )
        self.assertEqual(len(source.languages), 2)
        with self.assertRaises(ValueError):
            replace(source, content_hash="bad")

    def test_internal_run_state_machine_has_no_quota_state(self):
        run = self.make_run()
        self.assertEqual(run.status, WorkflowStatus.RECEIVED)
        self.assertNotIn("quota", run.__dict__)
        run = run.transition(WorkflowStatus.PARSING)
        self.assertIsNotNone(run.started_at)
        with self.assertRaises(ValueError):
            run.transition(WorkflowStatus.COMPLETED)
        failed = run.transition(WorkflowStatus.FAILED, Failure("parse", "bad input"))
        self.assertIsNotNone(failed.completed_at)
        with self.assertRaises(ValueError):
            failed.transition(WorkflowStatus.RECEIVED)

    def test_idempotency_scope_and_hash_determinism(self):
        self.assertNotEqual(
            IdempotencyScope("a", "run", "k"), IdempotencyScope("b", "run", "k")
        )
        self.assertEqual(
            semantic_hash({"b": 2, "a": 1}), semantic_hash({"a": 1, "b": 2})
        )
        run = self.make_run()
        self.assertEqual(len(run.request_fingerprint), 64)
        self.assertEqual(
            run.request_fingerprint,
            replace(
                run,
                language_context=replace(
                    run.language_context, format_locale=LocaleTag.FR_FR
                ),
            ).request_fingerprint,
        )
        self.assertNotEqual(
            run.request_fingerprint,
            replace(run, pipeline_version="2").request_fingerprint,
        )
        self.assertNotIn("format_locale", run.idempotency_payload())

    def test_semantic_canonicalization_supported_types_and_failures(self):
        identifier = uuid4()
        instant = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        payload = {
            "decimal": Decimal("1.2300"),
            "uuid": identifier,
            "date": date(2026, 7, 27),
            "instant": instant,
            "enum": ModuleType.COST,
            "sequence": (1, "a"),
        }
        self.assertEqual(
            semantic_hash(payload), semantic_hash(dict(reversed(list(payload.items()))))
        )
        self.assertNotEqual(
            semantic_hash(Decimal("1.0")), semantic_hash(Decimal("1.00"))
        )
        self.assertEqual(
            canonicalize(self.context())["format_locale"], {"$enum": "pt-PT"}
        )
        for invalid in (1.2, object()):
            with self.assertRaises(TypeError):
                semantic_hash(invalid)
        with self.assertRaises(ValueError):
            semantic_hash(datetime(2026, 1, 1))

    def test_run_and_stage_invariants(self):
        base = self.make_run()
        for changes in (
            {"schema_version": ""},
            {"idempotency_key": ""},
            {"input_hashes": ("bad",)},
            {"source_ids": (base.source_ids[0],) * 2},
            {"created_at": datetime(2026, 1, 1)},
            {"failure": Failure("x", "x")},
        ):
            with self.assertRaises(ValueError):
                replace(base, **changes)
        with self.assertRaises(ValueError):
            AnalysisRun(
                base.workspace_id,
                base.created_by_user_id,
                base.module,
                "1",
                "1",
                base.language_context,
                (),
                (),
                "fixture",
                "empty",
            )
        empty = AnalysisRun(
            base.workspace_id,
            base.created_by_user_id,
            base.module,
            "1",
            "1",
            base.language_context,
            (),
            (),
            "fixture",
            "empty",
            allow_empty_sources=True,
        )
        self.assertEqual(empty.source_ids, ())
        now = datetime.now(timezone.utc)
        completed = StageResult(
            base.run_id,
            "parse",
            "1",
            StageStatus.COMPLETED,
            "a" * 64,
            "b" * 64,
            "1",
            {"amount": 1},
            completed_at=now,
        )
        self.assertEqual(completed.status, StageStatus.COMPLETED)
        invalid_stages = (
            {"stage_name": ""},
            {"input_hash": "bad"},
            {"output_hash": None},
            {"completed_at": None},
            {"error": Failure("x", "x")},
            {"payload": {"x": 1.2}},
        )
        for changes in invalid_stages:
            with self.assertRaises((ValueError, TypeError)):
                replace(completed, **changes)
        with self.assertRaises(ValueError):
            StageResult(
                base.run_id,
                "parse",
                "1",
                StageStatus.FAILED,
                "a" * 64,
                None,
                "1",
                {},
                completed_at=now,
            )


if __name__ == "__main__":
    unittest.main()
