#!/usr/bin/env python3
"""Produce a strictly read-only PostgreSQL schema inspection report.

Besides comparing SQLAlchemy metadata with the live schema, the inspector
validates every durable schema object created or altered by the three legacy
manual migrations. It never stamps a revision or executes DDL/DML.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import Base  # noqa: E402
import models  # noqa: E402,F401  # Register expected legacy tables.

MANUAL_MIGRATIONS = {
    "20260724_fix_usage_counters_quota.sql": {
        "checks": [
            {
                "id": "usage_counters.month",
                "kind": "column",
                "table": "usage_counters",
                "column": "month",
                "type": "character varying(16)",
                "preexisting": True,
            },
            {
                "id": "uq_usage_counters_workspace_month",
                "kind": "index",
                "name": "uq_usage_counters_workspace_month",
                "table": "usage_counters",
                "columns": ["workspace_id", "month"],
                "unique": True,
                "method": "btree",
                "predicate": None,
            },
        ],
        "non_schema_effects": [
            "Duplicate (workspace_id, month) rows are consolidated before the unique index; "
            "historical execution of this data rewrite cannot be proven from catalog state."
        ],
    },
    "20260724_secure_magic_links.sql": {
        "checks": [
            {"id": "magic_link_tokens", "kind": "table", "table": "magic_link_tokens"},
            {
                "id": "magic_link_tokens.id",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "id",
                "type": "uuid",
                "nullable": False,
            },
            {
                "id": "magic_link_tokens.token_hash",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "token_hash",
                "type": "character varying(64)",
                "nullable": False,
            },
            {
                "id": "magic_link_tokens.user_id",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "user_id",
                "type": "uuid",
                "nullable": False,
            },
            {
                "id": "magic_link_tokens.workspace_id",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "workspace_id",
                "type": "uuid",
                "nullable": False,
            },
            {
                "id": "magic_link_tokens.role",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "role",
                "type": "character varying(20)",
                "nullable": False,
            },
            {
                "id": "magic_link_tokens.expires_at",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "expires_at",
                "type": "timestamp with time zone",
                "nullable": False,
            },
            {
                "id": "magic_link_tokens.used_at",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "used_at",
                "type": "timestamp with time zone",
                "nullable": True,
            },
            {
                "id": "magic_link_tokens.created_at",
                "kind": "column",
                "table": "magic_link_tokens",
                "column": "created_at",
                "type": "timestamp with time zone",
                "nullable": False,
                "default": "now()",
            },
            {
                "id": "magic_link_tokens.pk",
                "kind": "constraint",
                "table": "magic_link_tokens",
                "constraint_type": "primary_key",
                "columns": ["id"],
            },
            {
                "id": "magic_link_tokens.token_hash.unique",
                "kind": "constraint",
                "table": "magic_link_tokens",
                "constraint_type": "unique",
                "columns": ["token_hash"],
            },
            {
                "id": "magic_link_tokens.user_id.fk",
                "kind": "constraint",
                "table": "magic_link_tokens",
                "constraint_type": "foreign_key",
                "columns": ["user_id"],
                "referred_table": "users",
                "referred_columns": ["id"],
                "on_delete": "CASCADE",
            },
            {
                "id": "magic_link_tokens.workspace_id.fk",
                "kind": "constraint",
                "table": "magic_link_tokens",
                "constraint_type": "foreign_key",
                "columns": ["workspace_id"],
                "referred_table": "workspaces",
                "referred_columns": ["id"],
                "on_delete": "CASCADE",
            },
            {
                "id": "ix_magic_link_tokens_expires_at",
                "kind": "index",
                "name": "ix_magic_link_tokens_expires_at",
                "table": "magic_link_tokens",
                "columns": ["expires_at"],
                "unique": False,
                "method": "btree",
                "predicate": None,
            },
        ],
    },
    "20260724_sync_stripe_subscriptions.sql": {
        "checks": [
            {
                "id": "workspaces.stripe_subscription_status",
                "kind": "column",
                "table": "workspaces",
                "column": "stripe_subscription_status",
                "type": "character varying(50)",
                "nullable": True,
            },
            {
                "id": "workspaces.stripe_current_period_end",
                "kind": "column",
                "table": "workspaces",
                "column": "stripe_current_period_end",
                "type": "timestamp with time zone",
                "nullable": True,
            },
            {
                "id": "workspaces.stripe_cancel_at_period_end",
                "kind": "column",
                "table": "workspaces",
                "column": "stripe_cancel_at_period_end",
                "type": "boolean",
                "nullable": False,
                "default": "false",
            },
            {
                "id": "workspaces.stripe_cancel_at",
                "kind": "column",
                "table": "workspaces",
                "column": "stripe_cancel_at",
                "type": "timestamp with time zone",
                "nullable": True,
            },
            {
                "id": "stripe_webhook_events",
                "kind": "table",
                "table": "stripe_webhook_events",
            },
            {
                "id": "stripe_webhook_events.id",
                "kind": "column",
                "table": "stripe_webhook_events",
                "column": "id",
                "type": "character varying(255)",
                "nullable": False,
            },
            {
                "id": "stripe_webhook_events.event_type",
                "kind": "column",
                "table": "stripe_webhook_events",
                "column": "event_type",
                "type": "character varying(100)",
                "nullable": False,
            },
            {
                "id": "stripe_webhook_events.processed_at",
                "kind": "column",
                "table": "stripe_webhook_events",
                "column": "processed_at",
                "type": "timestamp with time zone",
                "nullable": False,
                "default": "now()",
            },
            {
                "id": "stripe_webhook_events.pk",
                "kind": "constraint",
                "table": "stripe_webhook_events",
                "constraint_type": "primary_key",
                "columns": ["id"],
            },
            {
                "id": "ix_workspaces_stripe_customer_id",
                "kind": "index",
                "name": "ix_workspaces_stripe_customer_id",
                "table": "workspaces",
                "columns": ["stripe_customer_id"],
                "unique": False,
                "method": "btree",
                "predicate": "stripe_customer_id IS NOT NULL",
            },
            {
                "id": "ix_workspaces_stripe_subscription_id",
                "kind": "index",
                "name": "ix_workspaces_stripe_subscription_id",
                "table": "workspaces",
                "columns": ["stripe_subscription_id"],
                "unique": False,
                "method": "btree",
                "predicate": "stripe_subscription_id IS NOT NULL",
            },
        ],
    },
}


CATALOG_COLUMNS_SQL = text("""
SELECT c.relname AS table_name, a.attname AS column_name,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS default_expression
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
""")

CATALOG_CONSTRAINTS_SQL = text("""
SELECT con.conname, tbl.relname AS table_name, con.contype,
       ARRAY(SELECT att.attname FROM unnest(con.conkey) WITH ORDINALITY keys(attnum, ord)
             JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = keys.attnum
             ORDER BY keys.ord) AS columns,
       ref.relname AS referred_table,
       ARRAY(SELECT att.attname FROM unnest(con.confkey) WITH ORDINALITY keys(attnum, ord)
             JOIN pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = keys.attnum
             ORDER BY keys.ord) AS referred_columns,
       con.confdeltype, pg_get_constraintdef(con.oid, true) AS definition,
       con.convalidated
FROM pg_constraint con
JOIN pg_class tbl ON tbl.oid = con.conrelid
JOIN pg_namespace n ON n.oid = tbl.relnamespace
LEFT JOIN pg_class ref ON ref.oid = con.confrelid
WHERE n.nspname = 'public'
ORDER BY tbl.relname, con.conname
""")

CATALOG_INDEXES_SQL = text("""
SELECT idx.relname AS index_name, tbl.relname AS table_name, am.amname AS method,
       ind.indisunique, ind.indisvalid, ind.indisready,
       ARRAY(SELECT pg_get_indexdef(ind.indexrelid, key_no, true)
             FROM generate_series(1, ind.indnkeyatts) key_no ORDER BY key_no) AS columns,
       pg_get_expr(ind.indpred, ind.indrelid, true) AS predicate,
       pg_get_indexdef(ind.indexrelid) AS definition
FROM pg_index ind
JOIN pg_class idx ON idx.oid = ind.indexrelid
JOIN pg_class tbl ON tbl.oid = ind.indrelid
JOIN pg_namespace n ON n.oid = tbl.relnamespace
JOIN pg_am am ON am.oid = idx.relam
WHERE n.nspname = 'public'
ORDER BY tbl.relname, idx.relname
""")


def async_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime, Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("ascii")
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def normalize_expression(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = re.sub(r"::(?:character varying|text|boolean)\b", "", normalized)
    return normalized


def catalog_snapshot(connection: Connection) -> dict[str, Any]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    columns = {
        (row.table_name, row.column_name): {
            "type": row.formatted_type,
            "nullable": row.nullable,
            "default": row.default_expression,
        }
        for row in connection.execute(CATALOG_COLUMNS_SQL)
    }
    constraints = [
        dict(row._mapping) for row in connection.execute(CATALOG_CONSTRAINTS_SQL)
    ]
    indexes = {
        row.index_name: dict(row._mapping)
        for row in connection.execute(CATALOG_INDEXES_SQL)
    }
    return {
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
    }


def missing(check: dict[str, Any], code: str, observed: Any = None) -> dict[str, Any]:
    return {
        "object_id": check["id"],
        "object_type": check["kind"],
        "result": "missing",
        "reason_code": code,
        "expected": expected_details(check),
        "observed": observed,
    }


def divergent(check: dict[str, Any], code: str, observed: Any) -> dict[str, Any]:
    return {
        "object_id": check["id"],
        "object_type": check["kind"],
        "result": "divergent",
        "reason_code": code,
        "expected": expected_details(check),
        "observed": observed,
    }


def passed(check: dict[str, Any], observed: Any) -> dict[str, Any]:
    return {
        "object_id": check["id"],
        "object_type": check["kind"],
        "result": "present",
        "reason_code": "OBJECT_MATCHES",
        "expected": expected_details(check),
        "observed": observed,
    }


def expected_details(check: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in check.items() if key not in {"id", "preexisting"}
    }


def evaluate_column(check: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    observed = catalog["columns"].get((check["table"], check["column"]))
    if observed is None:
        return missing(check, "COLUMN_MISSING")
    mismatches = {}
    if observed["type"] != check["type"]:
        mismatches["type"] = observed["type"]
    if "nullable" in check and observed["nullable"] != check["nullable"]:
        mismatches["nullable"] = observed["nullable"]
    if "default" in check and normalize_expression(
        observed["default"]
    ) != normalize_expression(check["default"]):
        mismatches["default"] = observed["default"]
    if mismatches:
        if check.get("preexisting"):
            return missing(check, "COLUMN_ALTERATION_NOT_APPLIED", observed)
        return divergent(check, "COLUMN_DEFINITION_DIVERGENT", observed)
    return passed(check, observed)


def constraint_type(value: str) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii")
    return {"p": "primary_key", "u": "unique", "f": "foreign_key", "c": "check"}.get(
        value, value
    )


def evaluate_constraint(
    check: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any]:
    candidates = [
        item
        for item in catalog["constraints"]
        if item["table_name"] == check["table"]
        and constraint_type(item["contype"]) == check["constraint_type"]
        and list(item["columns"] or []) == check["columns"]
    ]
    if not candidates:
        return missing(check, "CONSTRAINT_MISSING")
    observed = candidates[0]
    if not observed["convalidated"]:
        return divergent(check, "CONSTRAINT_NOT_VALIDATED", observed)
    if check["constraint_type"] == "foreign_key":
        delete_code = observed["confdeltype"]
        if isinstance(delete_code, bytes):
            delete_code = delete_code.decode("ascii")
        on_delete = {
            "a": "NO ACTION",
            "r": "RESTRICT",
            "c": "CASCADE",
            "n": "SET NULL",
            "d": "SET DEFAULT",
        }.get(delete_code, delete_code)
        if (
            observed["referred_table"] != check["referred_table"]
            or list(observed["referred_columns"] or []) != check["referred_columns"]
            or on_delete != check["on_delete"]
        ):
            return divergent(check, "FOREIGN_KEY_DEFINITION_DIVERGENT", observed)
    return passed(check, observed)


def evaluate_index(check: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    observed = catalog["indexes"].get(check["name"])
    if observed is None:
        return missing(check, "INDEX_MISSING")
    actual = {
        "table": observed["table_name"],
        "columns": list(observed["columns"] or []),
        "unique": observed["indisunique"],
        "method": observed["method"],
        "predicate": observed["predicate"],
        "valid": observed["indisvalid"],
        "ready": observed["indisready"],
        "definition": observed["definition"],
    }
    if not observed["indisvalid"] or not observed["indisready"]:
        return divergent(check, "INDEX_NOT_VALID_OR_READY", actual)
    if observed["table_name"] != check["table"]:
        return divergent(check, "INDEX_TABLE_DIVERGENT", actual)
    if list(observed["columns"] or []) != check["columns"]:
        return divergent(check, "INDEX_COLUMNS_DIVERGENT", actual)
    if observed["indisunique"] != check["unique"]:
        return divergent(check, "INDEX_UNIQUENESS_DIVERGENT", actual)
    if observed["method"] != check["method"]:
        return divergent(check, "INDEX_METHOD_DIVERGENT", actual)
    if normalize_expression(observed["predicate"]) != normalize_expression(
        check["predicate"]
    ):
        return divergent(check, "INDEX_PREDICATE_DIVERGENT", actual)
    return passed(check, actual)


def evaluate_check(check: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    if check["kind"] == "table":
        return (
            passed(check, {"table": check["table"]})
            if check["table"] in catalog["tables"]
            else missing(check, "TABLE_MISSING")
        )
    if check["kind"] == "column":
        return evaluate_column(check, catalog)
    if check["kind"] == "constraint":
        return evaluate_constraint(check, catalog)
    if check["kind"] == "index":
        return evaluate_index(check, catalog)
    raise ValueError(f"Unsupported check kind: {check['kind']}")


def evaluate_manual_migrations(catalog: dict[str, Any]) -> dict[str, Any]:
    results = {}
    for filename, specification in MANUAL_MIGRATIONS.items():
        checks = [evaluate_check(check, catalog) for check in specification["checks"]]
        counts = {
            result: sum(check["result"] == result for check in checks)
            for result in ("present", "missing", "divergent")
        }
        if counts["divergent"]:
            status = "inconsistent"
        elif counts["present"] == len(checks):
            status = "complete"
        elif counts["missing"] == len(checks):
            status = "not_applied"
        else:
            status = "partial"
        results[filename] = {
            "status": status,
            "summary": {"total": len(checks), **counts},
            "checks": checks,
            "non_schema_effects": specification.get("non_schema_effects", []),
        }
    return results


def sorted_names(items: list[Mapping[str, Any]], key: str = "name") -> list[str]:
    return sorted(item[key] for item in items if item.get(key))


def unique_column_sets(inspector, table: str) -> list[list[str]]:
    values = [
        sorted(item.get("column_names") or [])
        for item in inspector.get_unique_constraints(table)
    ]
    values.extend(
        sorted(item.get("column_names") or [])
        for item in inspector.get_indexes(table)
        if item.get("unique")
    )
    return sorted(values)


def snapshot(connection: Connection) -> dict[str, Any]:
    inspector = inspect(connection)
    catalog = catalog_snapshot(connection)
    actual_tables = sorted(catalog["tables"])
    expected_tables = sorted(Base.metadata.tables)
    expected: dict[str, Any] = {}
    actual: dict[str, Any] = {}
    for name in expected_tables:
        table = Base.metadata.tables[name]
        expected[name] = {
            "columns": sorted(column.name for column in table.columns),
            "primary_key": sorted(column.name for column in table.primary_key.columns),
            "unique_constraints": sorted(
                sorted(column.name for column in constraint.columns)
                for constraint in table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            ),
        }
    for name in actual_tables:
        actual[name] = {
            "columns": sorted_names(inspector.get_columns(name)),
            "primary_key": sorted(
                inspector.get_pk_constraint(name).get("constrained_columns") or []
            ),
            "unique_constraints": unique_column_sets(inspector, name),
            "indexes": sorted_names(inspector.get_indexes(name)),
            "foreign_keys": sorted(
                [
                    {
                        "columns": sorted(fk.get("constrained_columns") or []),
                        "referred_table": fk.get("referred_table"),
                        "referred_columns": sorted(fk.get("referred_columns") or []),
                    }
                    for fk in inspector.get_foreign_keys(name)
                ],
                key=lambda item: (item["referred_table"] or "", item["columns"]),
            ),
        }
    missing_tables = sorted(set(expected_tables) - set(actual_tables))
    unexpected_tables = sorted(
        set(actual_tables) - set(expected_tables) - {"alembic_version"}
    )
    column_differences = {}
    for name in sorted(set(expected_tables) & set(actual_tables)):
        expected_columns, actual_columns = set(expected[name]["columns"]), set(
            actual[name]["columns"]
        )
        if expected_columns != actual_columns:
            column_differences[name] = {
                "missing_in_database": sorted(expected_columns - actual_columns),
                "additional_in_database": sorted(actual_columns - expected_columns),
            }
    alembic_rows = []
    if "alembic_version" in actual_tables:
        alembic_rows = list(
            connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars()
        )
    return {
        "mode": "read_only",
        "database": {
            "dialect": connection.dialect.name,
            "server_version": ".".join(
                str(part) for part in connection.dialect.server_version_info
            ),
        },
        "alembic": {
            "version_table_present": "alembic_version" in actual_tables,
            "versions": alembic_rows,
        },
        "comparison": {
            "matches_expected_table_and_column_names": not missing_tables
            and not column_differences,
            "missing_tables": missing_tables,
            "unexpected_tables": unexpected_tables,
            "column_differences": column_differences,
        },
        "manual_migration_signals": evaluate_manual_migrations(catalog),
        "expected_schema": expected,
        "actual_schema": actual,
    }


async def inspect_database(url: str) -> dict[str, Any]:
    engine = create_async_engine(async_url(url), pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(snapshot)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Write JSON to this path instead of stdout."
    )
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print(
            "DATABASE_URL must reference an authorized PostgreSQL database",
            file=sys.stderr,
        )
        return 2
    rendered = (
        json.dumps(
            asyncio.run(inspect_database(url)),
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n"
    )
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
