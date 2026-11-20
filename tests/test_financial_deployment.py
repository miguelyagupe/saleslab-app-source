import asyncio
import io
import json
import os
import unittest
from copy import deepcopy
from unittest.mock import patch
from urllib.error import HTTPError, URLError

os.environ.setdefault(
    "DATABASE_URL", "postgresql://user:pass@localhost:5432/saleslab_test"
)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-with-at-least-32-characters")

from httpx import ASGITransport, AsyncClient

from main import app
from saleslab_v2.deployment import assess_deployment, health_payload
from scripts.financial_deployment_check import check_backend

SHA = "c64277b22bc58424740cf468df605cca4efcea51"


class FakeHTTP:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(
            value if isinstance(value, bytes) else json.dumps(value).encode()
        )


class DeploymentTests(unittest.TestCase):
    def test_actual_public_health_and_routes_pass_without_database_or_model_access(
        self,
    ):
        async def scenario():
            with patch.dict(os.environ, {"RAILWAY_GIT_COMMIT_SHA": SHA}):
                async with AsyncClient(
                    transport=ASGITransport(app), base_url="http://test"
                ) as client:
                    health = await client.get("/health")
                    schema = await client.get("/openapi.json")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.headers["cache-control"], "no-store")
                self.assertEqual(
                    health.json(),
                    {"status": "ok", "service": "SalesLab API v3", "revision": SHA},
                )
                receipt = assess_deployment(health.json(), schema.json(), SHA)
                self.assertEqual(receipt["status"], "pass")
                self.assertFalse(receipt["authenticated_pilot_checked"])
                self.assertFalse(receipt["rollout_authorized"])

        asyncio.run(scenario())

    def test_healthy_legacy_backend_wrong_commit_and_incomplete_routes_are_blocked(
        self,
    ):
        health = health_payload({"RAILWAY_GIT_COMMIT_SHA": SHA})
        schema = app.openapi()
        for changed_health, changed_schema in (
            (
                {"status": "ok", "service": "SalesLab API v3"},
                {"paths": {"/analyze-sales": {"post": {}}}},
            ),
            ({**health, "revision": "a" * 40}, schema),
            ({**health, "status": "unhealthy"}, schema),
            (health, {"paths": None}),
        ):
            self.assertEqual(
                assess_deployment(changed_health, changed_schema, SHA)["status"],
                "blocked",
            )
        incomplete = deepcopy(schema)
        incomplete["paths"]["/financial-v2/generate"] = {"get": {}}
        result = assess_deployment(health, incomplete, SHA)
        self.assertEqual(result["missing_routes"], ["POST /financial-v2/generate"])
        self.assertEqual(result["status"], "blocked")
        for value in (None, "short-sha", "secret-token\n", "a" * 41):
            self.assertIsNone(
                health_payload({"RAILWAY_GIT_COMMIT_SHA": value})["revision"]
            )

    def test_public_check_only_gets_health_and_schema_with_bounded_requests(self):
        http = FakeHTTP(
            [health_payload({"RAILWAY_GIT_COMMIT_SHA": SHA}), app.openapi()]
        )
        result = check_backend("https://api.example.test/", SHA, opener=http)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            [r.full_url for r, _ in http.requests],
            [
                "https://api.example.test/health",
                "https://api.example.test/openapi.json",
            ],
        )
        self.assertTrue(
            all(
                r.get_method() == "GET" and r.data is None and timeout == 15
                for r, timeout in http.requests
            )
        )
        self.assertTrue(
            all(
                r.get_header("X-token") is None
                and r.get_header("Authorization") is None
                for r, _ in http.requests
            )
        )
        for url in (
            "http://api.example.test",
            "https://user:password@api.example.test",
            "https://api.example.test/?token=secret",
            "https://api.example.test/#secret",
        ):
            with self.assertRaises(ValueError):
                check_backend(url, SHA, opener=FakeHTTP([]))
        with self.assertRaises(ValueError):
            check_backend("https://api.example.test", "short-sha", opener=FakeHTTP([]))

    def test_transport_failures_and_bad_responses_cannot_pass(self):
        for response in (
            HTTPError("https://api.example.test", 302, "secret", {}, None),
            HTTPError("https://api.example.test", 404, "secret", {}, None),
            URLError("secret provider request"),
            b"<html>secret</html>",
            b"x" * 2_000_001,
            [],
        ):
            with self.assertRaises((ValueError, TypeError)) as caught:
                check_backend(
                    "https://api.example.test", SHA, opener=FakeHTTP([response, {}])
                )
            self.assertNotIn("secret", str(caught.exception))
