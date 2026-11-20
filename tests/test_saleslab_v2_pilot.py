import json
import subprocess
import sys
import unittest
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import patch
from urllib.error import URLError
from uuid import uuid4

from saleslab_v2.domain.idempotency import canonicalize
from saleslab_v2.pipeline.evaluator import evaluate
from saleslab_v2.product.contracts import UploadChoices
from saleslab_v2.product.ingestion import parse_upload
from saleslab_v2.product.pilot import (
    SOURCE_SHA256,
    check_metrics,
    offline_acceptance,
    sample,
)
from saleslab_v2.product.pilot_client import (
    NoRedirect,
    PilotFailure,
    PilotHTTP,
    run_pilot,
    verify_report,
)
from saleslab_v2.product.planning import prepare_upload


def case_report():
    case = sample()
    parsed = parse_upload(
        uuid4(),
        case["filename"],
        case["content"].encode(),
        UploadChoices(**case["choices"]),
    )
    request, preview = prepare_upload(
        parsed, UploadChoices(**case["choices"]), uuid4(), "fixture"
    )
    evaluation = evaluate(request, uuid4(), datetime.now(UTC))
    run_id = str(uuid4())
    report = {
        "run_id": run_id,
        "module": "financial",
        "audit_result": {"status": "approved"},
        "metric_observations": [
            {"observation": canonicalize(asdict(o))} for _, o, _ in evaluation.metrics
        ],
        "capability_decisions": [
            {
                "capability": d.capability.value,
                "decision_status": d.decision_status.value,
            }
            for d in evaluation.decisions
        ],
        "source_index": [{"content_hash": SOURCE_SHA256}],
        "forecasts": [],
        "recommendations": [],
    }
    return case, preview, run_id, report


class PilotTests(unittest.TestCase):
    def test_public_figures_scale_basis_and_capability_limits(self):
        result = offline_acceptance()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["provider_calls"], 0)
        self.assertFalse(result["rollout_authorized"])
        self.assertEqual(result["semantic_quality"], "not_certified")
        self.assertEqual(result["capabilities"]["forecast"], "blocked")
        data = sample()
        self.assertIn("Profit before non-controlling interests;606", data["content"])
        self.assertEqual(data["source_facts"][1]["published_millions_eur"], "606")
        self.assertIn("Cash flow from operating activities;1,654", data["content"])

    def test_checker_rejects_wrong_basis_currency_duplicates_nonfinite_and_extreme_numbers(
        self,
    ):
        _, _, run_id, report = case_report()
        self.assertTrue(verify_report(report, run_id)["pass"])
        observations = [
            {**r["observation"], "value": r["observation"]["value"]["$decimal"]}
            for r in report["metric_observations"]
        ]
        for value in ("599", "NaN", "1e999999999", "1e-999999999", 33464000000):
            changed = deepcopy(observations)
            changed[0]["value"] = value
            with self.subTest(value=value):
                self.assertFalse(check_metrics(changed)["pass"])
        changed = deepcopy(observations)
        changed[0]["currency"] = "USD"
        self.assertFalse(check_metrics(changed)["pass"])
        self.assertFalse(check_metrics(observations + observations[:1])["pass"])
        for mutate in (
            lambda r: r["source_index"][0].update(content_hash="0" * 64),
            lambda r: r["audit_result"].update(status="blocked"),
            lambda r: r.update(run_id=str(uuid4())),
            lambda r: r.update(forecasts=[{"invented": True}]),
        ):
            changed = deepcopy(report)
            mutate(changed)
            self.assertFalse(verify_report(changed, run_id)["pass"])

    def test_sample_hash_and_metadata_are_not_mutable_between_reads(self):
        data = sample()
        data["sources"][0]["url"] = "https://invalid.example"
        self.assertNotEqual(data["sources"], sample()["sources"])
        with patch(
            "pathlib.Path.read_text", return_value=data["content"] + "changed"
        ), self.assertRaises(ValueError):
            sample()

    def test_live_transport_refuses_insecure_urls_credentials_missing_token_and_redirects(
        self,
    ):
        for url in (
            "http://api.example",
            "https://user:pass@api.example",
            "https://api.example?secret=x",
            "https://api.example/#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(PilotFailure):
                PilotHTTP(url, "session")
        with self.assertRaises(PilotFailure):
            PilotHTTP("https://api.example", "")
        self.assertIsNone(
            NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://other.example"
            )
        )
        transport = PilotHTTP("https://api.example", "private-session-token")
        with patch.object(
            transport.opener, "open", side_effect=URLError("private-session-token")
        ):
            with self.assertRaises(PilotFailure) as error:
                transport.call("/financial-v2/sample")
            self.assertNotIn("private-session-token", str(error.exception))

    def test_preview_is_read_only_generation_is_explicit_resume_is_read_only_and_pending_is_bounded(
        self,
    ):
        case, preview, run_id, report = case_report()
        job_id = str(uuid4())
        calls = []
        pending = False

        class Transport:
            def call(self, path, data=None, content_type=None):
                calls.append((path, data, content_type))
                if path.endswith("/capabilities"):
                    result = {"enabled": True}
                elif path.endswith("/sample"):
                    result = case
                elif path.endswith("/preview"):
                    result = preview
                elif path.endswith("/generate"):
                    result = {"job_id": job_id}
                elif "/jobs/" in path:
                    result = (
                        {"status": "pending"}
                        if pending
                        else {"status": "completed", "run_id": run_id}
                    )
                elif path.endswith("/json"):
                    result = report
                elif path.endswith("/html"):
                    return b"<!doctype html>verified report"
                elif path.endswith("/pdf"):
                    return b"%PDF-1.7 verified"
                else:
                    raise AssertionError(path)
                return json.dumps(result).encode()

        transport = Transport()
        self.assertEqual(run_pilot(transport)["status"], "preview_pass")
        self.assertFalse(any(p.endswith("/generate") for p, _, _ in calls))
        with self.assertRaises(PilotFailure):
            run_pilot(transport, generate=True)
        self.assertEqual(
            run_pilot(transport, generate=True, attempt_key=str(uuid4()))["status"],
            "pass",
        )
        self.assertEqual(sum(p.endswith("/generate") for p, _, _ in calls), 1)
        calls.clear()
        self.assertEqual(run_pilot(transport, job_id=job_id)["status"], "pass")
        self.assertTrue(all(body is None for _, body, _ in calls))
        calls.clear()
        case["sha256"] = "0" * 64
        with self.assertRaises(PilotFailure):
            run_pilot(transport, generate=True, attempt_key=str(uuid4()))
        self.assertFalse(any(p.endswith("/generate") for p, _, _ in calls))
        pending = True
        tick = [0]

        def wait(seconds):
            tick[0] += seconds

        result = run_pilot(
            transport, job_id=job_id, timeout=3, clock=lambda: tick[0], sleep=wait
        )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["job_id"], job_id)
        self.assertEqual(tick[0], 3)

    def test_default_command_is_offline_and_reports_no_rollout_authorization(self):
        result = subprocess.run(
            [sys.executable, "scripts/financial_pilot_acceptance.py"],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["provider_calls"], 0)
        self.assertFalse(payload["rollout_authorized"])
