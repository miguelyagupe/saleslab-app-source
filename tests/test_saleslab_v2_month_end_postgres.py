"""PostgreSQL 16 close preparation: source isolation, exact replay and rollback."""

import asyncio
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import test_saleslab_v2_pipeline_postgres as pipeline_tests
from month_end_fixtures import close_request, monthly
from pipeline_fixtures import change_row
from sqlalchemy import func, select

from models import Analysis, UsageCounter
from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.month_end.contracts import InvalidMonthEnd
from saleslab_v2.month_end.service import FinancialMonthEndCloser
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    CalculationTraceModel,
    MetricObservationModel,
    StageResultModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.service import FinancialPipeline


@unittest.skipUnless(
    pipeline_tests.URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class MonthEndPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pipeline_tests.PipelinePostgresTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        pipeline_tests.PipelinePostgresTests.tearDownClass.__func__(cls)

    setup_request = pipeline_tests.PipelinePostgresTests.setup_request

    async def ready(self, transform=None):
        value, settings = await self.setup_request()
        value = monthly(value)
        if transform:
            value = transform(value)
        settings = replace(settings, financial_month_end_enabled=True)
        async with self.sessions.begin() as session:
            pipeline = await FinancialPipeline(session, settings).execute(value)
        return value, settings, close_request(value, pipeline.run_id)

    async def execute(self, settings, request, key="monthly-close"):
        async with self.sessions.begin() as session:
            return await FinancialMonthEndCloser(session, settings).execute(
                request, attempt_key=key
            )

    def test_complete_persisted_preparation_replays_without_changing_source_or_product(
        self,
    ):
        async def scenario():
            _, settings, request = await self.ready()
            first = await self.execute(settings, request)
            replay = await self.execute(settings, request)
            self.assertFalse(first.replayed)
            self.assertTrue(replay.replayed)
            self.assertEqual(first.payload, replay.payload)
            self.assertEqual(first.payload["preparation_readiness"], "ready_for_review")
            async with self.sessions.begin() as session:
                source = await session.get(AnalysisRunModel, request.pipeline_run_id)
                self.assertEqual(source.workflow_status, "calculating")
                self.assertIsNone(source.completed_at)
                parent = await session.get(AnalysisRunModel, first.run_id)
                self.assertEqual(parent.workflow_status, "completed")
                for table in (MetricObservationModel, CalculationTraceModel):
                    self.assertEqual(
                        await session.scalar(
                            select(func.count())
                            .select_from(table)
                            .where(table.run_id == first.run_id)
                        ),
                        6,
                    )
                for table in (Analysis, UsageCounter):
                    self.assertEqual(
                        await session.scalar(select(func.count()).select_from(table)), 0
                    )

        asyncio.run(scenario())

    def test_concurrent_preparations_create_one_parent_and_one_trace_set(self):
        async def scenario():
            _, settings, request = await self.ready()
            left, right = await asyncio.gather(
                self.execute(settings, request), self.execute(settings, request)
            )
            self.assertEqual(left.run_id, right.run_id)
            self.assertEqual(left.payload, right.payload)
            self.assertEqual({left.replayed, right.replayed}, {False, True})
            async with self.sessions.begin() as session:
                self.assertEqual(
                    await session.scalar(
                        select(func.count()).select_from(AnalysisRunModel)
                    ),
                    2,
                )

        asyncio.run(scenario())

    def test_rounding_pass_preparation_is_satisfied_and_replays_after_persistence(self):
        async def scenario():
            _, settings, request = await self.ready(
                lambda value: change_row(value, "assets", text="100.005")
            )
            first = await self.execute(settings, request)
            replay = await self.execute(settings, request)
            self.assertTrue(replay.replayed)
            self.assertEqual(first.payload, replay.payload)
            self.assertEqual(first.payload["preparation_readiness"], "ready_for_review")
            reconciliation = next(
                i
                for i in first.payload["checklist"]
                if i["category"] == "reconciliation"
            )
            self.assertEqual(reconciliation["status"], "satisfied")
            self.assertEqual(
                reconciliation["reason_code"], "month_end.reconciliation_rounding_pass"
            )
            self.assertEqual(first.payload["missing_data_requests"], [])

        asyncio.run(scenario())

    def test_exact_repeating_ratio_materiality_survives_database_replay(self):
        async def scenario():
            def transform(value):
                return change_row(
                    change_row(value, "revenue", text="5"),
                    "revenue_prior_month",
                    text="3",
                )

            _, settings, request = await self.ready(transform)
            threshold = Decimal("66." + "6" * 97 + "7")
            request = replace(
                request,
                comparisons=request.comparisons[:1],
                policy=replace(
                    request.policy,
                    required_comparisons=("month_on_month",),
                    percentage_variation_threshold=threshold,
                ),
            )
            first = await self.execute(settings, request)
            replay = await self.execute(settings, request)
            self.assertEqual(first.payload, replay.payload)
            self.assertTrue(replay.replayed)
            self.assertEqual(first.payload["preparation_readiness"], "ready_for_review")
            self.assertEqual(
                Decimal(first.payload["comparisons"][0]["percentage"]), threshold
            )
            self.assertFalse(first.payload["comparisons"][0]["material"])

        asyncio.run(scenario())

    def test_feature_scope_membership_and_foreign_evidence_are_denied(self):
        async def scenario():
            _, settings, request = await self.ready()
            foreign_plan = replace(
                request.comparisons[0],
                current=replace(request.comparisons[0].current, source_id=uuid4()),
            )
            for denied_settings, denied_request in (
                (replace(settings, financial_month_end_enabled=False), request),
                (replace(settings, internal_workspaces=frozenset()), request),
                (settings, replace(request, user_id=uuid4())),
                (settings, replace(request, pipeline_run_id=uuid4())),
                (settings, replace(request, workspace_id=uuid4())),
                (settings, replace(request, comparisons=(foreign_plan,))),
            ):
                with self.subTest(request=denied_request), self.assertRaises(
                    PermissionError
                ):
                    await self.execute(denied_settings, denied_request)
            async with self.sessions.begin() as session:
                self.assertEqual(
                    await session.scalar(
                        select(func.count()).select_from(AnalysisRunModel)
                    ),
                    1,
                )

        asyncio.run(scenario())

    def test_changed_policy_or_confirmation_requires_new_attempt_key(self):
        async def scenario():
            _, settings, request = await self.ready()
            await self.execute(settings, request)
            for altered in (
                replace(
                    request,
                    policy=replace(
                        request.policy, absolute_variation_threshold=Decimal(100)
                    ),
                ),
                replace(request, confirmations=()),
                replace(request, comparisons=request.comparisons[:1]),
            ):
                with self.assertRaises(IdempotencyConflict):
                    await self.execute(settings, altered)
            new = await self.execute(
                settings, replace(request, confirmations=()), "missing-confirmation"
            )
            self.assertEqual(new.payload["preparation_readiness"], "incomplete")

        asyncio.run(scenario())

    def test_blocked_and_mapping_required_inputs_produce_gaps_without_calculations(
        self,
    ):
        async def scenario():
            for ref, changes in (
                ("assets", {"text": "200"}),
                ("equity", {"name": "unmapped mystery account"}),
            ):
                with self.subTest(reference=ref):
                    _, settings, request = await self.ready(
                        lambda v, ref=ref, changes=changes: change_row(
                            v, ref, **changes
                        )
                    )
                    first = await self.execute(settings, request)
                    self.assertEqual(
                        first.payload["preparation_readiness"], "incomplete"
                    )
                    self.assertTrue(first.payload["missing_data_requests"])
                    self.assertEqual(first.payload["calculations"], [])
                    replay = await self.execute(settings, request)
                    self.assertTrue(replay.replayed)
                    self.assertEqual(first.payload, replay.payload)

        asyncio.run(scenario())

    def test_rehashed_output_or_input_tampering_cannot_pass_exact_replay(self):
        async def scenario():
            _, settings, request = await self.ready()
            first = await self.execute(settings, request)
            for name in ("financial_month_end_input", "financial_month_end_result"):
                async with self.sessions() as session, session.begin():
                    stage = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == first.run_id,
                            StageResultModel.stage_name == name,
                        )
                    )
                    forged = deepcopy(stage.payload)
                    forged["accounting_close_attested"] = True
                    stage.payload, stage.output_hash = forged, semantic_hash(forged)
                    await session.flush()
                    with self.assertRaises(IdempotencyConflict):
                        await FinancialMonthEndCloser(session, settings).execute(
                            request, attempt_key="monthly-close"
                        )
                    await session.rollback()

        asyncio.run(scenario())

    def test_missing_or_changed_persisted_calculation_is_rejected_not_repaired(self):
        async def scenario():
            _, settings, request = await self.ready()
            first = await self.execute(settings, request)
            for table, field, forged in (
                (MetricObservationModel, "value", Decimal(99)),
                (CalculationTraceModel, "exact_output", "99"),
            ):
                async with self.sessions() as session, session.begin():
                    row = await session.scalar(
                        select(table).where(table.run_id == first.run_id)
                    )
                    setattr(row, field, forged)
                    await session.flush()
                    with self.assertRaises(InvalidMonthEnd):
                        await FinancialMonthEndCloser(session, settings).execute(
                            request, attempt_key="monthly-close"
                        )
                    await session.rollback()
            async with self.sessions() as session, session.begin():
                row = await session.scalar(
                    select(MetricObservationModel).where(
                        MetricObservationModel.run_id == first.run_id
                    )
                )
                await session.delete(row)
                await session.flush()
                with self.assertRaises(InvalidMonthEnd):
                    await FinancialMonthEndCloser(session, settings).execute(
                        request, attempt_key="monthly-close"
                    )
                await session.rollback()

        asyncio.run(scenario())

    def test_live_source_metric_tampering_invalidates_preparation_replay(self):
        async def scenario():
            _, settings, request = await self.ready()
            await self.execute(settings, request)
            async with self.sessions() as session, session.begin():
                row = await session.scalar(
                    select(MetricObservationModel).where(
                        MetricObservationModel.run_id == request.pipeline_run_id
                    )
                )
                row.value = Decimal(99)
                await session.flush()
                with self.assertRaises(InvalidCheckpoint):
                    await FinancialMonthEndCloser(session, settings).execute(
                        request, attempt_key="monthly-close"
                    )
                await session.rollback()

        asyncio.run(scenario())

    def test_failure_after_metric_writes_rolls_back_entire_preparation_and_allows_retry(
        self,
    ):
        async def scenario():
            _, settings, request = await self.ready()
            original = V2Repository.persist_stage

            async def fail(repository, stage):
                if stage.stage_name == "financial_month_end_result":
                    raise RuntimeError("synthetic storage failure")
                return await original(repository, stage)

            async with self.sessions.begin() as session:
                with patch.object(
                    V2Repository, "persist_stage", fail
                ), self.assertRaises(RuntimeError):
                    await FinancialMonthEndCloser(session, settings).execute(
                        request, attempt_key="retry"
                    )
                self.assertEqual(
                    await session.scalar(
                        select(func.count()).select_from(AnalysisRunModel)
                    ),
                    1,
                )
                result = await FinancialMonthEndCloser(session, settings).execute(
                    request, attempt_key="retry"
                )
                self.assertEqual(
                    result.payload["preparation_readiness"], "ready_for_review"
                )

        asyncio.run(scenario())
