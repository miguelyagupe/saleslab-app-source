"""Explicit synthetic financial evaluation cases, no customer data or paid calls."""

import os
from copy import deepcopy
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql://unused/shadow_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-with-at-least-32-characters")

from pipeline_fixtures import request, with_growth

import main
from saleslab_v2.pipeline.contracts import InputRef
from saleslab_v2.shadow.contracts import MetricExpectation, ShadowCase
from saleslab_v2.shadow.legacy import DeterministicLegacyRunner


class RecordingLegacy(DeterministicLegacyRunner):
    def __init__(self, transform=None):
        super().__init__(main.build_deterministic_analysis)
        self.calls, self.transform = [], transform

    async def generate(self, shared_input):
        self.calls.append(deepcopy(shared_input))
        raw = await super().generate(shared_input)
        return self.transform(raw) if self.transform else raw


def shadow_case(value=None, *, case_id="financial-baseline", growth=False):
    value = value or request()
    if growth:
        value = with_growth(value)
    sid = value.sources[0].document.source_id
    bindings = (
        ("revenue_current", InputRef("revenue", sid)),
        ("net_result", InputRef("net_income", sid)),
    )
    metrics = (
        MetricExpectation(
            "margin", Decimal(10), "percentage", None, "net_margin_pct", Decimal("0.05")
        ),
    )
    if growth:
        bindings += (("revenue_previous", InputRef("revenue_prior", sid)),)
        metrics += (
            MetricExpectation("growth", Decimal(25), "percentage", None),
            MetricExpectation(
                "current_revenue",
                Decimal(1000),
                "monetary",
                "EUR",
                "revenue",
                Decimal("0.5"),
            ),
        )
    return ShadowCase(
        case_id,
        "synthetic-financial-v1",
        value,
        bindings,
        "completed",
        "completed",
        metrics,
        (
            ("calculate", "allowed"),
            ("forecast", "blocked"),
            ("recommend_material", "blocked"),
        ),
        expected_audit_status="approved",
    )
