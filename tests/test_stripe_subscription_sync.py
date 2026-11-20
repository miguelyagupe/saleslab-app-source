import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/saleslab_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-with-at-least-32-characters")

import main


class WorkspaceStub:
    def __init__(self, plan="free"):
        self.plan = plan
        self.stripe_subscription_id = None
        self.stripe_customer_id = None
        self.stripe_subscription_status = None
        self.stripe_current_period_end = None
        self.stripe_cancel_at_period_end = False
        self.stripe_cancel_at = None
        self.plan_renewed_at = None


def subscription(status="active", price_id="price_1Tw2gzF6K4YHMTtacoJh9S8f", **overrides):
    data = {
        "id": "sub_123",
        "customer": "cus_123",
        "status": status,
        "current_period_end": 1_800_000_000,
        "cancel_at_period_end": False,
        "cancel_at": None,
        "items": {"data": [{"price": {"id": price_id}}]},
    }
    data.update(overrides)
    return data


class StripeSubscriptionSyncTests(unittest.TestCase):
    def test_active_subscription_sets_plan_and_subscription_fields(self):
        workspace = WorkspaceStub()
        main.apply_subscription_state_to_workspace(workspace, subscription(status="active"))

        self.assertEqual(workspace.plan, "pro")
        self.assertEqual(workspace.stripe_subscription_id, "sub_123")
        self.assertEqual(workspace.stripe_customer_id, "cus_123")
        self.assertEqual(workspace.stripe_subscription_status, "active")
        self.assertEqual(workspace.stripe_current_period_end, datetime.fromtimestamp(1_800_000_000, timezone.utc))
        self.assertFalse(workspace.stripe_cancel_at_period_end)
        self.assertIsNotNone(workspace.plan_renewed_at)

    def test_canceled_subscription_returns_workspace_to_free(self):
        workspace = WorkspaceStub(plan="team")
        main.apply_subscription_state_to_workspace(workspace, subscription(status="canceled"))

        self.assertEqual(workspace.plan, "free")
        self.assertEqual(workspace.stripe_subscription_status, "canceled")

    def test_payment_failed_past_due_keeps_access_during_recovery(self):
        workspace = WorkspaceStub(plan="pro")
        main.apply_failed_invoice_state_to_workspace(workspace, subscription(status="past_due"))

        self.assertEqual(workspace.plan, "pro")
        self.assertEqual(workspace.stripe_subscription_status, "past_due")

    def test_subscription_updated_cancel_at_period_end_keeps_access_until_terminal_status(self):
        workspace = WorkspaceStub(plan="pro")
        main.apply_subscription_state_to_workspace(
            workspace,
            subscription(status="active", cancel_at_period_end=True, cancel_at=1_800_000_000),
        )

        self.assertEqual(workspace.plan, "pro")
        self.assertTrue(workspace.stripe_cancel_at_period_end)
        self.assertEqual(workspace.stripe_cancel_at, datetime.fromtimestamp(1_800_000_000, timezone.utc))

    def test_object_style_stripe_payloads_are_supported(self):
        workspace = WorkspaceStub()
        stripe_object = SimpleNamespace(
            id="sub_obj",
            customer="cus_obj",
            status="active",
            current_period_end=1_800_000_000,
            cancel_at_period_end=False,
            cancel_at=None,
            items=SimpleNamespace(data=[SimpleNamespace(price=SimpleNamespace(id="price_1Tw2k5F6K4YHMTtaEkdDnpnX"))]),
        )

        main.apply_subscription_state_to_workspace(workspace, stripe_object)

        self.assertEqual(workspace.plan, "team")
        self.assertEqual(workspace.stripe_subscription_id, "sub_obj")
        self.assertEqual(workspace.stripe_customer_id, "cus_obj")


class StripeOutOfOrderEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_retrieve = main.retrieve_stripe_subscription
        self.original_find_workspace = main.find_workspace_for_stripe_subscription
        self.workspace = WorkspaceStub(plan="pro")

        async def find_workspace(_db, _subscription_id=None, _customer_id=None, _email=None):
            return self.workspace

        main.find_workspace_for_stripe_subscription = find_workspace

    async def asyncTearDown(self):
        main.retrieve_stripe_subscription = self.original_retrieve
        main.find_workspace_for_stripe_subscription = self.original_find_workspace

    async def test_out_of_order_updated_event_uses_current_subscription_from_stripe(self):
        old_active_snapshot = subscription(status="active")
        current_canceled_subscription = subscription(status="canceled")

        async def retrieve_current(_subscription_id):
            return current_canceled_subscription

        main.retrieve_stripe_subscription = retrieve_current

        await main.process_subscription_event(None, old_active_snapshot)

        self.assertEqual(self.workspace.plan, "free")
        self.assertEqual(self.workspace.stripe_subscription_status, "canceled")

    async def test_out_of_order_deleted_event_does_not_override_current_active_subscription(self):
        old_deleted_snapshot = subscription(status="canceled")
        current_active_subscription = subscription(status="active")

        async def retrieve_current(_subscription_id):
            return current_active_subscription

        main.retrieve_stripe_subscription = retrieve_current

        await main.process_subscription_event(None, old_deleted_snapshot, allow_deleted_snapshot_fallback=True)

        self.assertEqual(self.workspace.plan, "pro")
        self.assertEqual(self.workspace.stripe_subscription_status, "active")

    async def test_deleted_event_falls_back_to_snapshot_when_subscription_no_longer_exists(self):
        deleted_snapshot = subscription(status="canceled")

        async def retrieve_missing(_subscription_id):
            return None

        main.retrieve_stripe_subscription = retrieve_missing

        await main.process_subscription_event(None, deleted_snapshot, allow_deleted_snapshot_fallback=True)

        self.assertEqual(self.workspace.plan, "free")
        self.assertEqual(self.workspace.stripe_subscription_status, "canceled")


if __name__ == "__main__":
    unittest.main()
