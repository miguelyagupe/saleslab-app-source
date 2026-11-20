import os
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/saleslab_test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret-with-at-least-32-characters")

import jwt as pyjwt
from fastapi import HTTPException

import auth


class FakeResult:
    def __init__(self, row=None, scalar=None):
        self._row = row
        self._scalar = scalar

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._scalar


class FakeMagicDb:
    def __init__(self, rows):
        self.rows = list(rows)

    async def execute(self, _stmt):
        row = self.rows.pop(0) if self.rows else None
        return FakeResult(row=row)


class FakeSessionDb:
    def __init__(self, user, workspace, membership):
        self.user = user
        self.workspace = workspace
        self.membership = membership

    async def get(self, model, obj_id):
        if model.__name__ == "Workspace" and obj_id == self.workspace.id:
            return self.workspace
        if model.__name__ == "User" and obj_id == self.user.id:
            return self.user
        return None

    async def execute(self, _stmt):
        return FakeResult(scalar=self.membership)


class FakeConsumedMagicSessionDb(FakeSessionDb):
    def __init__(self, user, workspace, membership, magic_row):
        super().__init__(user, workspace, membership)
        self.magic_row = magic_row
        self.execute_count = 0

    async def execute(self, _stmt):
        self.execute_count += 1
        if self.execute_count == 1:
            return FakeResult(row=self.magic_row)
        return FakeResult(scalar=self.membership)


class AuthSecurityTests(unittest.IsolatedAsyncioTestCase):
    def test_decode_rejects_invalid_jwt(self):
        with self.assertRaises(HTTPException) as ctx:
            auth.decode_session_token("not-a-jwt")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_decode_rejects_expired_jwt(self):
        token = pyjwt.encode(
            {
                "user_id": str(uuid.uuid4()),
                "workspace_id": str(uuid.uuid4()),
                "role": "owner",
                "iat": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()),
                "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
            },
            auth.JWT_SECRET,
            algorithm="HS256",
        )

        with self.assertRaises(HTTPException) as ctx:
            auth.decode_session_token(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_decode_requires_expected_claims(self):
        token = pyjwt.encode(
            {
                "user_id": str(uuid.uuid4()),
                "role": "owner",
                "iat": int(datetime.now(timezone.utc).timestamp()),
                "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
            },
            auth.JWT_SECRET,
            algorithm="HS256",
        )

        with self.assertRaises(HTTPException) as ctx:
            auth.decode_session_token(token)
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_magic_link_reuse_is_rejected_after_first_atomic_consume(self):
        user_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        first_row = SimpleNamespace(user_id=user_id, workspace_id=workspace_id, role="owner")
        db = FakeMagicDb([first_row, None])

        payload = await auth.consume_magic_link_token(db, "raw-token")
        self.assertEqual(payload["user_id"], user_id)

        with self.assertRaises(HTTPException) as ctx:
            await auth.consume_magic_link_token(db, "raw-token")
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_expired_magic_link_is_rejected(self):
        db = FakeMagicDb([None])
        with self.assertRaises(HTTPException) as ctx:
            await auth.consume_magic_link_token(db, "expired-token")
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_concurrent_magic_link_loser_is_rejected(self):
        db = FakeMagicDb([None])
        with self.assertRaises(HTTPException) as ctx:
            await auth.consume_magic_link_token(db, "already-claimed-token")
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_get_current_session_rejects_cross_workspace_without_membership(self):
        user = SimpleNamespace(id=uuid.uuid4())
        workspace = SimpleNamespace(id=uuid.uuid4())
        token = auth.issue_session_token(user.id, workspace.id, "owner")
        db = FakeSessionDb(user, workspace, membership=None)

        with self.assertRaises(HTTPException) as ctx:
            await auth.get_current_session(db, token)
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_backend_new_accepts_consumed_magic_link_for_old_frontend_during_ttl(self):
        user = SimpleNamespace(id=uuid.uuid4())
        workspace = SimpleNamespace(id=uuid.uuid4())
        membership = SimpleNamespace(role="owner")
        magic_row = SimpleNamespace(user_id=user.id, workspace_id=workspace.id, role="owner")
        db = FakeConsumedMagicSessionDb(user, workspace, membership, magic_row)

        session = await auth.get_current_session(db, "opaque-consumed-magic-token")

        self.assertEqual(session["user"], user)
        self.assertEqual(session["workspace"], workspace)
        self.assertEqual(session["role"], "owner")


if __name__ == "__main__":
    unittest.main()
