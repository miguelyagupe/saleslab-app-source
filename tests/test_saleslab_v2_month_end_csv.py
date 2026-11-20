"""CSV projection, spreadsheet injection and authenticated route regressions."""

import asyncio
import csv
import io
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://unused/export_test")
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from month_end_upload_fixtures import month_end_case

from database import get_db
from saleslab_v2.product.api import get_settings, router
from saleslab_v2.product.month_end import prepare_month_end
from saleslab_v2.product.month_end_csv import HEADERS, VERSION, checklist_csv, text_cell
from saleslab_v2.product.month_end_saved import InvalidMonthlySnapshot, saved_payload


def example(language="pt"):
    parsed, choices, close, _, _, user = month_end_case()
    if language != "pt":
        choices = choices.model_copy(update={"report_language": language})
        # Changing report language invalidates the reviewed preview binding.
        from saleslab_v2.product.planning import prepare_upload

        _, preview = prepare_upload(parsed, choices, user, "preview")
        close = close.model_copy(update={"preview_hash": preview["preview_hash"]})
    prepared = prepare_month_end(parsed, choices, close, user)
    return saved_payload(prepared, uuid4(), datetime(2026, 11, 1, tzinfo=UTC))


@pytest.mark.parametrize("language", ["pt", "en", "es", "fr"])
def test_csv_preserves_every_checklist_item_and_binding_without_mutation(language):
    value = example(language)
    before = deepcopy(value)
    content = checklist_csv(value)
    assert content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")), delimiter=";"))
    assert tuple(rows[0]) == HEADERS
    assert len(rows) == len(value["result"]["checklist"])
    for row, item in zip(rows, value["result"]["checklist"], strict=True):
        assert row["export_schema"] == VERSION
        for key in ("snapshot_id", "source_hash"):
            assert row[key] == value[key]
        assert row["snapshot_payload_hash"] == value["payload_hash"]
        assert row["accounting_close_attested"] == "false"
        assert row["language"] == language
        for key in ("item_id", "category", "title", "status", "reason_code"):
            assert row[key] == item[key]
        assert json.loads(row["evidence_json"]) == item["evidence"]
    assert checklist_csv(value) == content
    assert value == before


@pytest.mark.parametrize(
    "prefix",
    ["=", "+", "-", "@", " =", "\t=", "\r=", "\n=", "\ufeff=", "\u200b=", "\u2060+"],
)
def test_formula_prefixes_are_neutralized(prefix):
    assert text_cell(prefix + 'HYPERLINK("https://example.test")').startswith("'")


def test_unicode_delimiters_quotes_newlines_and_formula_titles_round_trip():
    value = example()
    value["filename"] = '=unsafe;"\r\nficheiro é €'
    value["result"]["checklist"][0]["title"] = "\t@SUM(1;2)"
    rows = list(
        csv.DictReader(
            io.StringIO(checklist_csv(value).decode("utf-8-sig")), delimiter=";"
        )
    )
    assert rows[0]["filename"] == "'" + value["filename"]
    assert rows[0]["title"] == "'\t@SUM(1;2)"
    assert all(None not in row for row in rows)
    assert text_cell("ação €") == "ação €"


@pytest.mark.parametrize(
    "failure,status",
    [
        (None, 200),
        (PermissionError("foreign"), 404),
        (InvalidMonthlySnapshot("tampered"), 409),
    ],
)
def test_csv_route_requires_verified_workspace_snapshot(failure, status):
    async def scenario():
        app = FastAPI()
        app.include_router(router)

        async def db():
            yield object()

        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_settings] = lambda: object()
        workspace, user, snapshot_id = uuid4(), uuid4(), uuid4()
        actor = {
            "workspace": SimpleNamespace(id=workspace),
            "user": SimpleNamespace(id=user),
        }
        snapshot = example()
        read = AsyncMock(return_value=snapshot, side_effect=failure)
        with (
            patch("saleslab_v2.product.api.actor", AsyncMock(return_value=actor)),
            patch("saleslab_v2.product.api.MonthlySnapshots.read", read),
        ):
            async with AsyncClient(
                transport=ASGITransport(app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/financial-v2/month-end/snapshots/{snapshot_id}/checklist.csv"
                )
        assert response.status_code == status
        read.assert_awaited_once_with(workspace, user, snapshot_id)
        if status == 200:
            assert response.content == checklist_csv(snapshot)
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert f"{snapshot_id}.csv" in response.headers["content-disposition"]
        else:
            assert "source_hash" not in response.text

    asyncio.run(scenario())
