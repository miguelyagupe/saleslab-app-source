"""Authenticated upload → durable job → audited report → history/quota → HTML/PDF."""

import asyncio
import json
import threading
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from uuid import UUID, uuid4

import test_saleslab_v2_pipeline_postgres as pipeline_tests
from analyst_fixtures import RecordingModel
from audit_fixtures import RecordingCritic
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from product_fixtures import csv_content, upload_case
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from auth import issue_session_token
from database import get_db
from models import Analysis, UsageCounter, Workspace, WorkspaceMember
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.persistence.models import StageResultModel
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.product.api import get_models, get_settings, router
from saleslab_v2.product.contracts import GenerateChoices, UploadChoices
from saleslab_v2.product.jobs import process_one, stage_id
from saleslab_v2.product.limits import FinancialUploadLimit
from saleslab_v2.product.pilot import SOURCE_SHA256
from saleslab_v2.product.pilot_client import verify_report


@unittest.skipUnless(
    pipeline_tests.URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class ProductPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pipeline_tests.PipelinePostgresTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        pipeline_tests.PipelinePostgresTests.tearDownClass.__func__(cls)

    setup_request = pipeline_tests.PipelinePostgresTests.setup_request

    async def ready(self, critic=None):
        value, settings = await self.setup_request()
        self.value = value
        self.settings = replace(
            settings,
            financial_product_enabled=True,
            financial_analyst_enabled=True,
            financial_audit_enabled=True,
            financial_report_enabled=True,
        )
        self.model, self.critic = RecordingModel(), critic or RecordingCritic()
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(FinancialUploadLimit)

        async def db():
            async with self.sessions() as session:
                yield session

        async def models():
            yield self.model, self.critic

        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_models] = models
        self.client = AsyncClient(
            transport=ASGITransport(app),
            base_url="http://test",
            headers={
                "X-Token": issue_session_token(
                    value.user_id, value.workspace_id, "owner"
                )
            },
        )
        self.parsed, self.preview, self.choices, self.content, _ = upload_case(
            value.workspace_id, value.user_id
        )

    async def post(self, path="generate", choices=None, content=None):
        return await self.client.post(
            "/financial-v2/" + path,
            data={"options": (choices or self.choices).model_dump_json()},
            files={
                "file": (
                    "statements.csv",
                    self.content if content is None else content,
                    "text/csv",
                )
            },
        )

    async def work(self):
        async with self.sessions.begin() as session:
            return await process_one(session, self.settings, self.model, self.critic)

    async def counts(self):
        async with self.sessions.begin() as session:
            return (
                await session.scalar(select(func.count()).select_from(Analysis)),
                await session.scalar(select(func.sum(UsageCounter.count))) or 0,
            )

    def test_legacy_ebit_and_fcf_jobs_keep_their_plan_through_queue_resume_and_report_replay(
        self,
    ):
        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            content = csv_content(
                extra=[
                    (
                        "EBIT",
                        "200",
                        "2026",
                        "income_statement",
                        "consolidated",
                        "EUR",
                        "raw",
                        "current",
                    ),
                    (
                        "cash flow from operations",
                        "300",
                        "2026",
                        "cash_flow_statement",
                        "consolidated",
                        "EUR",
                        "raw",
                        "current",
                    ),
                    (
                        "purchases of property plant and equipment",
                        "-80",
                        "2026",
                        "cash_flow_statement",
                        "consolidated",
                        "EUR",
                        "raw",
                        "current",
                    ),
                ]
            )
            async with self.client:
                self.assertEqual(
                    (await self.client.get("/financial-v2/capabilities")).json()[
                        "planning_versions"
                    ],
                    [
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
                    ],
                )
                for count, version in enumerate(("1", "2", "3"), 1):
                    choices = UploadChoices(
                        source_language="en",
                        format_locale="en-GB",
                        report_language="pt",
                        planning_version=version,
                    )
                    _, preview, generate, _, _ = upload_case(
                        self.value.workspace_id,
                        self.value.user_id,
                        choices=choices,
                        content=content,
                    )
                    queued = await self.post(choices=generate, content=content)
                    self.assertEqual(queued.status_code, 202, queued.text)
                    job_id = queued.json()["job_id"]
                    async with self.sessions.begin() as session:
                        saved = await session.get(
                            StageResultModel, stage_id(UUID(job_id), "input")
                        )
                        stored = saved.payload["choices"]
                        self.assertNotIn("accounting_parentheses", stored)
                        self.assertNotIn("fiscal_year_end_month", stored)
                        self.assertNotIn("source_currency", stored)
                        self.assertNotIn("source_scale", stored)
                        self.assertNotIn("normalization_version", stored)
                        self.assertEqual(
                            stored.get("planning_version"),
                            None if version == "1" else version,
                        )
                        self.assertEqual(
                            GenerateChoices.model_validate(stored).planning_version,
                            version,
                        )
                    self.assertEqual(
                        (await self.post(choices=generate, content=content)).json()[
                            "job_id"
                        ],
                        job_id,
                    )
                    self.assertEqual(await self.counts(), (count - 1, count - 1))
                    await self.work()
                    done = (
                        await self.client.get("/financial-v2/jobs/" + job_id)
                    ).json()
                    self.assertEqual(done["status"], "completed", done)
                    run = done["run_id"]
                    response = await self.client.get(
                        f"/financial-v2/reports/{run}/json"
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    metrics = {
                        m["observation"]["metric_id"]: unpack(m["observation"])
                        for m in response.json()["metric_observations"]
                    }
                    self.assertEqual("ebit_margin_pct" in metrics, version != "1")
                    if version != "1":
                        self.assertEqual(metrics["ebit_margin_pct"]["value"], "20.0")
                        self.assertEqual(
                            metrics["ebit_margin_pct"]["source_references"],
                            ["csv:2", "csv:7"],
                        )
                    self.assertEqual("free_cash_flow" in metrics, version == "3")
                    if version == "3":
                        self.assertEqual(metrics["free_cash_flow"]["value"], "220")
                        self.assertEqual(
                            metrics["free_cash_flow"]["metric_definition_version"], "2"
                        )
                        self.assertEqual(
                            metrics["free_cash_flow"]["source_references"],
                            ["csv:8", "csv:9"],
                        )
                    for channel in ("html", "pdf"):
                        self.assertEqual(
                            (
                                await self.client.get(
                                    f"/financial-v2/reports/{run}/{channel}"
                                )
                            ).status_code,
                            200,
                        )
                    self.assertEqual(
                        (await self.post(choices=generate, content=content)).json()[
                            "job_id"
                        ],
                        job_id,
                    )
                    self.assertEqual(await self.counts(), (count, count))
                    self.assertEqual(len(self.model.calls), count)
                    self.assertEqual(preview["planning_version"], version)

        asyncio.run(scenario())

    def test_current_ratio_uses_pinned_current_classifications_through_publication(
        self,
    ):
        from decimal import Decimal

        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            content = csv_content(
                extra=[
                    (
                        "current assets",
                        "0.04",
                        "2026",
                        "balance_sheet",
                        "consolidated",
                        "EUR",
                        "thousands",
                        "current",
                    ),
                    (
                        "current liabilities",
                        "20",
                        "2026",
                        "balance_sheet",
                        "consolidated",
                        "EUR",
                        "raw",
                        "current",
                    ),
                ]
            )
            opts = UploadChoices(
                source_language="en",
                format_locale="en-GB",
                report_language="pt",
                planning_version="5",
                normalization_version="2",
            )
            async with self.client:
                parsed, preview, generate, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=opts,
                )
                self.assertTrue(preview["ready"])
                self.assertEqual(parsed.batch.metadata.terminology_version, "2")
                current = next(
                    m
                    for m in preview["calculated_metrics"]
                    if m["metric_id"] == "current_ratio"
                )
                self.assertEqual(Decimal(current["exact_value"]), Decimal(2))
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                changed = generate.model_copy(update={"planning_version": "4"})
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(saved.payload["choices"]["planning_version"], "5")
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                self.assertEqual(
                    report.json()["methodology"]["terminology_version"],
                    "financial-terminology-v2",
                )
                metric = next(
                    m
                    for m in report.json()["metric_observations"]
                    if m["observation"]["metric_id"] == "current_ratio"
                )
                observation = unpack(metric["observation"])
                self.assertEqual(Decimal(observation["value"]), Decimal(2))
                self.assertEqual(observation["unit"], "ratio")
                self.assertEqual(observation["source_references"], ["csv:7", "csv:8"])
                self.assertEqual(
                    metric["trace"]["canonical_expression"],
                    "current_assets/current_liabilities",
                )
                self.assertEqual(
                    [Decimal(i["value"]) for i in metric["trace"]["inputs"]],
                    [Decimal(40), Decimal(20)],
                )
                async with self.sessions.begin() as session:
                    stage = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "normalization",
                        )
                    )
                    self.assertEqual(
                        stage.payload["request"]["sources"][0]["metadata"][
                            "terminology_version"
                        ],
                        "2",
                    )
                    rows = [
                        r
                        for r in stage.payload["observations"]
                        if r["concept_id"] in {"current_assets", "current_liabilities"}
                    ]
                    self.assertEqual(len(rows), 2)
                    self.assertTrue(
                        all(
                            r["terminology_version"] == "financial-terminology-v2"
                            for r in rows
                        )
                    )
                self.assertEqual(parsed.original_rows[-2]["amount"], "0.04")
                for channel in ("html", "pdf"):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{run}/{channel}"
                            )
                        ).status_code,
                        200,
                    )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_cash_bridge_is_pinned_through_queue_audit_and_all_report_formats(self):
        from test_saleslab_v2_cash_reconciliation_upload import cash_rows, choices

        async def scenario():
            await self.ready()
            content = csv_content(extra=cash_rows(end="321"))
            opts = choices(cash_rounding_tolerance="1")
            async with self.client:
                parsed, preview, generate, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=opts,
                )
                self.assertTrue(preview["ready"])
                self.assertEqual(parsed.batch.metadata.terminology_version, "3")
                self.assertEqual(
                    preview["cash_reconciliation"]["status"], "rounding_pass"
                )
                cash_preview = next(
                    r
                    for r in preview["validation_summary"]["reconciliations"]
                    if r["rule_id"] == "REC-CF-001"
                )["reconciliation_evidence"]
                self.assertEqual(cash_preview["policy"]["rounding_absolute"], "1")
                self.assertEqual(len(cash_preview["inputs"]), 6)
                self.assertEqual(
                    cash_preview["equations"][0]["canonical_expression"],
                    "opening_cash+cfo+cfi+cff+fx_other=closing_cash",
                )
                changed = generate.model_copy(update={"cash_rounding_tolerance": "0"})
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(saved.payload["choices"]["planning_version"], "6")
                    self.assertEqual(
                        saved.payload["choices"]["cash_rounding_tolerance"], "1"
                    )
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                payload = report.json()
                self.assertEqual(
                    payload["methodology"]["terminology_version"],
                    "financial-terminology-v3",
                )
                bridge = next(
                    r
                    for r in payload["reconciliations"]
                    if r["rule_id"] == "REC-CF-001"
                )
                self.assertEqual(bridge["status"], "rounding_pass")
                self.assertEqual(bridge["absolute_delta"], "1")
                self.assertEqual(
                    bridge["policy_fingerprint"], cash_preview["policy_fingerprint"]
                )
                self.assertEqual(
                    bridge["equations"],
                    [
                        {
                            k: v
                            for k, v in equation.items()
                            if k in bridge["equations"][0]
                        }
                        for equation in cash_preview["equations"]
                    ],
                )
                self.assertEqual(len(bridge["source_references"]), 6)
                self.assertTrue(
                    any(
                        m["observation"]["metric_id"] == "cash_fx_other"
                        for m in payload["metric_observations"]
                    )
                )
                for channel in ("html", "pdf"):
                    response = await self.client.get(
                        f"/financial-v2/reports/{run}/{channel}"
                    )
                    self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_profitability_bundle_persists_variants_independent_bridges_and_quota(self):
        from decimal import Decimal

        from profitability_fixtures import profitability_choices, profitability_rows

        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            # The analyst deliberately selects at most its existing 20 findings;
            # all metric observations remain in the published evidence appendix.
            self.model.transform = lambda raw, context: {
                **raw,
                "findings": raw["findings"][:20],
            }
            opts = profitability_choices(profitability_rounding_tolerance="0.01")
            content = csv_content(extra=profitability_rows())
            async with self.client:
                _, preview, generate, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=opts,
                )
                self.assertTrue(preview["ready"])
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                changed = generate.model_copy(update={"planning_version": "6"})
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                changed = generate.model_copy(
                    update={"profitability_rounding_tolerance": "0"}
                )
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(saved.payload["choices"]["planning_version"], "7")
                    self.assertEqual(
                        saved.payload["choices"]["profitability_rounding_tolerance"],
                        "0.01",
                    )
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                payload = report.json()
                self.assertEqual(
                    payload["methodology"]["terminology_version"],
                    "financial-terminology-v4",
                )
                observations = [
                    (unpack(m["observation"]), m["trace"])
                    for m in payload["metric_observations"]
                ]
                statutory = [
                    (o, t)
                    for o, t in observations
                    if o["metric_id"] == "ebitda_statutory"
                ]
                self.assertEqual(
                    {o["metric_definition_version"] for o, t in statutory}, {"1", "2"}
                )
                self.assertTrue(all(Decimal(o["value"]) == 250 for o, t in statutory))
                self.assertEqual(
                    {tuple(o["source_references"]) for o, t in statutory},
                    {("csv:14", "csv:7"), ("csv:16",)},
                )
                margins = [
                    (o, t)
                    for o, t in observations
                    if o["metric_id"] == "ebitda_margin_pct"
                ]
                self.assertEqual(
                    {Decimal(o["value"]) for o, t in margins},
                    {Decimal(25), Decimal(27)},
                )
                self.assertEqual(
                    {t["inputs"][0]["variant"] for o, t in margins},
                    {"statutory", "adjusted"},
                )
                bridges = {r["rule_id"]: r for r in payload["reconciliations"]}
                for rule in ("REC-PL-001", "REC-EBITDA-001"):
                    self.assertEqual(bridges[rule]["status"], "pass")
                    self.assertEqual(len(bridges[rule]["equations"]), 2)
                source = next(
                    i
                    for i in bridges["REC-EBITDA-001"]["input_references"]
                    if i["role"] == "statutory_ebitda"
                )
                self.assertEqual(source["source_references"], ["csv:16"])
                for channel in ("html", "pdf"):
                    result = await self.client.get(
                        f"/financial-v2/reports/{run}/{channel}"
                    )
                    self.assertEqual(result.status_code, 200)
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )
                bad_content = csv_content(
                    extra=profitability_rows(
                        changes={"reported_statutory_ebitda": {"amount": "251"}}
                    )
                )
                _, blocked, bad_generate, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=bad_content,
                    choices=opts,
                )
                self.assertFalse(blocked["ready"])
                bad_job = await self.post(choices=bad_generate, content=bad_content)
                self.assertEqual(bad_job.status_code, 422, bad_job.text)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_pdf_source_review_blocks_calculation_until_confirmed_and_replays_publication(
        self,
    ):
        from hashlib import sha256

        from pdf_fixtures import pdf_content, pending_pdf

        from saleslab_v2.product.pdf_contracts import PdfImport

        async def scenario():
            await self.ready()
            content = pdf_content()
            _, choices, _ = pending_pdf(content)
            async with self.client:

                async def preview_pdf(options):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": options.model_dump_json()},
                        files={"file": ("statements.pdf", content, "application/pdf")},
                    )

                inspected = await self.client.post(
                    "/financial-v2/inspect",
                    data={
                        "pdf_layout": '{"pages":[{"page":1,"cuts":[40]},{"page":2,"cuts":[40]}]}'
                    },
                    files={"file": ("statements.pdf", content, "application/pdf")},
                )
                self.assertEqual(inspected.status_code, 200, inspected.text)
                self.assertEqual(inspected.json()["pdf"]["page_count"], 2)
                pending = await preview_pdf(choices)
                self.assertEqual(pending.status_code, 200, pending.text)
                review = pending.json()
                self.assertEqual(review["schema"], "financial-pdf-review-v1")
                self.assertFalse(review["ready"])
                self.assertNotIn("calculated_metrics", review)
                self.assertIn("no-store", pending.headers["cache-control"])
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                unconfirmed = GenerateChoices(
                    **choices.model_dump(),
                    preview_hash="0" * 64,
                    attempt_key=str(uuid4()),
                )
                denied = await self.client.post(
                    "/financial-v2/generate",
                    data={"options": unconfirmed.model_dump_json()},
                    files={"file": ("statements.pdf", content)},
                )
                self.assertEqual(denied.status_code, 422)
                self.assertEqual(await self.counts(), (0, 0))
                selected = choices.pdf_import.model_dump()
                selected.update(
                    review_hash=review["review_hash"],
                    confirmed_rows=[r["location"] for r in review["review_rows"]],
                )
                confirmed = choices.model_copy(
                    update={"pdf_import": PdfImport.model_validate(selected)}
                )
                valid = await preview_pdf(confirmed)
                self.assertEqual(valid.status_code, 200, valid.text)
                financial = valid.json()
                self.assertTrue(financial["ready"])
                options = GenerateChoices(
                    **confirmed.model_dump(),
                    preview_hash=financial["preview_hash"],
                    attempt_key=str(uuid4()),
                )

                async def submit(value):
                    return await self.client.post(
                        "/financial-v2/generate",
                        data={"options": value.model_dump_json()},
                        files={"file": ("statements.pdf", content, "application/pdf")},
                    )

                changed = options.model_copy(update={"format_locale": "pt-PT"})
                self.assertEqual((await submit(changed)).status_code, 422)
                queued = await submit(options)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                from saleslab_v2.product.pdf_source import _DECODERS, PdfDecoderBusy

                with _DECODERS, self.assertRaises(PdfDecoderBusy):
                    await self.work()
                pending_job = (
                    await self.client.get("/financial-v2/jobs/" + job)
                ).json()
                self.assertEqual(pending_job["status"], "pending", pending_job)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                async with self.sessions.begin() as session:
                    self.assertIsNone(
                        await session.get(
                            StageResultModel, stage_id(UUID(job), "result")
                        )
                    )
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = (
                    await self.client.get(f"/financial-v2/reports/{run}/json")
                ).json()
                self.assertTrue(
                    report["source_index"][0]["parser_version"].startswith(
                        "financial-pdf-text-v1:pypdf:"
                    )
                )
                self.assertEqual(
                    report["source_index"][0]["content_hash"],
                    sha256(content).hexdigest(),
                )
                refs = {
                    ref["location"]
                    for e in report["evidence"]
                    for ref in e["source_references"]
                }
                self.assertIn("p1:L3:B", refs)
                for channel in ("html", "pdf"):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{run}/{channel}"
                            )
                        ).status_code,
                        200,
                    )
                async with self.sessions.begin() as session:
                    receipt = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "financial_product",
                        )
                    )
                    self.assertEqual(
                        receipt.payload["input"]["source_import"]["review_hash"],
                        review["review_hash"],
                    )
                    self.assertEqual(
                        receipt.payload["input"]["original_rows"][0]["page"], 1
                    )
                    self.assertEqual(
                        receipt.payload["input"]["original_rows"][0]["extracted_text"],
                        review["review_rows"][0]["extracted_text"],
                    )
                self.assertEqual((await submit(options)).json()["job_id"], job)
                from saleslab_v2.product.ingestion import parse_upload
                from saleslab_v2.product.service import FinancialProduct

                async with self.sessions.begin() as session:
                    parsed = parse_upload(
                        self.value.workspace_id, "statements.pdf", content, options
                    )
                    replay = await FinancialProduct(
                        session, self.settings, self.model, self.critic
                    ).generate(parsed, options, self.value.user_id)
                    self.assertTrue(replay["replayed"])
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_queued_pdf_parsing_keeps_authenticated_api_responsive(self):
        from pdf_fixtures import confirmed_pdf
        from product_fixtures import upload_case

        from saleslab_v2.product.ingestion import parse_upload

        async def scenario():
            await self.ready()
            content, choices, _, _ = confirmed_pdf(self.value.workspace_id)
            _, preview, *_ = upload_case(
                self.value.workspace_id,
                content=content,
                filename="statements.pdf",
                choices=choices,
            )
            options = GenerateChoices(
                **choices.model_dump(),
                preview_hash=preview["preview_hash"],
                attempt_key=str(uuid4()),
            )
            entered, release = threading.Event(), threading.Event()

            def held_parse(*args, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise AssertionError("Event loop did not release PDF parsing")
                return parse_upload(*args, **kwargs)

            async with self.client:
                queued = await self.client.post(
                    "/financial-v2/generate",
                    data={"options": options.model_dump_json()},
                    files={"file": ("statements.pdf", content, "application/pdf")},
                )
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                with patch("saleslab_v2.product.jobs.parse_upload", held_parse):
                    worker = asyncio.create_task(self.work())
                    try:
                        self.assertTrue(
                            await asyncio.wait_for(
                                asyncio.to_thread(entered.wait, 3), 4
                            )
                        )
                        response = await asyncio.wait_for(
                            self.client.get("/financial-v2/jobs/" + job), 2
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json()["status"], "pending")
                        self.assertFalse(worker.done())
                    finally:
                        release.set()
                        await worker
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_growth_definitions_bind_preview_queue_audit_report_and_replay(self):
        from growth_fixtures import growth_case
        from product_fixtures import upload_case

        async def scenario():
            await self.ready()
            _, _, content, choices = growth_case(
                self.value.workspace_id, self.value.user_id
            )
            async with self.client:

                async def preview(options):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": options.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                missing = choices.model_copy(update={"growth_definitions": None})
                review = await preview(missing)
                self.assertEqual(review.status_code, 200, review.text)
                self.assertEqual(review.json()["schema"], "financial-growth-review-v1")
                self.assertFalse(review.json()["ready"])
                self.assertNotIn("calculated_metrics", review.json())
                self.assertIn("no-store", review.headers["cache-control"])
                _, financial, *_ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=choices,
                )
                valid = await preview(choices)
                self.assertEqual(valid.status_code, 200, valid.text)
                self.assertEqual(
                    valid.json()["preview_hash"], financial["preview_hash"]
                )
                options = GenerateChoices(
                    **choices.model_dump(),
                    preview_hash=financial["preview_hash"],
                    attempt_key=str(uuid4()),
                )

                async def submit(value):
                    return await self.client.post(
                        "/financial-v2/generate",
                        data={"options": value.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                rejected = await submit(
                    options.model_copy(update={"growth_definitions": None})
                )
                self.assertEqual(rejected.status_code, 422, rejected.text)
                changed = dict(choices.growth_definitions)
                changed.update(
                    {
                        "csv:8": "exchange rates fixed at 2025",
                        "csv:9": "exchange rates fixed at 2025",
                    }
                )
                stale = await submit(
                    options.model_copy(update={"growth_definitions": changed})
                )
                self.assertIn(stale.status_code, (409, 422), stale.text)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                queued = await submit(options)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = (
                    await self.client.get(f"/financial-v2/reports/{run}/json")
                ).json()
                self.assertEqual(len(report["methodology"]["growth_definitions"]), 8)
                self.assertEqual(
                    report["methodology"]["terminology_version"],
                    "financial-terminology-v6",
                )
                self.assertTrue(
                    {
                        "revenue_growth_cc_pct",
                        "revenue_growth_lfl_pct",
                        "revenue_change_reported",
                    }
                    <= {
                        m["observation"]["metric_id"]
                        for m in report["metric_observations"]
                    }
                )
                quoted = choices.growth_definitions["csv:8"]
                html = await self.client.get(f"/financial-v2/reports/{run}/html")
                self.assertIn(quoted, html.text)
                self.assertEqual(
                    (
                        await self.client.get(f"/financial-v2/reports/{run}/pdf")
                    ).status_code,
                    200,
                )
                async with self.sessions.begin() as session:
                    receipt = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "financial_product",
                        )
                    )
                    self.assertTrue(
                        any(
                            r.get("growth_definition_quote") == quoted
                            for r in receipt.payload["input"]["original_rows"]
                        )
                    )
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["growth_definitions"],
                        choices.growth_definitions,
                    )
                self.assertEqual((await submit(options)).json()["job_id"], job)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_debt_partitions_bind_preview_queue_audit_report_and_replay(self):
        from debt_fixtures import debt_case
        from product_fixtures import upload_case

        async def scenario():
            await self.ready()
            _, _, content, choices = debt_case(
                self.value.workspace_id, self.value.user_id
            )
            async with self.client:

                async def preview(options):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": options.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                missing = choices.model_copy(update={"debt_definitions": None})
                review = await preview(missing)
                self.assertEqual(review.status_code, 200, review.text)
                self.assertEqual(review.json()["schema"], "financial-debt-review-v1")
                self.assertFalse(review.json()["ready"])
                self.assertNotIn("calculated_metrics", review.json())
                self.assertIn("no-store", review.headers["cache-control"])
                _, financial, *_ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=choices,
                )
                valid = await preview(choices)
                self.assertEqual(valid.status_code, 200, valid.text)
                self.assertEqual(
                    valid.json()["preview_hash"], financial["preview_hash"]
                )
                options = GenerateChoices(
                    **choices.model_dump(),
                    preview_hash=financial["preview_hash"],
                    attempt_key=str(uuid4()),
                )

                async def submit(value):
                    return await self.client.post(
                        "/financial-v2/generate",
                        data={"options": value.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                rejected = await submit(
                    options.model_copy(update={"debt_definitions": None})
                )
                self.assertEqual(rejected.status_code, 422, rejected.text)
                changed = {
                    loc: "outstanding principal excluding leases at reporting date"
                    for loc in choices.debt_definitions
                }
                stale = await submit(
                    options.model_copy(update={"debt_definitions": changed})
                )
                self.assertIn(stale.status_code, (409, 422), stale.text)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                queued = await submit(options)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = (
                    await self.client.get(f"/financial-v2/reports/{run}/json")
                ).json()
                self.assertEqual(len(report["methodology"]["debt_definitions"]), 5)
                self.assertEqual(
                    report["methodology"]["terminology_version"],
                    "financial-terminology-v7",
                )
                self.assertTrue(
                    {
                        "fixed_variable_mix",
                        "variable_rate_share_pct",
                        "maturity_concentration",
                    }
                    <= {
                        m["observation"]["metric_id"]
                        for m in report["metric_observations"]
                    }
                )
                quoted = choices.debt_definitions["csv:8"]
                html = await self.client.get(f"/financial-v2/reports/{run}/html")
                self.assertIn(quoted, html.text)
                self.assertEqual(
                    (
                        await self.client.get(f"/financial-v2/reports/{run}/pdf")
                    ).status_code,
                    200,
                )
                async with self.sessions.begin() as session:
                    receipt = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "financial_product",
                        )
                    )
                    self.assertTrue(
                        any(
                            r.get("debt_definition_quote") == quoted
                            for r in receipt.payload["input"]["original_rows"]
                        )
                    )
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["debt_definitions"],
                        choices.debt_definitions,
                    )
                self.assertEqual((await submit(options)).json()["job_id"], job)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_working_capital_disclosure_binds_source_preview_publication_and_replay(
        self,
    ):
        from working_capital_fixtures import BASIS, working_capital_case

        async def scenario():
            await self.ready()
            _, _, content, choices, locations = working_capital_case(
                self.value.workspace_id, self.value.user_id
            )
            async with self.client:

                async def preview(options):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": options.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                partial = choices.model_copy(
                    update={
                        "mappings": {
                            locations["closing_nwc"]: "reported_operating_nwc"
                        },
                        "working_capital_definitions": None,
                    }
                )
                partial_response = await preview(partial)
                self.assertEqual(
                    partial_response.status_code, 200, partial_response.text
                )
                self.assertIn("mapping_options", partial_response.json())
                self.assertFalse(partial_response.json()["ready"])
                review = await preview(
                    choices.model_copy(update={"working_capital_definitions": None})
                )
                self.assertEqual(review.status_code, 200, review.text)
                self.assertEqual(
                    review.json()["schema"], "financial-working-capital-review-v1"
                )
                self.assertIn("no-store", review.headers["cache-control"])
                valid = await preview(choices)
                self.assertEqual(valid.status_code, 200, valid.text)
                self.assertTrue(valid.json()["ready"])
                options = GenerateChoices(
                    **choices.model_dump(),
                    preview_hash=valid.json()["preview_hash"],
                    attempt_key=str(uuid4()),
                )

                async def submit(value, upload=content):
                    return await self.client.post(
                        "/financial-v2/generate",
                        data={"options": value.model_dump_json()},
                        files={"file": ("statements.csv", upload, "text/csv")},
                    )

                self.assertEqual(
                    (
                        await submit(
                            options.model_copy(
                                update={"working_capital_definitions": None}
                            )
                        )
                    ).status_code,
                    422,
                )
                shorter = "operating current assets exclude cash and non-operating assets; operating current liabilities exclude debt and leases"
                changed = {loc: shorter for loc in choices.working_capital_definitions}
                self.assertIn(
                    (
                        await submit(
                            options.model_copy(
                                update={"working_capital_definitions": changed}
                            )
                        )
                    ).status_code,
                    (409, 422),
                )
                self.assertIn(
                    (
                        await submit(options, content.replace(b";300;", b";301;"))
                    ).status_code,
                    (409, 422),
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                queued = await submit(options)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = (
                    await self.client.get(f"/financial-v2/reports/{run}/json")
                ).json()
                self.assertEqual(
                    len(report["methodology"]["working_capital_definitions"]), 7
                )
                self.assertTrue(
                    all(
                        d["definition_quote"] == BASIS
                        for d in report["methodology"]["working_capital_definitions"]
                    )
                )
                self.assertTrue(
                    any(
                        m["observation"]["metric_id"] == "operating_nwc_change"
                        for m in report["metric_observations"]
                    )
                )
                self.assertIn(
                    BASIS,
                    (await self.client.get(f"/financial-v2/reports/{run}/html")).text,
                )
                self.assertEqual(
                    (
                        await self.client.get(f"/financial-v2/reports/{run}/pdf")
                    ).status_code,
                    200,
                )
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["working_capital_definitions"],
                        choices.working_capital_definitions,
                    )
                self.assertEqual((await submit(options)).json()["job_id"], job)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )
                self.assertEqual(
                    (await self.client.get(f"/financial-v2/reports/{run}/json")).json(),
                    report,
                )

        asyncio.run(scenario())

    def test_roic_source_confirmation_survives_queue_audit_publication_and_replay(self):
        from roic_upload_fixtures import AVERAGE, TAX, roic_case

        async def scenario():
            await self.ready()
            _, _, content, choices = roic_case(
                self.value.workspace_id, self.value.user_id
            )
            async with self.client:

                async def preview(options):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": options.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                missing = choices.model_copy(update={"roic_sources": None})
                review = await preview(missing)
                self.assertEqual(review.status_code, 200, review.text)
                self.assertEqual(review.json()["schema"], "financial-roic-review-v1")
                self.assertFalse(review.json()["ready"])
                self.assertIn("no-store", review.headers["cache-control"])
                valid = await preview(choices)
                self.assertEqual(valid.status_code, 200, valid.text)
                self.assertTrue(valid.json()["ready"])
                options = GenerateChoices(
                    **choices.model_dump(),
                    preview_hash=valid.json()["preview_hash"],
                    attempt_key=str(uuid4()),
                )

                async def submit(value, upload=content):
                    return await self.client.post(
                        "/financial-v2/generate",
                        data={"options": value.model_dump_json()},
                        files={"file": ("statements.csv", upload, "text/csv")},
                    )

                self.assertEqual(
                    (
                        await submit(options.model_copy(update={"roic_sources": None}))
                    ).status_code,
                    422,
                )
                changed = options.roic_sources.model_copy(
                    update={
                        "nopat_definition": "reported operating profit after operating taxes"
                    }
                )
                self.assertIn(
                    (
                        await submit(
                            options.model_copy(update={"roic_sources": changed})
                        )
                    ).status_code,
                    (409, 422),
                )
                self.assertIn(
                    (
                        await submit(options, content.replace(b";80;", b";81;"))
                    ).status_code,
                    (409, 422),
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                queued = await submit(options)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = (
                    await self.client.get(f"/financial-v2/reports/{run}/json")
                ).json()
                evidence = report["methodology"]["roic_sources"][0]["evidence"]
                self.assertEqual(evidence["tax_assumptions"]["quote"], TAX)
                self.assertEqual(evidence["average_invested_capital"]["quote"], AVERAGE)
                self.assertTrue(
                    any(
                        m["observation"]["metric_id"] == "roic"
                        for m in report["metric_observations"]
                    )
                )
                html = await self.client.get(f"/financial-v2/reports/{run}/html")
                self.assertIn(TAX, html.text)
                self.assertEqual(
                    (
                        await self.client.get(f"/financial-v2/reports/{run}/pdf")
                    ).status_code,
                    200,
                )
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["roic_sources"],
                        choices.roic_sources.model_dump(),
                    )
                self.assertEqual((await submit(options)).json()["job_id"], job)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )
                self.assertEqual(
                    (await self.client.get(f"/financial-v2/reports/{run}/json")).json(),
                    report,
                )

        asyncio.run(scenario())

    def test_capex_classification_binds_preview_queue_audit_report_and_replay(self):
        from capex_upload_fixtures import capex_case
        from product_fixtures import upload_case

        async def scenario():
            await self.ready()
            _, _, content, choices = capex_case(
                self.value.workspace_id, self.value.user_id
            )
            async with self.client:

                async def preview(options):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": options.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                missing = choices.model_copy(update={"capex_definitions": None})
                review = await preview(missing)
                self.assertEqual(review.status_code, 200, review.text)
                self.assertEqual(review.json()["schema"], "financial-capex-review-v1")
                self.assertFalse(review.json()["ready"])
                self.assertNotIn("calculated_metrics", review.json())
                self.assertIn("no-store", review.headers["cache-control"])
                _, financial, *_ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=choices,
                )
                valid = await preview(choices)
                self.assertEqual(valid.status_code, 200, valid.text)
                self.assertEqual(
                    valid.json()["preview_hash"], financial["preview_hash"]
                )
                options = GenerateChoices(
                    **choices.model_dump(),
                    preview_hash=financial["preview_hash"],
                    attempt_key=str(uuid4()),
                )

                async def submit(value):
                    return await self.client.post(
                        "/financial-v2/generate",
                        data={"options": value.model_dump_json()},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                rejected = await submit(
                    options.model_copy(update={"capex_definitions": None})
                )
                self.assertEqual(rejected.status_code, 422, rejected.text)
                changed = {
                    loc: (
                        quote
                        if "commitments at" in quote
                        else "cash paid for the same fixed asset purchases in the period"
                    )
                    for loc, quote in choices.capex_definitions.items()
                }
                stale = await submit(
                    options.model_copy(update={"capex_definitions": changed})
                )
                self.assertIn(stale.status_code, (409, 422), stale.text)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                queued = await submit(options)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = (
                    await self.client.get(f"/financial-v2/reports/{run}/json")
                ).json()
                self.assertEqual(len(report["methodology"]["capex_definitions"]), 5)
                self.assertEqual(
                    report["methodology"]["terminology_version"],
                    "financial-terminology-v8",
                )
                self.assertTrue(
                    {
                        "capex_cash_total",
                        "capex_maintenance_share_pct",
                        "capex_commitments_outstanding",
                    }
                    <= {
                        m["observation"]["metric_id"]
                        for m in report["metric_observations"]
                    }
                )
                quoted = choices.capex_definitions["csv:8"]
                html = await self.client.get(f"/financial-v2/reports/{run}/html")
                self.assertIn(quoted, html.text)
                self.assertEqual(
                    (
                        await self.client.get(f"/financial-v2/reports/{run}/pdf")
                    ).status_code,
                    200,
                )
                async with self.sessions.begin() as session:
                    receipt = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "financial_product",
                        )
                    )
                    self.assertTrue(
                        any(
                            r.get("capex_definition_quote") == quoted
                            for r in receipt.payload["input"]["original_rows"]
                        )
                    )
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["capex_definitions"],
                        choices.capex_definitions,
                    )
                self.assertEqual((await submit(options)).json()["job_id"], job)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_monthly_catalog_is_read_only_and_new_rule_policy_saves_and_replays(self):
        from close_catalog_fixtures import close_catalog_case

        from saleslab_v2.persistence.models import AnalysisRunModel
        from saleslab_v2.product.month_end_catalog import reconciliation_catalog
        from saleslab_v2.product.month_end_saved import SaveMonthEndChoices

        async def scenario():
            await self.ready()
            async with self.client:
                with patch(
                    "saleslab_v2.product.api.get_settings", return_value=self.settings
                ):
                    disabled = await self.client.get("/financial-v2/capabilities")
                self.assertNotIn("month_end_reconciliation_catalog", disabled.json())
                self.settings = replace(self.settings, financial_month_end_enabled=True)
                async with self.sessions.begin() as session:
                    before = await session.scalar(
                        select(func.count()).select_from(AnalysisRunModel)
                    )
                with patch(
                    "saleslab_v2.product.api.get_settings", return_value=self.settings
                ):
                    response = await self.client.get("/financial-v2/capabilities")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("no-store", response.headers["cache-control"])
                self.assertEqual(
                    response.json()["month_end_reconciliation_catalog"],
                    reconciliation_catalog(),
                )
                async with self.sessions.begin() as session:
                    self.assertEqual(
                        await session.scalar(
                            select(func.count()).select_from(AnalysisRunModel)
                        ),
                        before,
                    )
                _, choices, close, content, _ = close_catalog_case(
                    self.value.workspace_id, self.value.user_id
                )

                async def prepare(policy):
                    return await self.client.post(
                        "/financial-v2/month-end/preview",
                        data={
                            "options": choices.model_dump_json(),
                            "month_end": policy.model_dump_json(),
                        },
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                missing = await prepare(
                    close.model_copy(
                        update={"required_reconciliations": ["REC-WC-OPENING-001"]}
                    )
                )
                self.assertEqual(missing.status_code, 200, missing.text)
                self.assertEqual(
                    missing.json()["result"]["preparation_readiness"], "incomplete"
                )
                prepared = await prepare(close)
                self.assertEqual(prepared.status_code, 200, prepared.text)
                value = prepared.json()
                self.assertEqual(
                    value["result"]["preparation_readiness"], "ready_for_review"
                )
                save = SaveMonthEndChoices(
                    binding_hash=value["binding_hash"], attempt_key=str(uuid4())
                )

                async def persist():
                    return await self.client.post(
                        "/financial-v2/month-end/snapshots",
                        data={
                            "options": choices.model_dump_json(),
                            "month_end": close.model_dump_json(),
                            "save": save.model_dump_json(),
                        },
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                stored = await persist()
                self.assertEqual(stored.status_code, 201, stored.text)
                saved = stored.json()
                self.assertEqual(
                    saved["close_choices"]["required_reconciliations"],
                    close.required_reconciliations,
                )
                self.assertTrue(
                    all(
                        i["status"] == "satisfied"
                        for i in saved["result"]["checklist"]
                        if i["category"] == "reconciliation"
                    )
                )
                self.assertNotIn("month_end_reconciliation_catalog", saved)
                self.assertEqual((await persist()).json(), saved)
                self.assertEqual(
                    (
                        await self.client.get(
                            "/financial-v2/month-end/snapshots/" + saved["snapshot_id"]
                        )
                    ).json(),
                    saved,
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                denied = await self.client.get(
                    "/financial-v2/capabilities", headers={"X-Token": "invalid"}
                )
                self.assertIn(denied.status_code, (401, 403))

        asyncio.run(scenario())

    def test_monthly_snapshot_is_explicit_idempotent_and_reopens_without_quota(self):
        from month_end_upload_fixtures import month_end_case

        from saleslab_v2.persistence.models import (
            AnalysisRunModel,
            CalculationTraceModel,
            MetricObservationModel,
        )
        from saleslab_v2.product.month_end import prepare_month_end
        from saleslab_v2.product.month_end_saved import PURPOSE

        async def scenario():
            await self.ready()
            self.settings = replace(self.settings, financial_month_end_enabled=True)
            parsed, choices, close, _, content, user = month_end_case(
                self.value.workspace_id, self.value.user_id
            )
            reviewed = prepare_month_end(parsed, choices, close, user)
            attempt = str(uuid4())

            async def save(binding=None, key=attempt):
                return await self.client.post(
                    "/financial-v2/month-end/snapshots",
                    data={
                        "options": choices.model_dump_json(),
                        "month_end": close.model_dump_json(),
                        "save": json.dumps(
                            {
                                "binding_hash": binding or reviewed["binding_hash"],
                                "attempt_key": key,
                            }
                        ),
                    },
                    files={"file": ("statements.csv", content, "text/csv")},
                )

            async with self.client:
                stale = await save("0" * 64)
                self.assertEqual(stale.status_code, 409, stale.text)
                async with self.sessions() as session:
                    self.assertEqual(
                        await session.scalar(
                            select(func.count()).select_from(AnalysisRunModel)
                        ),
                        0,
                    )
                replies = await asyncio.gather(save(), save(), save())
                self.assertEqual(
                    sorted(r.status_code for r in replies),
                    [200, 200, 201],
                    [r.text for r in replies],
                )
                value = replies[0].json()
                self.assertTrue(value["persisted"])
                self.assertFalse(value["quota_consumed"])
                self.assertFalse(value["result"]["accounting_close_attested"])
                self.assertTrue(all(r.json() == value for r in replies))
                self.assertTrue(all(r.content == replies[0].content for r in replies))
                snapshot = value["snapshot_id"]
                read = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + snapshot
                )
                self.assertEqual(read.status_code, 200, read.text)
                self.assertEqual(read.json(), value)
                self.assertEqual(read.content, replies[0].content)
                from saleslab_v2.product.month_end_csv import checklist_csv

                exported = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + snapshot + "/checklist.csv"
                )
                self.assertEqual(exported.status_code, 200, exported.text)
                self.assertEqual(exported.content, checklist_csv(value))
                self.assertIn("no-store", exported.headers["cache-control"])

                self.assertIn("no-store", read.headers["cache-control"])
                history = await self.client.get("/financial-v2/month-end/snapshots")
                self.assertEqual(history.status_code, 200, history.text)
                self.assertEqual(
                    history.json()["snapshots"][0]["snapshot_id"], snapshot
                )
                self.assertNotIn(
                    "preparation_readiness", history.json()["snapshots"][0]
                )
                async with self.sessions() as session:
                    self.assertEqual(
                        await session.scalar(
                            select(func.count())
                            .select_from(AnalysisRunModel)
                            .where(AnalysisRunModel.idempotency_purpose == PURPOSE)
                        ),
                        1,
                    )
                    self.assertEqual(
                        await session.scalar(
                            select(func.count()).select_from(StageResultModel)
                        ),
                        2,
                    )
                    for model in (CalculationTraceModel, MetricObservationModel):
                        self.assertEqual(
                            await session.scalar(
                                select(func.count()).select_from(model)
                            ),
                            0,
                        )
                self.settings = replace(
                    self.settings,
                    financial_product_enabled=False,
                    financial_month_end_enabled=False,
                )
                self.assertEqual((await save()).status_code, 403)
                retained = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + snapshot
                )
                self.assertEqual(retained.json(), value, retained.text)
                retained_csv = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + snapshot + "/checklist.csv"
                )
                self.assertEqual(retained_csv.content, exported.content)

                with patch(
                    "saleslab_v2.product.api.get_settings", return_value=self.settings
                ):
                    caps_response = await self.client.get("/financial-v2/capabilities")
                    self.assertIn("no-store", caps_response.headers["cache-control"])
                    caps = caps_response.json()
                self.assertTrue(caps["month_end_snapshots_available"])
                self.assertEqual(caps["month_end_save_versions"], [])
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))

        asyncio.run(scenario())

    def test_monthly_snapshot_policy_conflicts_tampering_and_incomplete_state(self):
        from copy import deepcopy

        from month_end_upload_fixtures import month_end_case

        from saleslab_v2.product.month_end import prepare_month_end
        from saleslab_v2.product.month_end_saved import STAGES, stage_id

        async def scenario():
            await self.ready()
            self.settings = replace(self.settings, financial_month_end_enabled=True)
            parsed, choices, close, _, content, user = month_end_case(
                self.value.workspace_id, self.value.user_id
            )
            close = close.model_copy(update={"confirmations": []})
            reviewed = prepare_month_end(parsed, choices, close, user)
            attempt = str(uuid4())

            async def save(policy=close, binding=reviewed["binding_hash"], key=attempt):
                return await self.client.post(
                    "/financial-v2/month-end/snapshots",
                    data={
                        "options": choices.model_dump_json(),
                        "month_end": policy.model_dump_json(),
                        "save": json.dumps(
                            {"binding_hash": binding, "attempt_key": key}
                        ),
                    },
                    files={"file": ("statements.csv", content, "text/csv")},
                )

            async with self.client:
                response = await save()
                self.assertEqual(response.status_code, 201, response.text)
                value = response.json()
                sid = UUID(value["snapshot_id"])
                self.assertEqual(value["result"]["preparation_readiness"], "incomplete")
                changed = close.model_copy(update={"absolute_variation_threshold": "1"})
                other = prepare_month_end(parsed, choices, changed, user)
                conflict = await save(changed, other["binding_hash"])
                self.assertEqual(conflict.status_code, 409, conflict.text)
                async with self.sessions.begin() as session:
                    row = await session.get(StageResultModel, stage_id(sid, STAGES[1]))
                    original = deepcopy(row.payload)
                    payload = deepcopy(original)
                    payload["result"]["comparisons"][0]["delta"] = "999"
                    payload["payload_hash"] = semantic_hash(
                        {k: v for k, v in payload.items() if k != "payload_hash"}
                    )
                    row.payload = payload
                    row.output_hash = semantic_hash(payload)
                rejected = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + str(sid)
                )
                self.assertEqual(rejected.status_code, 409, rejected.text)
                rejected_csv = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + str(sid) + "/checklist.csv"
                )
                self.assertEqual(rejected_csv.status_code, 409, rejected_csv.text)

                async with self.sessions.begin() as session:
                    row = await session.get(StageResultModel, stage_id(sid, STAGES[1]))
                    row.payload = original
                    row.output_hash = semantic_hash(original)
                self.assertEqual(
                    (
                        await self.client.get(
                            "/financial-v2/month-end/snapshots/" + str(sid)
                        )
                    ).json(),
                    value,
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))

        asyncio.run(scenario())

    def test_monthly_snapshot_scope_role_cursor_and_hourly_limit(self):
        from month_end_upload_fixtures import month_end_case

        from models import User
        from saleslab_v2.product.month_end import prepare_month_end

        async def scenario():
            await self.ready()
            self.settings = replace(self.settings, financial_month_end_enabled=True)
            parsed, choices, close, _, content, user = month_end_case(
                self.value.workspace_id, self.value.user_id
            )
            reviewed = prepare_month_end(parsed, choices, close, user)

            async def save(key):
                return await self.client.post(
                    "/financial-v2/month-end/snapshots",
                    data={
                        "options": choices.model_dump_json(),
                        "month_end": close.model_dump_json(),
                        "save": json.dumps(
                            {
                                "binding_hash": reviewed["binding_hash"],
                                "attempt_key": key,
                            }
                        ),
                    },
                    files={"file": ("statements.csv", content, "text/csv")},
                )

            async with self.client:
                key = str(uuid4())
                created = await save(key)
                self.assertEqual(created.status_code, 201, created.text)
                sid = created.json()["snapshot_id"]
                with patch(
                    "saleslab_v2.product.month_end_saved.MAX_ATTEMPTS_PER_HOUR", 1
                ):
                    self.assertEqual((await save(str(uuid4()))).status_code, 429)
                    self.assertEqual((await save(key)).status_code, 200)
                with patch("saleslab_v2.product.month_end_saved.PAGE_SIZE", 1):
                    second = await save(str(uuid4()))
                    self.assertEqual(second.status_code, 201, second.text)
                    page = (
                        await self.client.get("/financial-v2/month-end/snapshots")
                    ).json()
                    self.assertEqual(len(page["snapshots"]), 1)
                    self.assertIsNotNone(page["next_cursor"])
                    next_page = (
                        await self.client.get(
                            "/financial-v2/month-end/snapshots?cursor="
                            + page["next_cursor"]
                        )
                    ).json()
                    self.assertEqual(len(next_page["snapshots"]), 1)
                    self.assertNotEqual(
                        page["snapshots"][0]["snapshot_id"],
                        next_page["snapshots"][0]["snapshot_id"],
                    )
                    self.assertIsNone(next_page["next_cursor"])
                other_ws, other_user = uuid4(), uuid4()
                async with self.sessions.begin() as session:
                    session.add(
                        Workspace(id=other_ws, company_name="Other monthly workspace")
                    )
                    session.add(
                        User(
                            id=other_user,
                            email="monthly-other@example.test",
                            password_hash="x",
                        )
                    )
                    await session.flush()
                    session.add(
                        WorkspaceMember(
                            workspace_id=other_ws, user_id=other_user, role="owner"
                        )
                    )
                self.settings = replace(
                    self.settings,
                    internal_workspaces=self.settings.internal_workspaces
                    | {str(other_ws)},
                )
                owner_token = self.client.headers["X-Token"]
                self.client.headers["X-Token"] = issue_session_token(
                    other_user, other_ws, "owner"
                )
                self.assertEqual(
                    (
                        await self.client.get(
                            "/financial-v2/month-end/snapshots/" + sid
                        )
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    (await self.client.get("/financial-v2/month-end/snapshots")).json()[
                        "snapshots"
                    ],
                    [],
                )
                self.assertEqual(
                    (
                        await self.client.get(
                            "/financial-v2/month-end/snapshots?cursor=" + sid
                        )
                    ).status_code,
                    404,
                )

                foreign_csv = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + sid + "/checklist.csv"
                )
                self.assertEqual(foreign_csv.status_code, 404)
                self.client.headers["X-Token"] = owner_token
                async with self.sessions.begin() as session:
                    member = await session.scalar(
                        select(WorkspaceMember).where(
                            WorkspaceMember.workspace_id == self.value.workspace_id,
                            WorkspaceMember.user_id == user,
                        )
                    )
                    member.role = "member"
                self.assertIn(
                    (
                        await self.client.get(
                            "/financial-v2/month-end/snapshots/" + sid
                        )
                    ).status_code,
                    (403, 404),
                )

                revoked_csv = await self.client.get(
                    "/financial-v2/month-end/snapshots/" + sid + "/checklist.csv"
                )
                self.assertIn(revoked_csv.status_code, (403, 404))
                self.assertEqual((await save(str(uuid4()))).status_code, 403)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))

        asyncio.run(scenario())

    def test_month_end_upload_is_flagged_source_bound_and_does_not_persist_or_charge(
        self,
    ):
        from month_end_upload_fixtures import month_end_case

        from saleslab_v2.persistence.models import (
            AnalysisRunModel,
            CalculationTraceModel,
            MetricObservationModel,
        )

        async def scenario():
            await self.ready()
            _, choices, close, _, content, _ = month_end_case(
                self.value.workspace_id, self.value.user_id
            )

            async def records():
                async with self.sessions.begin() as session:
                    return tuple(
                        [
                            await session.scalar(
                                select(func.count()).select_from(model)
                            )
                            for model in (
                                AnalysisRunModel,
                                StageResultModel,
                                MetricObservationModel,
                                CalculationTraceModel,
                            )
                        ]
                    )

            async def submit(policy=None, upload=None, headers=None):
                return await self.client.post(
                    "/financial-v2/month-end/preview",
                    data={
                        "options": choices.model_dump_json(),
                        "month_end": json.dumps(policy or close.model_dump()),
                    },
                    files={"file": ("statements.csv", upload or content, "text/csv")},
                    headers=headers,
                )

            async with self.client:
                self.assertEqual((await submit()).status_code, 403)
                self.settings = replace(self.settings, financial_month_end_enabled=True)
                with patch(
                    "saleslab_v2.product.api.get_settings", return_value=self.settings
                ):
                    caps = (await self.client.get("/financial-v2/capabilities")).json()
                self.assertEqual(caps["month_end_preview_versions"], ["1"])
                before = await records()
                response = await submit()
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("no-store", response.headers["cache-control"])
                value = response.json()
                self.assertEqual(
                    value["result"]["preparation_readiness"], "ready_for_review"
                )
                self.assertEqual(
                    value["result"]["requested_by_user_id"], str(self.value.user_id)
                )
                self.assertEqual(len(value["result"]["calculations"]), 6)
                self.assertFalse(value["persisted"])
                replay = (await submit()).json()
                self.assertEqual(replay["binding_hash"], value["binding_hash"])
                missing = await submit({**close.model_dump(), "confirmations": []})
                self.assertEqual(
                    missing.json()["result"]["preparation_readiness"], "incomplete"
                )
                stale = await submit(upload=content.replace(b"1100", b"1200"))
                self.assertEqual(stale.status_code, 422, stale.text)
                outside = await submit(
                    {
                        **close.model_dump(),
                        "confirmations": [
                            {
                                "task_id": "review_sources",
                                "status": "complete",
                                "evidence": ["other-workspace:1"],
                            }
                        ],
                    }
                )
                self.assertEqual(outside.status_code, 422, outside.text)
                invalid = await submit(
                    {**close.model_dump(), "absolute_variation_threshold": "NaN"}
                )
                self.assertEqual(invalid.status_code, 422, invalid.text)
                denied = await submit(headers={"X-Token": "invalid"})
                self.assertIn(denied.status_code, (401, 403))
                self.assertEqual(await records(), before)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))
                async with self.sessions.begin() as session:
                    await session.execute(
                        text(
                            "UPDATE workspace_members SET role='member' WHERE workspace_id=:w AND user_id=:u"
                        ),
                        {"w": self.value.workspace_id, "u": self.value.user_id},
                    )
                self.assertEqual((await submit()).status_code, 403)

        asyncio.run(scenario())

    def test_month_end_upload_requires_confirmed_pdf_source_and_retains_tabular_origins(
        self,
    ):
        from month_end_upload_fixtures import month_end_case
        from pdf_fixtures import pending_pdf
        from product_fixtures import upload_case
        from tabular_fixtures import import_choices, workbook_content

        async def scenario():
            await self.ready()
            self.settings = replace(self.settings, financial_month_end_enabled=True)
            _, _, close, _, _, _ = month_end_case(
                self.value.workspace_id, self.value.user_id
            )
            async with self.client:
                content, options, _ = pending_pdf()
                blocked = await self.client.post(
                    "/financial-v2/month-end/preview",
                    data={
                        "options": options.model_dump_json(),
                        "month_end": close.model_dump_json(),
                    },
                    files={"file": ("statements.pdf", content, "application/pdf")},
                )
                self.assertEqual(blocked.status_code, 422, blocked.text)
                content = workbook_content()
                options = import_choices(content)
                preview = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    filename="erp.xlsx",
                    choices=options,
                )[1]
                close = close.model_copy(
                    update={
                        "preview_hash": preview["preview_hash"],
                        "comparisons": [],
                        "movements": [],
                        "confirmations": [],
                    }
                )
                result = await self.client.post(
                    "/financial-v2/month-end/preview",
                    data={
                        "options": options.model_dump_json(),
                        "month_end": close.model_dump_json(),
                    },
                    files={"file": ("erp.xlsx", content)},
                )
                self.assertEqual(result.status_code, 200, result.text)
                value = result.json()
                self.assertEqual(value["result"]["preparation_readiness"], "incomplete")
                self.assertEqual(
                    value["source_import"]["version"], "financial-tabular-v1"
                )
                self.assertIn("source_cells", value["original_rows"][0])
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual((self.model.calls, self.critic.calls), ([], []))

        asyncio.run(scenario())

    def test_explicit_multisheet_import_is_bound_through_job_report_and_receipt(self):
        from hashlib import sha256

        from tabular_fixtures import import_choices, workbook_content

        async def scenario():
            await self.ready()
            content = workbook_content()
            opts = import_choices(content)
            async with self.client:
                response = await self.client.post(
                    "/financial-v2/inspect",
                    files={
                        "file": (
                            "erp.xlsx",
                            content,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("no-store", response.headers["cache-control"])
                self.assertEqual(
                    response.json()["inspection_hash"],
                    opts.tabular_import.inspection_hash,
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                _, preview, choices, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    filename="erp.xlsx",
                    choices=opts,
                )
                self.assertTrue(preview["ready"])

                async def submit(value):
                    return await self.client.post(
                        "/financial-v2/generate",
                        data={"options": value.model_dump_json()},
                        files={
                            "file": (
                                "erp.xlsx",
                                content,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            )
                        },
                    )

                altered = choices.model_copy(deep=True)
                # A different header can produce the same selected numbers but must not reuse approval.
                value = altered.tabular_import.model_dump()
                value["tables"][0]["header_row"] = 2
                from saleslab_v2.product.tabular_contracts import TabularImport

                altered = altered.model_copy(
                    update={"tabular_import": TabularImport.model_validate(value)}
                )
                rejected = await submit(altered)
                self.assertEqual(rejected.status_code, 409, rejected.text)
                queued = await submit(choices)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = (
                    await self.client.get(f"/financial-v2/reports/{run}/json")
                ).json()
                self.assertEqual(
                    report["source_index"][0]["parser_version"], "financial-tabular-v1"
                )
                self.assertEqual(
                    report["source_index"][0]["content_hash"],
                    sha256(content).hexdigest(),
                )
                refs = {
                    ref["location"]
                    for e in report["evidence"]
                    for ref in e["source_references"]
                }
                self.assertIn("s1!C4", refs)
                self.assertIn("s1!C5", refs)
                async with self.sessions.begin() as session:
                    receipt = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "financial_product",
                        )
                    )
                    imported = receipt.payload["input"]["source_import"]
                    self.assertEqual(imported["excluded_sheets"][0]["sheet"], 3)
                    self.assertEqual(
                        receipt.payload["input"]["original_rows"][0]["source_cells"][
                            "amount"
                        ],
                        {"cell": "C4"},
                    )
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["tabular_import"]["inspection_hash"],
                        opts.tabular_import.inspection_hash,
                    )
                for channel in ("html", "pdf"):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{run}/{channel}"
                            )
                        ).status_code,
                        200,
                    )
                self.assertEqual((await submit(choices)).json()["job_id"], job)
                from saleslab_v2.product.ingestion import parse_upload
                from saleslab_v2.product.service import FinancialProduct

                async with self.sessions.begin() as session:
                    parsed = parse_upload(
                        self.value.workspace_id, "erp.xlsx", content, choices
                    )
                    replay = await FinancialProduct(
                        session, self.settings, self.model, self.critic
                    ).generate(parsed, choices, self.value.user_id)
                    self.assertTrue(replay["replayed"])
                    self.assertEqual(replay["status"], "completed")
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )
                self.client.headers["X-Token"] = "invalid"
                denied = await self.client.post(
                    "/financial-v2/inspect", files={"file": ("erp.xlsx", content)}
                )
                self.assertIn(denied.status_code, (401, 403))

        asyncio.run(scenario())

    def test_capital_bundle_preserves_calendar_days_variants_selection_and_all_report_evidence(
        self,
    ):
        from decimal import Decimal

        from capital_fixtures import capital_choices, capital_content

        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            self.model.transform = lambda raw, context: {
                **raw,
                "findings": raw["findings"][:20],
            }
            opts = capital_choices(capital_rounding_tolerance="0.01")
            content = capital_content()
            async with self.client:
                _, preview, generate, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=opts,
                )
                self.assertTrue(preview["ready"])
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                changed = generate.model_copy(update={"planning_version": "7"})
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 422
                )
                changed = generate.model_copy(
                    update={"capital_rounding_tolerance": "0"}
                )
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(saved.payload["choices"]["planning_version"], "8")
                    self.assertEqual(
                        saved.payload["choices"]["capital_rounding_tolerance"], "0.01"
                    )
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                response = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(response.status_code, 200, response.text)
                report = response.json()
                self.assertEqual(
                    report["methodology"]["terminology_version"],
                    "financial-terminology-v5",
                )
                self.assertEqual(report["render_version"], "financial-render-v4")
                self.assertEqual(len(report["metric_observations"]), 63)
                self.assertEqual(len(report["evidence"]), 33)
                self.assertIn(
                    "days_for_dso",
                    report["methodology"]["evidence_selection"]["excluded_keys"],
                )
                self.assertEqual(len(self.model.calls[0]["evidence"]), 33)
                self.assertFalse(
                    any(
                        e["concept_id"] == "days_in_period"
                        for e in self.model.calls[0]["evidence"]
                    )
                )
                observations = [
                    (unpack(m["observation"]), m["trace"])
                    for m in report["metric_observations"]
                ]
                cycle = next(
                    (o, t)
                    for o, t in observations
                    if o["metric_id"] == "cash_conversion_cycle"
                )
                self.assertEqual(Decimal(cycle[0]["value"]), Decimal("63.875"))
                self.assertEqual(cycle[1]["canonical_expression"], "dso+dio-dpo")
                days = [
                    (o, t)
                    for o, t in observations
                    if o["metric_id"] == "days_in_period"
                ]
                self.assertEqual(len(days), 3)
                self.assertTrue(all(Decimal(o["value"]) == 365 for o, t in days))
                self.assertTrue(
                    all(
                        t["canonical_expression"]
                        == "inclusive_calendar_days(period_anchor.start_date,period_anchor.end_date)"
                        for o, t in days
                    )
                )
                debt = [
                    (o, t) for o, t in observations if o["metric_id"] == "gross_debt"
                ]
                self.assertEqual(
                    {o["metric_definition_version"] for o, t in debt}, {"2"}
                )
                self.assertEqual(
                    {Decimal(o["value"]) for o, t in debt}, {Decimal(500), Decimal(600)}
                )
                ratios = [
                    (o, t)
                    for o, t in observations
                    if o["metric_id"] == "net_debt_to_ebitda"
                ]
                self.assertEqual(len(ratios), 4)
                self.assertEqual(
                    {t["inputs"][0]["variant"] for o, t in ratios},
                    {"excluding_leases", "including_leases"},
                )
                self.assertEqual(
                    {t["inputs"][1]["variant"] for o, t in ratios},
                    {"statutory", "adjusted"},
                )
                bridges = {r["rule_id"]: r for r in report["reconciliations"]}
                self.assertEqual(bridges["REC-DEBT-001"]["status"], "pass")
                self.assertEqual(bridges["REC-LEASE-001"]["status"], "pass")
                self.assertTrue(
                    bridges["REC-LEASE-001"]["capability_effects"][0]["blocked"]
                )
                self.assertEqual(report["forecasts"], [])
                self.assertEqual(report["recommendations"], [])
                async with self.sessions.begin() as session:
                    normalized = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "normalization",
                        )
                    )
                    saved_request = normalized.payload["request"]
                    self.assertEqual(
                        saved_request["sources"][0]["metadata"]["terminology_version"],
                        "5",
                    )
                    self.assertEqual(len(saved_request["analyst_metric_keys"]), 33)
                for channel in ("html", "pdf"):
                    response = await self.client.get(
                        f"/financial-v2/reports/{run}/{channel}"
                    )
                    self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )
                bad_content = capital_content(
                    changes={"debt_excluding_leases": {"amount": "601"}}
                )
                _, blocked, bad_generate, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=bad_content,
                    choices=opts,
                )
                self.assertFalse(blocked["ready"])
                rejected = await self.post(choices=bad_generate, content=bad_content)
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_exact_normalization_is_pinned_across_worker_precision_and_report_replay(
        self,
    ):
        from decimal import Decimal, localcontext

        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            digits = "123456789012345678901234567890.123456789"
            original = "(" + digits + ")"
            content = csv_content(
                extra=[
                    (
                        "cash flow from investing",
                        original,
                        "2026",
                        "cash_flow_statement",
                        "consolidated",
                        "EUR",
                        "thousands",
                        "current",
                    )
                ]
            )
            opts = UploadChoices(
                source_language="en",
                format_locale="en-GB",
                report_language="pt",
                planning_version="4",
                accounting_parentheses=True,
                normalization_version="2",
            )
            async with self.client:
                caps = (await self.client.get("/financial-v2/capabilities")).json()
                self.assertEqual(caps["normalization_versions"], ["1", "2"])
                self.assertNotIn(
                    "normalization_version",
                    (await self.client.get("/financial-v2/sample")).json()["choices"],
                )
                with localcontext() as context:
                    context.prec = 28
                    response = await self.post(
                        path="preview", choices=opts, content=content
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.json()["ready"])
                    generate = GenerateChoices(
                        **opts.model_dump(),
                        preview_hash=response.json()["preview_hash"],
                        attempt_key=str(uuid4()),
                    )
                    queued = await self.post(choices=generate, content=content)
                    self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["normalization_version"], "2"
                    )
                with localcontext() as context:
                    context.prec = 100
                    changed = generate.model_copy(update={"normalization_version": "1"})
                    self.assertEqual(
                        (await self.post(choices=changed, content=content)).status_code,
                        409,
                    )
                with localcontext() as context:
                    context.prec = 6
                    await self.work()
                with localcontext() as context:
                    context.prec = 28
                    done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                    self.assertEqual(done["status"], "completed", done)
                    run = done["run_id"]
                    report = await self.client.get(f"/financial-v2/reports/{run}/json")
                    self.assertEqual(report.status_code, 200, report.text)
                    metric = next(
                        m
                        for m in report.json()["metric_observations"]
                        if m["observation"]["metric_id"] == "cfi"
                    )
                    actual = Decimal(unpack(metric["observation"])["value"])
                    for channel in ("html", "pdf"):
                        self.assertEqual(
                            (
                                await self.client.get(
                                    f"/financial-v2/reports/{run}/{channel}"
                                )
                            ).status_code,
                            200,
                        )
                with localcontext() as context:
                    context.prec = 100
                    self.assertEqual(actual, Decimal("-" + digits) * 1000)
                async with self.sessions.begin() as session:
                    stage = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "normalization",
                        )
                    )
                    self.assertEqual(
                        stage.payload["request"]["sources"][0]["metadata"][
                            "normalization_version"
                        ],
                        "2",
                    )
                    row = next(
                        r
                        for r in stage.payload["observations"]
                        if r["concept_id"] == "cfi"
                    )
                    self.assertEqual(row["parser_version"], "financial-normalizer-v2")
                    self.assertEqual(row["original_text"], original)
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_version_four_cash_flows_are_pinned_through_resume_and_report_replay(self):
        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            content = csv_content(
                extra=[
                    (
                        "cash flow from investing",
                        "(120)",
                        "2026",
                        "cash_flow_statement",
                        "consolidated",
                        "EUR",
                        "thousands",
                        "current",
                    ),
                    (
                        "cash flow from financing",
                        "40",
                        "2026",
                        "cash_flow_statement",
                        "consolidated",
                        "EUR",
                        "thousands",
                        "current",
                    ),
                ]
            )
            async with self.client:
                opts = UploadChoices(
                    source_language="en",
                    format_locale="en-GB",
                    report_language="pt",
                    planning_version="4",
                    accounting_parentheses=True,
                )
                parsed, preview, generate, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    content=content,
                    choices=opts,
                )
                from saleslab_v2.product.planning import prepare_upload

                _, old = prepare_upload(
                    parsed,
                    opts.model_copy(update={"planning_version": "3"}),
                    self.value.user_id,
                    "preview",
                )
                self.assertNotIn("cfi", old["planned_metrics"])
                self.assertNotIn("cff", old["planned_metrics"])
                self.assertTrue(preview["ready"])
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                changed = generate.model_copy(update={"planning_version": "3"})
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(saved.payload["choices"]["planning_version"], "4")
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                metrics = {
                    m["observation"]["metric_id"]: m
                    for m in report.json()["metric_observations"]
                }
                for concept, amount, original, location in (
                    ("cfi", "-120000", "(120)", "csv:7"),
                    ("cff", "40000", "40", "csv:8"),
                ):
                    row = metrics[concept]
                    observation = unpack(row["observation"])
                    self.assertEqual(observation["value"], amount)
                    self.assertEqual(observation["metric_definition_version"], "1")
                    self.assertEqual(observation["source_references"], [location])
                    self.assertEqual(
                        row["trace"]["canonical_expression"], f"reported_{concept}"
                    )
                    source = next(
                        r for r in parsed.original_rows if r["location"] == location
                    )
                    self.assertEqual(source["amount"], original)
                    self.assertEqual(source["scale"], "thousands")
                for channel in ("html", "pdf"):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{run}/{channel}"
                            )
                        ).status_code,
                        200,
                    )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_source_currency_scale_confirmation_survives_queue_and_report(self):
        import base64

        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            concepts = (
                "revenue",
                "net_income",
                "total_assets",
                "total_liabilities",
                "equity",
            )
            content = csv_content(
                changes={c: {"currency": "", "scale": ""} for c in concepts}
            )
            options = {
                "source_language": "en",
                "format_locale": "en-GB",
                "report_language": "pt",
            }
            async with self.client:
                caps = (await self.client.get("/financial-v2/capabilities")).json()
                self.assertTrue(caps["source_metadata_supported"])
                self.assertIn("EUR", caps["source_currencies"])
                sample_choices = (await self.client.get("/financial-v2/sample")).json()[
                    "choices"
                ]
                self.assertNotIn("source_currency", sample_choices)
                self.assertNotIn("source_scale", sample_choices)

                async def preview(opts):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": json.dumps(opts)},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                unconfirmed = await preview(options)
                self.assertEqual(unconfirmed.status_code, 200, unconfirmed.text)
                self.assertFalse(unconfirmed.json()["ready"])
                self.assertEqual(
                    (await preview({**options, "source_currency": "XXX"})).status_code,
                    422,
                )
                confirmed = await preview(
                    {**options, "source_currency": "EUR", "source_scale": "thousands"}
                )
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertTrue(confirmed.json()["ready"])
                self.assertEqual(
                    unconfirmed.json()["source_hash"], confirmed.json()["source_hash"]
                )
                self.assertTrue(
                    all(
                        r["original_input"]["currency"]
                        == r["original_input"]["scale"]
                        == ""
                        for r in confirmed.json()["rows"]
                    )
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                generate = GenerateChoices(
                    **options,
                    source_currency="EUR",
                    source_scale="thousands",
                    preview_hash=confirmed.json()["preview_hash"],
                    attempt_key=str(uuid4()),
                )
                stale = generate.model_copy(
                    update={"preview_hash": unconfirmed.json()["preview_hash"]}
                )
                self.assertEqual(
                    (await self.post(choices=stale, content=content)).status_code, 409
                )
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(saved.payload["choices"]["source_currency"], "EUR")
                    self.assertEqual(
                        saved.payload["choices"]["source_scale"], "thousands"
                    )
                    self.assertEqual(
                        base64.b64decode(saved.payload["content"]), content
                    )
                for change in (
                    {"source_currency": "USD"},
                    {"source_scale": "millions"},
                ):
                    changed = generate.model_copy(update=change)
                    self.assertEqual(
                        (await self.post(choices=changed, content=content)).status_code,
                        409,
                    )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                await self.work()
                done = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(done["status"], "completed", done)
                run = done["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                metrics = {
                    m["observation"]["metric_id"]: unpack(m["observation"])
                    for m in report.json()["metric_observations"]
                }
                self.assertEqual(metrics["revenue"]["value"], "1000000")
                self.assertEqual(metrics["revenue"]["currency"], "EUR")
                self.assertEqual(metrics["net_margin_pct"]["value"], "10.0")
                async with self.sessions.begin() as session:
                    stage = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "normalization",
                        )
                    )
                    source = stage.payload["request"]["sources"][0]
                    self.assertEqual(source["metadata"]["currency"], "EUR")
                    self.assertEqual(source["metadata"]["scale"], "thousands")
                    self.assertTrue(
                        all(
                            r["currency"] is None and r["scale"] is None
                            for r in source["rows"]
                        )
                    )
                for channel in ("html", "pdf"):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{run}/{channel}"
                            )
                        ).status_code,
                        200,
                    )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_fiscal_calendar_is_bound_to_durable_queue_and_report_dates(self):
        import base64

        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            concepts = (
                "revenue",
                "net_income",
                "total_assets",
                "total_liabilities",
                "equity",
            )
            content = csv_content(
                changes={c: {"period": "FY 2026"} for c in concepts},
                extra=[
                    (
                        "revenue",
                        "800",
                        "FY 2025",
                        "income_statement",
                        "consolidated",
                        "EUR",
                        "raw",
                        "comparative",
                    )
                ],
            )
            options = {
                "source_language": "en",
                "format_locale": "en-GB",
                "report_language": "pt",
                "planning_version": "3",
            }
            async with self.client:
                capabilities = (
                    await self.client.get("/financial-v2/capabilities")
                ).json()
                self.assertTrue(capabilities["fiscal_calendar_supported"])
                self.assertNotIn(
                    "fiscal_year_end_month",
                    (await self.client.get("/financial-v2/sample")).json()["choices"],
                )

                async def preview(opts):
                    return await self.client.post(
                        "/financial-v2/preview",
                        data={"options": json.dumps(opts)},
                        files={"file": ("statements.csv", content, "text/csv")},
                    )

                unconfirmed = await preview(options)
                self.assertEqual(unconfirmed.status_code, 200, unconfirmed.text)
                self.assertFalse(unconfirmed.json()["ready"])
                invalid = await preview({**options, "fiscal_year_end_month": True})
                self.assertEqual(invalid.status_code, 422)
                confirmed = await preview({**options, "fiscal_year_end_month": 6})
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertTrue(confirmed.json()["ready"])
                self.assertEqual(
                    confirmed.json()["source_hash"], unconfirmed.json()["source_hash"]
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                generate = GenerateChoices(
                    **options,
                    fiscal_year_end_month=6,
                    preview_hash=confirmed.json()["preview_hash"],
                    attempt_key=str(uuid4()),
                )
                stale = generate.model_copy(
                    update={"preview_hash": unconfirmed.json()["preview_hash"]}
                )
                self.assertEqual(
                    (await self.post(choices=stale, content=content)).status_code, 409
                )
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertEqual(
                        saved.payload["choices"]["fiscal_year_end_month"], 6
                    )
                    self.assertEqual(
                        base64.b64decode(saved.payload["content"]), content
                    )
                changed = generate.model_copy(update={"fiscal_year_end_month": 12})
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                await self.work()
                result = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(result["status"], "completed", result)
                run = result["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                metrics = {
                    m["observation"]["metric_id"]: unpack(m["observation"])
                    for m in report.json()["metric_observations"]
                }
                self.assertEqual(
                    metrics["revenue_growth_reported_pct"]["value"], "25.00"
                )
                for metric in metrics.values():
                    period = metric["period"]
                    self.assertEqual(
                        (period["start_date"], period["end_date"]),
                        ("2025-07-01", "2026-06-30"),
                    )
                    self.assertEqual(
                        (period["fiscal_year"], period["fiscal_year_end_month"]),
                        (2026, 6),
                    )
                async with self.sessions.begin() as session:
                    normalized = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "normalization",
                        )
                    )
                    rows = normalized.payload["request"]["sources"][0]["rows"]
                    self.assertTrue(all(r["fiscal_year_end_month"] == 6 for r in rows))
                for channel in ("html", "pdf"):
                    response = await self.client.get(
                        f"/financial-v2/reports/{run}/{channel}"
                    )
                    self.assertEqual(
                        response.status_code,
                        200,
                        response.text if channel == "html" else "pdf",
                    )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_parenthesized_cash_outflow_confirmation_is_bound_to_queue_and_report(self):
        import base64

        from saleslab_v2.analyst.context import unpack

        async def scenario():
            await self.ready()
            content = csv_content(
                extra=[
                    (
                        "cash flow from operations",
                        "300",
                        "2026",
                        "cash_flow_statement",
                        "consolidated",
                        "EUR",
                        "raw",
                        "current",
                    ),
                    (
                        "purchases of property plant and equipment",
                        "(80)",
                        "2026",
                        "cash_flow_statement",
                        "consolidated",
                        "EUR",
                        "raw",
                        "current",
                    ),
                ]
            )
            options = {
                "source_language": "en",
                "format_locale": "en-GB",
                "report_language": "pt",
                "planning_version": "3",
            }
            files = {"file": ("statements.csv", content, "text/csv")}
            async with self.client:
                capabilities = (
                    await self.client.get("/financial-v2/capabilities")
                ).json()
                self.assertTrue(capabilities["accounting_parentheses_supported"])
                self.assertNotIn(
                    "accounting_parentheses",
                    (await self.client.get("/financial-v2/sample")).json()["choices"],
                )
                unconfirmed = await self.client.post(
                    "/financial-v2/preview",
                    data={"options": json.dumps(options)},
                    files=files,
                )
                self.assertEqual(unconfirmed.status_code, 200, unconfirmed.text)
                self.assertFalse(unconfirmed.json()["ready"])
                confirmed = await self.client.post(
                    "/financial-v2/preview",
                    data={
                        "options": json.dumps(
                            {**options, "accounting_parentheses": True}
                        )
                    },
                    files=files,
                )
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertTrue(confirmed.json()["ready"])
                self.assertEqual(
                    confirmed.json()["source_hash"], unconfirmed.json()["source_hash"]
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                generate = GenerateChoices(
                    **options,
                    accounting_parentheses=True,
                    preview_hash=confirmed.json()["preview_hash"],
                    attempt_key=str(uuid4()),
                )
                stale = generate.model_copy(
                    update={"preview_hash": unconfirmed.json()["preview_hash"]}
                )
                self.assertEqual(
                    (await self.post(choices=stale, content=content)).status_code, 409
                )
                queued = await self.post(choices=generate, content=content)
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                async with self.sessions.begin() as session:
                    saved = await session.get(
                        StageResultModel, stage_id(UUID(job), "input")
                    )
                    self.assertIs(
                        saved.payload["choices"]["accounting_parentheses"], True
                    )
                    self.assertEqual(
                        base64.b64decode(saved.payload["content"]), content
                    )
                changed = generate.model_copy(update={"accounting_parentheses": False})
                self.assertEqual(
                    (await self.post(choices=changed, content=content)).status_code, 409
                )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                await self.work()
                result = (await self.client.get("/financial-v2/jobs/" + job)).json()
                self.assertEqual(result["status"], "completed", result)
                run = result["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                fcf = next(
                    m
                    for m in report.json()["metric_observations"]
                    if m["observation"]["metric_id"] == "free_cash_flow"
                )
                self.assertEqual(unpack(fcf["observation"])["value"], "220")
                self.assertEqual(fcf["trace"]["inputs"][1]["value"], "-80")
                async with self.sessions.begin() as session:
                    normalized = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(run),
                            StageResultModel.stage_name == "normalization",
                        )
                    )
                    self.assertIs(
                        normalized.payload["request"]["sources"][0]["metadata"][
                            "accounting_parentheses"
                        ],
                        True,
                    )
                for channel in ("html", "pdf"):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{run}/{channel}"
                            )
                        ).status_code,
                        200,
                    )
                self.assertEqual(
                    (await self.post(choices=generate, content=content)).json()[
                        "job_id"
                    ],
                    job,
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )

        asyncio.run(scenario())

    def test_public_case_runs_through_authenticated_upload_audit_and_all_report_formats(
        self,
    ):
        async def scenario():
            await self.ready()
            async with self.client:
                response = await self.client.get("/financial-v2/sample")
                self.assertEqual(response.status_code, 200, response.text)
                case = response.json()
                self.assertEqual(case["sha256"], SOURCE_SHA256)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                files = {
                    "file": (case["filename"], case["content"].encode(), "text/csv")
                }
                preview = await self.client.post(
                    "/financial-v2/preview",
                    data={"options": json.dumps(case["choices"])},
                    files=files,
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertTrue(preview.json()["ready"])
                validation = preview.json()["validation_summary"]
                self.assertEqual(validation["reconciliations"], [])
                self.assertEqual(
                    next(
                        d
                        for d in validation["capabilities"]
                        if d["capability"] == "forecast"
                    )["status"],
                    "blocked",
                )
                metrics = {
                    m["metric_id"]: m for m in preview.json()["calculated_metrics"]
                }
                self.assertEqual(metrics["revenue"]["exact_value"], "33464000000")
                self.assertTrue(
                    metrics["net_margin_pct"]["exact_value"].startswith("1.810901")
                )
                self.assertEqual(metrics["cfo"]["exact_value"], "1654000000")
                growth = metrics["revenue_growth_reported_pct"]["calculation_evidence"]
                self.assertEqual(
                    [i["value"] for i in growth["inputs"]],
                    ["33464000000", "30608000000"],
                )
                self.assertEqual(growth["inputs"][0]["sources"][0]["scale"], "millions")
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                self.assertEqual(self.critic.calls, [])
                detailed = await self.client.post(
                    "/financial-v2/preview",
                    data={
                        "options": json.dumps(
                            {**case["choices"], "planning_version": "3"}
                        )
                    },
                    files=files,
                )
                self.assertEqual(detailed.status_code, 200, detailed.text)
                self.assertTrue(detailed.json()["ready"])
                availability = {
                    m["metric_id"]: m for m in detailed.json()["metric_availability"]
                }
                self.assertEqual(
                    availability["revenue_growth_reported_pct"]["status"], "calculated"
                )
                self.assertEqual(
                    [
                        r["concept_id"]
                        for r in availability["free_cash_flow"]["requirements"]
                    ],
                    ["capex"],
                )
                self.assertEqual(
                    availability["free_cash_flow"]["requirements"][0]["status"],
                    "missing",
                )
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])
                self.assertEqual(self.critic.calls, [])
                choices = GenerateChoices(
                    **case["choices"],
                    preview_hash=preview.json()["preview_hash"],
                    attempt_key=str(uuid4()),
                )
                queued = await self.client.post(
                    "/financial-v2/generate",
                    data={"options": choices.model_dump_json()},
                    files=files,
                )
                self.assertEqual(queued.status_code, 202, queued.text)
                await self.work()
                result = (
                    await self.client.get(
                        "/financial-v2/jobs/" + queued.json()["job_id"]
                    )
                ).json()
                self.assertEqual(result["status"], "completed", result)
                run = result["run_id"]
                report = await self.client.get(f"/financial-v2/reports/{run}/json")
                self.assertEqual(report.status_code, 200, report.text)
                self.assertTrue(verify_report(report.json(), run)["pass"])
                self.assertIn("no-store", report.headers["cache-control"])
                for channel in ("html", "pdf", "json"):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{run}/{channel}"
                            )
                        ).status_code,
                        200,
                    )
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )
                self.assertEqual(await self.counts(), (1, 1))
                self.settings = replace(self.settings, financial_product_enabled=False)
                self.assertEqual(
                    (await self.client.get("/financial-v2/sample")).status_code, 403
                )
                self.assertEqual(
                    (
                        await self.client.get(f"/financial-v2/reports/{run}/json")
                    ).status_code,
                    200,
                )
                self.client.headers["X-Token"] = issue_session_token(
                    self.value.user_id, uuid4(), "owner"
                )
                self.assertEqual(
                    (
                        await self.client.get(f"/financial-v2/reports/{run}/json")
                    ).status_code,
                    401,
                )

        asyncio.run(scenario())

    def test_unsupported_metadata_is_a_validation_response_not_a_server_error(self):
        async def scenario():
            await self.ready()
            preview_choices = UploadChoices(
                **self.choices.model_dump(exclude={"preview_hash", "attempt_key"})
            )
            async with self.client:
                for change in (
                    {"statement_type": "cash_flow"},
                    {"statement_type": "invented"},
                    {"comparison_basis": "invented"},
                ):
                    response = await self.post(
                        "preview",
                        choices=preview_choices,
                        content=csv_content(changes={"revenue": change}),
                    )
                    self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])

        asyncio.run(scenario())

    def test_free_report_history_survives_refresh_and_disabling_uploads_and_is_scoped(
        self,
    ):
        async def scenario():
            await self.ready()
            async with self.client:
                queued = await self.post()
                await self.work()
                done = (
                    await self.client.get(
                        "/financial-v2/jobs/" + queued.json()["job_id"]
                    )
                ).json()
                history = await self.client.get("/financial-v2/history")
                self.assertEqual(history.status_code, 200, history.text)
                self.assertEqual(
                    [r["run_id"] for r in history.json()["reports"]], [done["run_id"]]
                )
                self.settings = replace(self.settings, financial_product_enabled=False)
                self.assertEqual(
                    (await self.client.get("/financial-v2/history")).json(),
                    history.json(),
                )
                self.assertEqual(
                    (
                        await self.client.get(
                            f"/financial-v2/reports/{done['run_id']}/pdf"
                        )
                    ).status_code,
                    200,
                )
                other = uuid4()
                async with self.sessions.begin() as session:
                    session.add(Workspace(id=other, company_name="Other workspace"))
                    await session.flush()
                    session.add(
                        WorkspaceMember(
                            workspace_id=other, user_id=self.value.user_id, role="owner"
                        )
                    )
                self.client.headers["X-Token"] = issue_session_token(
                    self.value.user_id, other, "owner"
                )
                self.assertEqual(
                    (await self.client.get("/financial-v2/history")).json()["reports"],
                    [],
                )
                self.assertEqual(
                    (
                        await self.client.get(
                            "/financial-v2/history?before=" + done["history_id"]
                        )
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    (
                        await self.client.get(
                            "/financial-v2/jobs/" + queued.json()["job_id"]
                        )
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    (
                        await self.client.get(
                            f"/financial-v2/reports/{done['run_id']}/html"
                        )
                    ).status_code,
                    404,
                )
                self.assertEqual(await self.counts(), (1, 1))

        asyncio.run(scenario())

    def test_pending_queue_is_bounded_but_existing_attempt_can_be_resumed(self):
        async def scenario():
            await self.ready()
            async with self.client:
                first = await self.post()
                for _ in range(2):
                    self.assertEqual(
                        (
                            await self.post(
                                choices=self.choices.model_copy(
                                    update={"attempt_key": str(uuid4())}
                                )
                            )
                        ).status_code,
                        202,
                    )
                fourth = await self.post(
                    choices=self.choices.model_copy(
                        update={"attempt_key": str(uuid4())}
                    )
                )
                self.assertEqual(fourth.status_code, 429)
                self.assertEqual(
                    (await self.post()).json()["job_id"], first.json()["job_id"]
                )
                self.assertEqual(await self.counts(), (0, 0))

        asyncio.run(scenario())

    def test_database_lock_timeout_rolls_back_and_keeps_the_job_retryable(self):
        async def scenario():
            await self.ready()
            async with self.client:
                queued = await self.post()
                async with self.sessions.begin() as blocker:
                    await blocker.execute(
                        select(
                            func.pg_advisory_xact_lock(
                                func.hashtextextended(
                                    "financial-product:" + str(self.value.workspace_id),
                                    0,
                                )
                            )
                        )
                    )
                    with self.assertRaises(DBAPIError):
                        async with self.sessions.begin() as session:
                            await session.execute(
                                text("SET LOCAL lock_timeout = '50ms'")
                            )
                            await process_one(
                                session, self.settings, self.model, self.critic
                            )
                    pending = await self.client.get(
                        "/financial-v2/jobs/" + queued.json()["job_id"]
                    )
                    self.assertEqual(pending.json()["status"], "pending")
                    self.assertEqual(self.model.calls, [])
                    self.assertEqual(await self.counts(), (0, 0))
                await self.work()
                self.assertEqual(await self.counts(), (1, 1))

        asyncio.run(scenario())

    def test_rejected_attempts_count_towards_durable_workspace_rate_limit(self):
        def block(raw, context):
            raw["findings"][0]["checks"][0].update(
                outcome="block",
                message="Unsupported claim.",
                remediation="Review the supplied evidence.",
            )
            return raw

        async def scenario():
            await self.ready(RecordingCritic(block))
            with patch("saleslab_v2.product.jobs.MAX_ATTEMPTS_PER_HOUR", 2):
                async with self.client:
                    first = await self.post()
                    await self.work()
                    second = await self.post(
                        choices=self.choices.model_copy(
                            update={"attempt_key": str(uuid4())}
                        )
                    )
                    self.assertEqual(second.status_code, 202)
                    await self.work()
                    rejected = await self.post(
                        choices=self.choices.model_copy(
                            update={"attempt_key": str(uuid4())}
                        )
                    )
                    self.assertEqual(rejected.status_code, 429, rejected.text)
                    self.assertEqual(
                        (await self.post()).json()["job_id"], first.json()["job_id"]
                    )
                    self.assertEqual(len(self.model.calls), 2)
                    self.assertEqual(await self.counts(), (0, 0))

        asyncio.run(scenario())

    def test_http_upload_validation_queue_generation_artifacts_history_and_exact_quota(
        self,
    ):
        async def scenario():
            await self.ready()
            async with self.client:
                response = await self.post(
                    "preview",
                    UploadChoices(
                        **self.choices.model_dump(
                            exclude={"preview_hash", "attempt_key"}
                        )
                    ),
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["ready"])
                self.assertEqual(await self.counts(), (0, 0))
                queued = await self.post()
                self.assertEqual(queued.status_code, 202, queued.text)
                job = queued.json()["job_id"]
                pending = await self.client.get("/financial-v2/jobs/" + job)
                self.assertEqual(pending.json()["status"], "pending")
                self.assertTrue(await self.work())
                done = await self.client.get("/financial-v2/jobs/" + job)
                self.assertEqual(done.json()["status"], "completed", done.text)
                run = done.json()["run_id"]
                html = await self.client.get(f"/financial-v2/reports/{run}/html")
                pdf = await self.client.get(f"/financial-v2/reports/{run}/pdf")
                self.assertEqual(html.status_code, 200, html.text)
                self.assertEqual(pdf.status_code, 200)
                self.assertTrue(pdf.content.startswith(b"%PDF"))
                self.assertIn("no-store", pdf.headers["cache-control"])
                self.assertEqual(await self.counts(), (1, 1))
                repeated = await self.post()
                self.assertEqual(repeated.json()["job_id"], job)
                self.assertFalse(await self.work())
                self.assertEqual(
                    (len(self.model.calls), len(self.critic.calls)), (1, 1)
                )
                self.assertEqual(await self.counts(), (1, 1))

        asyncio.run(scenario())

    def test_concurrent_duplicate_submissions_and_workers_publish_once(self):
        async def scenario():
            await self.ready()
            async with self.client:
                left, right = await asyncio.gather(self.post(), self.post())
                self.assertEqual(left.status_code, 202, left.text)
                self.assertEqual(left.json()["job_id"], right.json()["job_id"])
                results = await asyncio.gather(self.work(), self.work())
                self.assertEqual(sorted(results), [False, True])
                self.assertEqual(await self.counts(), (1, 1))
                self.assertEqual(len(self.model.calls), 1)

        asyncio.run(scenario())

    def test_last_quota_slot_allows_one_of_two_distinct_jobs_to_call_models(self):
        async def scenario():
            await self.ready()
            async with self.sessions.begin() as session:
                session.add(
                    UsageCounter(
                        workspace_id=self.value.workspace_id, month="lifetime", count=9
                    )
                )
            async with self.client:
                self.assertEqual((await self.post()).status_code, 202)
                other = self.choices.model_copy(update={"attempt_key": str(uuid4())})
                self.assertEqual((await self.post(choices=other)).status_code, 202)
                await asyncio.gather(self.work(), self.work())
                self.assertEqual(await self.counts(), (1, 10))
                self.assertEqual(len(self.model.calls), 1)
                repeated = await self.post()
                self.assertEqual(repeated.status_code, 202)

        asyncio.run(scenario())

    def test_same_file_with_new_confirmed_source_language_keeps_both_reports_readable(
        self,
    ):
        async def scenario():
            await self.ready()
            async with self.client:
                first = await self.post()
                await self.work()
                original = (
                    await self.client.get(
                        "/financial-v2/jobs/" + first.json()["job_id"]
                    )
                ).json()
                choices = UploadChoices(
                    source_language="pt",
                    format_locale="en-GB",
                    report_language="pt",
                    mappings={
                        row["location"]: row["concept_id"]
                        for row in self.preview["rows"]
                    },
                )
                _, preview, generation, _, _ = upload_case(
                    self.value.workspace_id,
                    self.value.user_id,
                    choices=choices,
                    content=self.content,
                )
                self.assertTrue(preview["ready"])
                second = await self.post(choices=generation)
                self.assertEqual(second.status_code, 202, second.text)
                await self.work()
                changed = (
                    await self.client.get(
                        "/financial-v2/jobs/" + second.json()["job_id"]
                    )
                ).json()
                self.assertEqual(changed["status"], "completed", changed)
                self.assertNotEqual(original["run_id"], changed["run_id"])
                for result in (original, changed):
                    self.assertEqual(
                        (
                            await self.client.get(
                                f"/financial-v2/reports/{result['run_id']}/pdf"
                            )
                        ).status_code,
                        200,
                    )
                refreshed = (
                    await self.client.get(
                        "/financial-v2/jobs/" + first.json()["job_id"]
                    )
                ).json()
                self.assertEqual(refreshed["analyses_remaining"], 8)
                self.assertEqual(await self.counts(), (2, 2))

        asyncio.run(scenario())

    def test_changed_preview_or_attempt_inputs_are_rejected_without_provider_or_history(
        self,
    ):
        async def scenario():
            await self.ready()
            async with self.client:
                changed = await self.post(
                    content=csv_content(changes={"revenue": {"amount": "2000"}})
                )
                self.assertEqual(changed.status_code, 409)
                self.assertEqual((await self.post()).status_code, 202)
                changed = await self.post(
                    choices=self.choices.model_copy(update={"report_language": "en"})
                )
                self.assertEqual(changed.status_code, 409)
                self.assertEqual(await self.counts(), (0, 0))
                self.assertEqual(self.model.calls, [])

        asyncio.run(scenario())

    def test_critic_rejection_never_creates_paid_history_or_report(self):
        def block(raw, context):
            raw["findings"][0]["checks"][0].update(
                outcome="block",
                message="Unsupported claim.",
                remediation="Review the supplied evidence.",
            )
            return raw

        async def scenario():
            await self.ready(RecordingCritic(block))
            async with self.client:
                queued = await self.post()
                await self.work()
                result = await self.client.get(
                    "/financial-v2/jobs/" + queued.json()["job_id"]
                )
                self.assertNotEqual(result.json()["status"], "completed")
                self.assertEqual(await self.counts(), (0, 0))
                pdf = await self.client.get(
                    f"/financial-v2/reports/{result.json()['run_id']}/pdf"
                )
                self.assertEqual(pdf.status_code, 404)

        asyncio.run(scenario())

    def test_failure_after_history_and_quota_writes_rolls_back_both(self):
        async def scenario():
            await self.ready()
            original = V2Repository.persist_stage

            async def fail(repository, stage):
                if stage.stage_name == "financial_product":
                    raise RuntimeError("synthetic receipt persistence failure")
                return await original(repository, stage)

            async with self.client:
                queued = await self.post()
                with patch.object(V2Repository, "persist_stage", fail):
                    await self.work()
                result = await self.client.get(
                    "/financial-v2/jobs/" + queued.json()["job_id"]
                )
                self.assertEqual(result.json()["status"], "failed")
                self.assertEqual(await self.counts(), (0, 0))

        asyncio.run(scenario())

    def test_cancelled_worker_leaves_durable_job_pending_for_restart(self):
        async def scenario():
            await self.ready()
            async with self.client:
                queued = await self.post()
                original = self.model.generate

                async def cancel(context):
                    raise asyncio.CancelledError

                self.model.generate = cancel
                with self.assertRaises(asyncio.CancelledError):
                    await self.work()
                self.assertEqual(await self.counts(), (0, 0))
                result = await self.client.get(
                    "/financial-v2/jobs/" + queued.json()["job_id"]
                )
                self.assertEqual(result.json()["status"], "pending")
                self.model.generate = original
                await self.work()
                self.assertEqual(await self.counts(), (1, 1))

        asyncio.run(scenario())

    def test_current_scope_and_feature_flags_are_enforced_and_published_reads_survive_upload_disable(
        self,
    ):
        async def scenario():
            await self.ready()
            async with self.client:
                queued = await self.post()
                await self.work()
                done = (
                    await self.client.get(
                        "/financial-v2/jobs/" + queued.json()["job_id"]
                    )
                ).json()
                self.settings = replace(self.settings, financial_product_enabled=False)
                self.assertEqual((await self.post()).status_code, 403)
                self.assertEqual(
                    (
                        await self.client.get(
                            f"/financial-v2/reports/{done['run_id']}/pdf"
                        )
                    ).status_code,
                    200,
                )
                self.client.headers["X-Token"] = issue_session_token(
                    self.value.user_id, uuid4(), "owner"
                )
                self.assertEqual(
                    (
                        await self.client.get(
                            "/financial-v2/jobs/" + queued.json()["job_id"]
                        )
                    ).status_code,
                    401,
                )

        asyncio.run(scenario())

    def test_rehashed_report_artifact_tampering_is_rejected_before_download(self):
        async def scenario():
            await self.ready()
            async with self.client:
                queued = await self.post()
                await self.work()
                result = (
                    await self.client.get(
                        "/financial-v2/jobs/" + queued.json()["job_id"]
                    )
                ).json()
                async with self.sessions.begin() as session:
                    stage = await session.scalar(
                        select(StageResultModel).where(
                            StageResultModel.run_id == UUID(result["run_id"]),
                            StageResultModel.stage_name == "financial_report_render",
                        )
                    )
                    forged = deepcopy(stage.payload)
                    forged["html"] += "<p>fabricated value</p>"
                    stage.payload, stage.output_hash = forged, semantic_hash(forged)
                response = await self.client.get(
                    f"/financial-v2/reports/{result['run_id']}/html"
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    (
                        await self.client.get(
                            f"/financial-v2/reports/{result['run_id']}/json"
                        )
                    ).status_code,
                    404,
                )

        asyncio.run(scenario())

    def test_queue_configuration_change_fails_without_calls_or_quota(self):
        async def scenario():
            await self.ready()
            async with self.client:
                queued = await self.post()
                self.model.configuration = {
                    **self.model.configuration,
                    "model": "changed",
                }
                await self.work()
                result = (
                    await self.client.get(
                        "/financial-v2/jobs/" + queued.json()["job_id"]
                    )
                ).json()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(self.model.calls, [])
                self.assertEqual(await self.counts(), (0, 0))

        asyncio.run(scenario())
