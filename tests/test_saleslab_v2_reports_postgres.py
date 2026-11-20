"""Real PostgreSQL compilation, immutable publication and concurrency tests."""

import asyncio
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from uuid import UUID, uuid4

import test_saleslab_v2_audit_postgres as audit_tests
from analyst_fixtures import RecordingModel
from audit_fixtures import RecordingCritic
from pipeline_fixtures import change_row, with_growth
from sqlalchemy import select

from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.audit.service import FinancialAuditor
from saleslab_v2.audit.workflow import FinancialReviewWorkflow
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    MetricObservationModel,
    StageResultModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.reports.contracts import InvalidReport, ReportParityError
from saleslab_v2.reports.render import verify_artifacts
from saleslab_v2.reports.service import FinancialReportCompiler


@unittest.skipUnless(
    audit_tests.URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class ReportPostgresTests(unittest.TestCase):
    def test_legacy_publication_replays_identical_artifacts_after_renderer_upgrade(
        self,
    ):
        async def scenario(version):
            _, settings, _, _, _, _, args = await self.ready(
                growth=True, report_language="pt"
            )
            async with self.sessions.begin() as session:
                with patch("saleslab_v2.reports.service.RENDER_VERSION", version):
                    legacy = await FinancialReportCompiler(session, settings).execute(
                        **args
                    )
            async with self.sessions.begin() as session:
                replay = await FinancialReportCompiler(session, settings).execute(
                    **args
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.payload, legacy.payload)
                self.assertEqual(replay.artifacts, legacy.artifacts)
                self.assertEqual(replay.payload["render_version"], version)

        for version in ("financial-render-v1", "financial-render-v2"):
            with self.subTest(version=version):
                asyncio.run(scenario(version))

    def test_new_report_locale_follows_language_instead_of_source_number_format(self):
        async def scenario():
            _, settings, _, _, _, _, args = await self.ready(
                growth=True, report_language="pt"
            )
            async with self.sessions.begin() as session:
                run = await session.get(AnalysisRunModel, args["run_id"])
                self.assertEqual(run.language_context["report_language"], "pt")
                run.language_context = {
                    **run.language_context,
                    "format_locale": "en-GB",
                }
                result = await FinancialReportCompiler(session, settings).execute(
                    **args
                )
                self.assertEqual(result.payload["locale"], "pt-PT")
                self.assertEqual(
                    result.payload["render_version"], "financial-render-v3"
                )
                self.assertIn("25,00 %", result.artifacts["html"])
                verify_artifacts(result.payload, result.artifacts)

        asyncio.run(scenario())

    def test_rehashed_audit_identity_and_original_draft_binding_cannot_be_reassigned(
        self,
    ):
        async def scenario():
            _, settings, checkpoint, _, _, _, args = await self.ready()
            for key in ("audit_id", "draft_stage_id", "run_id"):
                async with self.sessions() as session, session.begin():
                    stage = await session.get(StageResultModel, args["audit_id"])
                    forged = deepcopy(stage.payload)
                    forged[key] = str(uuid4())
                    stage.payload, stage.output_hash = forged, semantic_hash(forged)
                    await session.flush()
                    with self.assertRaises(InvalidReport):
                        await FinancialReportCompiler(session, settings).execute(**args)
                    self.assertEqual(await self.state(session, checkpoint), "compiling")
                    await session.rollback()

        asyncio.run(scenario())

    def test_persistence_failure_after_report_write_rolls_back_all_publication_stages(
        self,
    ):
        async def scenario():
            _, settings, checkpoint, _, _, _, args = await self.ready()
            original = V2Repository.persist_stage

            async def fail_render(repository, stage):
                if stage.stage_name == "financial_report_render":
                    raise RuntimeError("synthetic storage failure")
                return await original(repository, stage)

            async with self.sessions.begin() as session:
                with patch.object(
                    V2Repository, "persist_stage", fail_render
                ), self.assertRaises(RuntimeError):
                    await FinancialReportCompiler(session, settings).execute(**args)
                rows = (
                    await session.scalars(
                        select(StageResultModel).where(
                            StageResultModel.run_id == checkpoint.run_id,
                            StageResultModel.stage_name.like("financial_report%"),
                        )
                    )
                ).all()
                self.assertEqual(rows, [])
                self.assertEqual(await self.state(session, checkpoint), "compiling")
                result = await FinancialReportCompiler(session, settings).execute(
                    **args
                )
                self.assertFalse(result.replayed)
                self.assertEqual(await self.state(session, checkpoint), "completed")

        asyncio.run(scenario())

    @classmethod
    def setUpClass(cls):
        audit_tests.AuditPostgresTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        audit_tests.AuditPostgresTests.tearDownClass.__func__(cls)

    setup_review = audit_tests.AuditPostgresTests.setup_review
    args = audit_tests.AuditPostgresTests.args
    state = audit_tests.AuditPostgresTests.state

    async def ready(
        self,
        *,
        warning=False,
        growth=False,
        quality_warning=False,
        report_language=None
    ):
        def transform(value):
            if report_language:
                value = replace(
                    value,
                    language=replace(
                        value.language,
                        report_language=type(value.language.report_language)(
                            report_language
                        ),
                    ),
                )
            if growth:
                value = with_growth(value)
            if quality_warning:
                value = change_row(value, "assets", text="100.5")
            return value

        value, settings, checkpoint, draft, model = await self.setup_review(transform)
        settings = replace(settings, financial_report_enabled=True)
        critic = RecordingCritic(
            (lambda raw, ctx: audit_tests.judgment(raw, "scope", "warning"))
            if warning
            else None
        )
        async with self.sessions.begin() as session:
            audit = await FinancialAuditor(session, settings, critic).execute(
                **self.args(value, checkpoint)
            )
        args = {
            **self.args(value, checkpoint),
            "audit_id": UUID(audit.payload["audit_id"]),
        }
        return value, settings, checkpoint, draft, model, critic, args

    def test_full_pipeline_compiles_both_channels_completes_and_replays_without_models(
        self,
    ):
        async def scenario():
            _, settings, checkpoint, draft, model, critic, args = await self.ready(
                growth=True
            )
            async with self.sessions.begin() as session:
                result = await FinancialReportCompiler(session, settings).execute(
                    **args
                )
                self.assertFalse(result.replayed)
                self.assertEqual(len(result.payload["findings"]), 3)
                verify_artifacts(result.payload, result.artifacts)
                run = await session.get(AnalysisRunModel, checkpoint.run_id)
                self.assertEqual(run.workflow_status, "completed")
                completed_at = run.completed_at
                self.assertIsNotNone(completed_at)
            async with self.sessions.begin() as session:
                replay = await FinancialReportCompiler(session, settings).execute(
                    **args
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(result.payload, replay.payload)
                self.assertEqual(result.artifacts, replay.artifacts)
                run = await session.get(AnalysisRunModel, checkpoint.run_id)
                self.assertEqual(run.completed_at, completed_at)
                rows = (
                    await session.scalars(
                        select(StageResultModel).where(
                            StageResultModel.run_id == run.id,
                            StageResultModel.stage_name.like("financial_report%"),
                        )
                    )
                ).all()
                self.assertEqual(len(rows), 3)
                self.assertTrue(
                    all(r.output_hash == semantic_hash(r.payload) for r in rows)
                )
                original = await session.get(
                    StageResultModel,
                    UUID(result.payload["approval_binding"]["draft_stage_id"]),
                )
                self.assertEqual(original.payload, draft.payload)
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(len(critic.calls), 1)

        asyncio.run(scenario())

    def test_concurrent_compilers_publish_once_and_return_identical_artifacts(self):
        async def scenario():
            _, settings, _, _, _, _, args = await self.ready()

            async def execute():
                async with self.sessions.begin() as session:
                    return await FinancialReportCompiler(session, settings).execute(
                        **args
                    )

            results = await asyncio.gather(execute(), execute())
            self.assertEqual(sum(r.replayed for r in results), 1)
            self.assertEqual(results[0].payload, results[1].payload)
            self.assertEqual(results[0].artifacts, results[1].artifacts)

        asyncio.run(scenario())

    def test_warnings_and_gaps_survive_compilation_and_appear_in_both_channels(self):
        async def scenario():
            _, settings, _, _, _, _, args = await self.ready(
                warning=True, quality_warning=True
            )
            async with self.sessions.begin() as session:
                result = await FinancialReportCompiler(session, settings).execute(
                    **args
                )
                self.assertEqual(
                    result.payload["audit_result"]["status"], "approved_with_warnings"
                )
                self.assertTrue(result.payload["limitations"])
                self.assertTrue(result.payload["missing_data_requests"])
                self.assertIn("The proposed wording exceeds", result.artifacts["html"])
                self.assertIn(
                    "Aviso sobre a qualidade dos dados:", result.artifacts["html"]
                )
                self.assertIn("Aviso da auditoria:", result.artifacts["html"])
                verify_artifacts(result.payload, result.artifacts)

        asyncio.run(scenario())

    def test_revision_and_fresh_approval_compile_only_the_corrected_draft(self):
        async def scenario():
            value, settings, checkpoint, draft, _ = await self.setup_review()
            settings = replace(settings, financial_report_enabled=True)
            critic = RecordingCritic(
                lambda raw, ctx: (
                    audit_tests.judgment(raw, "causality", "revise")
                    if len(critic.calls) == 1
                    else raw
                )
            )
            model = RecordingModel(
                lambda raw, ctx: {
                    **raw,
                    "findings": [
                        {**f, "title": "Observação corrigida"} for f in raw["findings"]
                    ],
                }
            )
            async with self.sessions.begin() as session:
                review = await FinancialReviewWorkflow(
                    session, settings, model, critic
                ).execute(**self.args(value, checkpoint))
                result = await FinancialReportCompiler(session, settings).execute(
                    **self.args(value, checkpoint),
                    audit_id=UUID(review["audits"][-1]["audit_id"])
                )
                self.assertEqual(result.payload["audit_result"]["revision_number"], 1)
                self.assertEqual(
                    result.payload["findings"][0]["title"], "Observação corrigida"
                )
                self.assertNotEqual(
                    result.payload["approval_binding"]["draft_hash"],
                    semantic_hash(draft.payload),
                )

        asyncio.run(scenario())

    def test_flag_allowlist_member_and_foreign_workspace_checks_precede_publication(
        self,
    ):
        async def scenario():
            _, settings, checkpoint, _, _, _, args = await self.ready()
            for changed in (
                replace(settings, financial_report_enabled=False),
                replace(settings, enabled=False),
                replace(settings, persist_stages=False),
                replace(settings, financial_audit_enabled=False),
                replace(settings, internal_workspaces=frozenset()),
            ):
                async with self.sessions.begin() as session:
                    with self.assertRaises(PermissionError):
                        await FinancialReportCompiler(session, changed).execute(**args)
                    self.assertEqual(await self.state(session, checkpoint), "compiling")
            for key in ("workspace_id", "user_id"):
                async with self.sessions.begin() as session:
                    with self.assertRaises(PermissionError):
                        await FinancialReportCompiler(session, settings).execute(
                            **{**args, key: uuid4()}
                        )

        asyncio.run(scenario())

    def test_blocked_and_revision_required_audits_cannot_publish(self):
        async def scenario():
            for outcome in ("revise", "block"):
                value, settings, checkpoint, _, _ = await self.setup_review()
                settings = replace(settings, financial_report_enabled=True)
                async with self.sessions.begin() as session:
                    audit = await FinancialAuditor(
                        session,
                        settings,
                        RecordingCritic(
                            lambda raw, ctx, outcome=outcome: audit_tests.judgment(
                                raw, "scope", outcome
                            )
                        ),
                    ).execute(**self.args(value, checkpoint))
                    with self.assertRaises(InvalidReport):
                        await FinancialReportCompiler(session, settings).execute(
                            **self.args(value, checkpoint),
                            audit_id=UUID(audit.payload["audit_id"])
                        )
                    run = await session.get(AnalysisRunModel, checkpoint.run_id)
                    self.assertIsNone(run.completed_at)

        asyncio.run(scenario())

    def test_rehashed_report_or_artifact_tampering_fails_on_replay(self):
        async def scenario():
            _, settings, checkpoint, _, _, _, args = await self.ready()
            async with self.sessions.begin() as session:
                await FinancialReportCompiler(session, settings).execute(**args)
            for name in (
                "financial_report",
                "financial_report_render",
                "generated_at",
                "artifact_extra",
            ):
                async with self.sessions() as session, session.begin():
                    stage = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == checkpoint.run_id,
                            StageResultModel.stage_name
                            == (
                                "financial_report"
                                if name == "generated_at"
                                else (
                                    "financial_report_render"
                                    if name == "artifact_extra"
                                    else name
                                )
                            ),
                        )
                    )
                    forged = deepcopy(stage.payload)
                    if name == "financial_report":
                        forged["findings"][0]["title"] = "Forged conclusion"
                    elif name == "generated_at":
                        forged["generated_at"] = "2026-01-01T00:00:00+00:00"
                    elif name == "artifact_extra":
                        forged["unexpected_narrative"] = "Unapproved additional content"
                    else:
                        forged["html"] = "<p>Missing limitations</p>"
                    stage.payload, stage.output_hash = forged, semantic_hash(forged)
                    await session.flush()
                    with self.assertRaises((InvalidReport, IdempotencyConflict)):
                        await FinancialReportCompiler(session, settings).execute(**args)
                    await session.rollback()

        asyncio.run(scenario())

    def test_live_metric_tampering_after_approval_blocks_publication(self):
        async def scenario():
            _, settings, checkpoint, _, _, _, args = await self.ready()
            async with self.sessions.begin() as session:
                metric = await session.scalar(
                    select(MetricObservationModel).where(
                        MetricObservationModel.run_id == checkpoint.run_id
                    )
                )
                metric.value += 1
                await session.flush()
                with self.assertRaises(
                    (InvalidCheckpoint, InvalidReport, IdempotencyConflict)
                ):
                    await FinancialReportCompiler(session, settings).execute(**args)
                self.assertEqual(await self.state(session, checkpoint), "compiling")

        asyncio.run(scenario())

    def test_renderer_failure_rolls_back_publication_and_session_can_retry(self):
        async def scenario():
            _, settings, checkpoint, _, _, _, args = await self.ready()
            async with self.sessions.begin() as session:
                with (
                    patch(
                        "saleslab_v2.reports.service.render_channels",
                        side_effect=ReportParityError("omitted warning"),
                    ),
                    self.assertRaises(ReportParityError),
                ):
                    await FinancialReportCompiler(session, settings).execute(**args)
                self.assertEqual(await self.state(session, checkpoint), "compiling")
                result = await FinancialReportCompiler(session, settings).execute(
                    **args
                )
                self.assertFalse(result.replayed)
                self.assertEqual(await self.state(session, checkpoint), "completed")

        asyncio.run(scenario())

    def test_missing_stage_and_different_locale_cannot_silently_republish(self):
        async def scenario():
            _, settings, checkpoint, _, _, _, args = await self.ready()
            async with self.sessions.begin() as session:
                await FinancialReportCompiler(session, settings).execute(**args)
            async with self.sessions() as session, session.begin():
                run = await session.get(AnalysisRunModel, checkpoint.run_id)
                run.language_context = {
                    **run.language_context,
                    "format_locale": "fr-FR",
                }
                await session.flush()
                with self.assertRaises((InvalidReport, IdempotencyConflict)):
                    await FinancialReportCompiler(session, settings).execute(**args)
                await session.rollback()
            async with self.sessions.begin() as session:
                stage = await session.scalar(
                    select(StageResultModel).where(
                        StageResultModel.run_id == checkpoint.run_id,
                        StageResultModel.stage_name == "financial_report_render",
                    )
                )
                await session.delete(stage)
                await session.flush()
                with self.assertRaises(InvalidReport):
                    await FinancialReportCompiler(session, settings).execute(**args)

        asyncio.run(scenario())
