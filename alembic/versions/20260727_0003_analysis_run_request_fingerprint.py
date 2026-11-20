"""Backfill the complete AnalysisRun idempotency fingerprint.

The canonicalization below is deliberately frozen in this revision. Importing the
application domain here would make historical migrations change when application
code evolves.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from alembic import context, op
import sqlalchemy as sa

revision = "20260727_0003"
down_revision = "20260727_0002"
branch_labels = None
depends_on = None

_ALLOWED_MODULES = frozenset({"financial", "sales", "cost", "efficiency"})
_ALLOWED_LANGUAGES = frozenset({"pt", "en", "es", "fr"})
_LANGUAGE_KEYS = frozenset(
    {
        "source_languages",
        "analysis_language",
        "report_language",
        "format_locale",
        "accounting_context",
    }
)


def _canonical_uuid(value: object) -> dict[str, str]:
    if not isinstance(value, str):
        raise ValueError("source_ids entries must be UUID strings")
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("source_ids entries must be canonical lowercase UUID strings")
    return {"$uuid": value}


def _fingerprint(row: sa.RowMapping) -> str:
    """Frozen equivalent of the F-01.1 AnalysisRun semantic hash contract."""
    module = row["module"]
    schema_version = row["schema_version"]
    pipeline_version = row["pipeline_version"]
    source_ids = row["source_ids"]
    input_hashes = row["input_hashes"]
    language = row["language_context"]

    if module not in _ALLOWED_MODULES:
        raise ValueError(f"unsupported module {module!r}")
    if not isinstance(schema_version, str) or not schema_version:
        raise ValueError("schema_version must be a non-empty string")
    if not isinstance(pipeline_version, str) or not pipeline_version:
        raise ValueError("pipeline_version must be a non-empty string")
    if not isinstance(source_ids, list) or not source_ids:
        raise ValueError("source_ids must be a non-empty JSON array")
    canonical_sources = [_canonical_uuid(value) for value in source_ids]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("source_ids must not contain duplicates")
    if not isinstance(input_hashes, list) or not all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in input_hashes
    ):
        raise ValueError("input_hashes must contain lowercase SHA-256 hex strings")
    if not isinstance(language, dict) or set(language) != _LANGUAGE_KEYS:
        raise ValueError("language_context has an incompatible shape")
    source_languages = language["source_languages"]
    if not isinstance(source_languages, list) or not all(
        isinstance(value, str) and value in _ALLOWED_LANGUAGES
        for value in source_languages
    ):
        raise ValueError("source_languages contains an unsupported language")
    for key in ("analysis_language", "report_language"):
        if language[key] not in _ALLOWED_LANGUAGES:
            raise ValueError(f"{key} contains an unsupported language")
    accounting_context = language["accounting_context"]
    if accounting_context is not None and not isinstance(accounting_context, str):
        raise ValueError("accounting_context must be a string or null")

    payload = {
        "accounting_context": accounting_context,
        "analysis_language": language["analysis_language"],
        "input_hashes": input_hashes,
        "module": module,
        "pipeline_version": pipeline_version,
        "report_language": language["report_language"],
        "schema_version": schema_version,
        "source_ids": canonical_sources,
        "source_languages": source_languages,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def upgrade():
    op.add_column(
        "analysis_runs_v2",
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
    )
    if not context.is_offline_mode():
        table = sa.table(
            "analysis_runs_v2",
            sa.column("id", sa.Uuid()),
            sa.column("module", sa.String()),
            sa.column("schema_version", sa.String()),
            sa.column("pipeline_version", sa.String()),
            sa.column("source_ids", sa.JSON()),
            sa.column("input_hashes", sa.JSON()),
            sa.column("language_context", sa.JSON()),
            sa.column("request_fingerprint", sa.String()),
        )
        connection = op.get_bind()
        rows = connection.execute(
            sa.select(
                table.c.id,
                table.c.module,
                table.c.schema_version,
                table.c.pipeline_version,
                table.c.source_ids,
                table.c.input_hashes,
                table.c.language_context,
            )
        ).mappings()
        for row in rows:
            try:
                fingerprint = _fingerprint(row)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"cannot safely backfill request fingerprint for AnalysisRun "
                    f"{row['id']}: {exc}"
                ) from exc
            connection.execute(
                table.update()
                .where(table.c.id == row["id"])
                .values(request_fingerprint=fingerprint)
            )
    op.alter_column(
        "analysis_runs_v2",
        "request_fingerprint",
        existing_type=sa.String(64),
        nullable=False,
    )


def downgrade():
    op.drop_column("analysis_runs_v2", "request_fingerprint")
