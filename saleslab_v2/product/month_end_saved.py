"""Immutable product preparation snapshots; no models, paid history or quota."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid5

from pydantic import Field, field_validator
from sqlalchemy import func, select, tuple_
from starlette.concurrency import run_in_threadpool

from models import WorkspaceMember
from saleslab_v2.domain.enums import ModuleType, StageStatus
from saleslab_v2.domain.idempotency import (
    IdempotencyConflict,
    canonicalize,
    semantic_hash,
)
from saleslab_v2.domain.runs import AnalysisRun, StageResult
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import (
    AnalysisRunModel,
    SourceDocumentModel,
    StageResultModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.pipeline.replay import restore_request
from saleslab_v2.pipeline.service import _ensure_source

from .contracts import InvalidUpload, UploadChoices
from .ingestion import ParsedUpload
from .jobs import MAX_ATTEMPTS_PER_HOUR
from .month_end import MonthEndChoices, StrictModel, prepare_month_end
from .planning import prepare_upload
from .service import available

VERSION = "financial-month-end-product-snapshot-v1"
PURPOSE = "financial-month-end-product-snapshot"
STAGES = ("monthly_snapshot_input", "monthly_snapshot_result")
MAX_BYTES = 2_000_000
PAGE_SIZE = 10


class InvalidMonthlySnapshot(ValueError):
    pass


class SaveMonthEndChoices(StrictModel):
    binding_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_key: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")

    @field_validator("attempt_key")
    @classmethod
    def validate_attempt(cls, value):
        return str(UUID(value))


def readable(settings, workspace_id, role):
    return bool(
        settings.enabled
        and settings.persist_stages
        and str(workspace_id) in settings.internal_workspaces
        and role in {"owner", "manager"}
    )


def stage_id(run_id, name):
    return uuid5(TRACE_NAMESPACE, f"{VERSION}:{run_id}:{name}")


def bounded(payload):
    if len(json.dumps(payload, ensure_ascii=True).encode()) > MAX_BYTES:
        raise InvalidUpload(
            "Monthly snapshot exceeds 2 MB; select fewer source rows or comparisons."
        )


def input_snapshot(parsed, choices, close, user_id):
    request, _ = prepare_upload(parsed, choices, user_id, "preview")
    result = prepare_month_end(parsed, choices, close, user_id)
    data = {
        "version": VERSION,
        "actor_id": str(user_id),
        "request": canonicalize(request.semantic_payload()),
        "request_hash": request.fingerprint,
        "upload_choices": choices.contract_payload(),
        "close_choices": close.model_dump(),
        "original_rows": list(parsed.original_rows),
        "mapping_decisions": parsed.decisions,
        "source_import": parsed.import_manifest,
        "binding_hash": result["binding_hash"],
    }
    bounded(data)
    bounded(result)
    return request, data, result


def restore_input(data, run):
    if data["version"] != VERSION or data["actor_id"] != str(run.created_by_user_id):
        raise InvalidMonthlySnapshot("Monthly snapshot actor or version differs.")
    request = restore_request(data["request"], run=run)
    if len(request.sources) != 1 or request.fingerprint != data["request_hash"]:
        raise InvalidMonthlySnapshot("Monthly snapshot source request differs.")
    choices = UploadChoices.model_validate(data["upload_choices"])
    close = MonthEndChoices.model_validate(data["close_choices"])
    parsed = ParsedUpload(
        request.sources[0],
        tuple(deepcopy(data["original_rows"])),
        deepcopy(data["mapping_decisions"]),
        deepcopy(data["source_import"]),
    )
    fresh, fresh_input, result = input_snapshot(
        parsed, choices, close, run.created_by_user_id
    )
    if fresh_input != data or canonicalize(fresh.semantic_payload()) != data["request"]:
        raise InvalidMonthlySnapshot("Monthly snapshot input replay differs.")
    return request, result


def semantic_result(payload):
    result = {
        k: v
        for k, v in payload.items()
        if k
        not in {"generated_at", "payload_hash", "snapshot_id", "saved_at", "persisted"}
    }
    if payload.get("persisted") is True:
        result["schema"] = result.pop("preparation_schema")
    return result


def saved_payload(prepared, run_id, saved_at):
    value = {
        **deepcopy(prepared),
        "persisted": True,
        "schema": VERSION,
        "preparation_schema": prepared["schema"],
        "snapshot_id": str(run_id),
        "saved_at": saved_at.isoformat(),
    }
    value.pop("payload_hash", None)
    payload = canonicalize({**value, "payload_hash": semantic_hash(value)})
    bounded(payload)
    return payload


class MonthlySnapshots:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    async def authorize(self, workspace_id, user_id, *, write=False):
        role = await self.session.scalar(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if not readable(self.settings, workspace_id, role) or (
            write
            and not (
                available(self.settings, workspace_id, role)
                and self.settings.financial_month_end_enabled
            )
        ):
            raise PermissionError(
                "Monthly preparation snapshots are unavailable for this actor."
            )

    async def save(self, parsed, choices, close, save, user_id):
        parsed = deepcopy(parsed)
        choices = UploadChoices.model_validate(choices.model_dump())
        close = MonthEndChoices.model_validate(close.model_dump())
        save = SaveMonthEndChoices.model_validate(save.model_dump())
        await self.authorize(parsed.batch.document.workspace_id, user_id, write=True)
        request, data, prepared = await run_in_threadpool(
            input_snapshot, parsed, choices, close, user_id
        )
        if prepared["binding_hash"] != save.binding_hash:
            raise IdempotencyConflict(
                "The monthly source or policy changed; prepare and review the checklist again."
            )
        workspace_id = request.workspace_id
        digest = semantic_hash(data)
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        "monthly-product-snapshots:" + str(workspace_id), 0
                    )
                )
            )
        )
        await self.authorize(workspace_id, user_id, write=True)
        existing = await self.session.scalar(
            select(AnalysisRunModel).where(
                AnalysisRunModel.workspace_id == workspace_id,
                AnalysisRunModel.idempotency_purpose == PURPOSE,
                AnalysisRunModel.idempotency_key == save.attempt_key,
            )
        )
        if existing is not None:
            stages = await self._stored(existing)
            if stages[STAGES[0]].payload != data:
                raise IdempotencyConflict(
                    "This monthly save attempt has different source or policy decisions."
                )
            return await self.read(workspace_id, user_id, existing.id), False
        count = await self.session.scalar(
            select(func.count())
            .select_from(AnalysisRunModel)
            .where(
                AnalysisRunModel.workspace_id == workspace_id,
                AnalysisRunModel.idempotency_purpose == PURPOSE,
                AnalysisRunModel.created_at >= func.now() - timedelta(hours=1),
            )
        )
        if count >= MAX_ATTEMPTS_PER_HOUR:
            from fastapi import HTTPException

            raise HTTPException(
                429,
                "This workspace has reached the hourly monthly-snapshot save limit.",
            )
        repo = V2Repository(self.session, workspace_id, persist_stages=True)
        await _ensure_source(repo, parsed.batch.document)
        candidate = AnalysisRun(
            workspace_id,
            user_id,
            ModuleType.FINANCIAL,
            "1",
            VERSION,
            request.language,
            (parsed.batch.document.source_id,),
            (parsed.batch.document.content_hash, prepared["binding_hash"], digest),
            PURPOSE,
            save.attempt_key,
        )
        run, created = await repo.get_or_create_run(candidate)
        if not created:
            raise IdempotencyConflict("The monthly save attempt changed concurrently.")
        now = datetime.now(UTC)
        snapshot = saved_payload(prepared, run.id, now)
        for name, payload in zip(STAGES, (data, snapshot)):
            await repo.persist_stage(
                StageResult(
                    run.id,
                    name,
                    VERSION,
                    StageStatus.COMPLETED,
                    digest,
                    semantic_hash(payload),
                    "1",
                    payload,
                    stage_result_id=stage_id(run.id, name),
                    started_at=now,
                    completed_at=now,
                )
            )
        run.workflow_status, run.started_at, run.completed_at = "completed", now, now
        await self.session.flush()
        return deepcopy(snapshot), True

    async def _stored(self, run):
        try:
            return await self._verify_stored(run)
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise InvalidMonthlySnapshot(
                "The saved monthly snapshot cannot be verified."
            ) from exc

    async def _verify_stored(self, run):
        rows = (
            await self.session.scalars(
                select(StageResultModel)
                .where(StageResultModel.run_id == run.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        if (
            len(rows) != 2
            or {r.stage_name for r in rows} != set(STAGES)
            or run.workflow_status != "completed"
            or run.completed_at is None
            or run.pipeline_version != VERSION
            or run.idempotency_purpose != PURPOSE
            or run.module != "financial"
            or run.schema_version != "1"
        ):
            raise InvalidMonthlySnapshot(
                "Monthly snapshot is incomplete or has a different contract."
            )
        stages = {r.stage_name: r for r in rows}
        data = stages[STAGES[0]].payload
        if not isinstance(data, dict) or not isinstance(
            stages[STAGES[1]].payload, dict
        ):
            raise InvalidMonthlySnapshot("Monthly snapshot payload shape differs.")
        digest = semantic_hash(data)
        for name, row in stages.items():
            if (
                row.id != stage_id(run.id, name)
                or row.stage_version != VERSION
                or row.status != "completed"
                or row.output_schema_version != "1"
                or row.input_hash != digest
                or row.output_hash != semantic_hash(row.payload)
                or row.completed_at is None
            ):
                raise InvalidMonthlySnapshot("Monthly snapshot stage differs.")
        snapshot = stages[STAGES[1]].payload
        if (
            snapshot.get("schema") != VERSION
            or snapshot.get("snapshot_id") != str(run.id)
            or snapshot.get("saved_at") != run.completed_at.isoformat()
            or snapshot.get("persisted") is not True
            or snapshot.get("quota_consumed") is not False
            or semantic_hash({k: v for k, v in snapshot.items() if k != "payload_hash"})
            != snapshot.get("payload_hash")
            or run.input_hashes
            != [snapshot.get("source_hash"), data.get("binding_hash"), digest]
        ):
            raise InvalidMonthlySnapshot("Monthly snapshot binding differs.")
        request = restore_request(data["request"], run=run)
        candidate = AnalysisRun(
            run.workspace_id,
            run.created_by_user_id,
            ModuleType.FINANCIAL,
            "1",
            VERSION,
            request.language,
            (request.sources[0].document.source_id,),
            tuple(run.input_hashes),
            PURPOSE,
            run.idempotency_key,
        )
        if (
            candidate.request_fingerprint != run.request_fingerprint
            or data.get("actor_id") != str(run.created_by_user_id)
            or data.get("request_hash") != request.fingerprint
        ):
            raise InvalidMonthlySnapshot("Monthly snapshot index identity differs.")
        return stages

    async def read(self, workspace_id, user_id, run_id):
        await self.authorize(workspace_id, user_id)
        run = await self.session.scalar(
            select(AnalysisRunModel)
            .where(
                AnalysisRunModel.id == run_id,
                AnalysisRunModel.workspace_id == workspace_id,
                AnalysisRunModel.idempotency_purpose == PURPOSE,
            )
            .execution_options(populate_existing=True)
        )
        if run is None:
            raise PermissionError("Monthly snapshot is unavailable.")
        try:
            stages = await self._stored(run)
            data = stages[STAGES[0]].payload
            snapshot = stages[STAGES[1]].payload
            context = SimpleNamespace(
                workspace_id=run.workspace_id,
                created_by_user_id=run.created_by_user_id,
                idempotency_key=run.idempotency_key,
                language_context=deepcopy(run.language_context),
                created_at=run.created_at,
            )
            request, fresh = await run_in_threadpool(restore_input, data, context)
            if semantic_result(snapshot) != semantic_result(fresh):
                raise InvalidMonthlySnapshot("Monthly snapshot result replay differs.")
            candidate = AnalysisRun(
                workspace_id,
                run.created_by_user_id,
                ModuleType.FINANCIAL,
                "1",
                VERSION,
                request.language,
                (request.sources[0].document.source_id,),
                tuple(run.input_hashes),
                PURPOSE,
                run.idempotency_key,
            )
            if (
                candidate.request_fingerprint != run.request_fingerprint
                or run.source_ids != [str(request.sources[0].document.source_id)]
                or run.language_context != request.language.to_dict()
            ):
                raise InvalidMonthlySnapshot("Monthly snapshot run identity differs.")
            doc = request.sources[0].document
            source = await self.session.scalar(
                select(SourceDocumentModel)
                .where(
                    SourceDocumentModel.id == doc.source_id,
                    SourceDocumentModel.workspace_id == workspace_id,
                )
                .execution_options(populate_existing=True)
            )
            expected = {
                "filename": doc.filename,
                "source_format": doc.source_format.value,
                "content_hash": doc.content_hash,
                "parser_version": doc.parser_version,
                "trust_level": doc.trust_level.value,
                "language_metadata": [v.value for v in doc.languages],
                "supersedes_source_id": doc.supersedes_source_id,
            }
            if source is None or any(
                getattr(source, k) != v for k, v in expected.items()
            ):
                raise InvalidMonthlySnapshot(
                    "Monthly snapshot source registration differs."
                )
            return canonicalize(snapshot)
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise InvalidMonthlySnapshot(
                "The saved monthly snapshot cannot be verified."
            ) from exc

    async def history(self, workspace_id, user_id, cursor=None):
        await self.authorize(workspace_id, user_id)
        filters = [
            AnalysisRunModel.workspace_id == workspace_id,
            AnalysisRunModel.idempotency_purpose == PURPOSE,
            AnalysisRunModel.pipeline_version == VERSION,
        ]
        if cursor:
            previous = await self.session.scalar(
                select(AnalysisRunModel).where(*filters, AnalysisRunModel.id == cursor)
            )
            if previous is None:
                raise PermissionError("Monthly snapshot cursor is unavailable.")
            filters.append(
                tuple_(AnalysisRunModel.created_at, AnalysisRunModel.id)
                < tuple_(previous.created_at, previous.id)
            )
        runs = (
            await self.session.scalars(
                select(AnalysisRunModel)
                .where(*filters)
                .order_by(
                    AnalysisRunModel.created_at.desc(), AnalysisRunModel.id.desc()
                )
                .limit(PAGE_SIZE + 1)
            )
        ).all()
        items = []
        for run in runs[:PAGE_SIZE]:
            try:
                stages = await self._stored(run)
                data = stages[STAGES[0]].payload
                items.append(
                    {
                        "snapshot_id": str(run.id),
                        "saved_at": run.completed_at.isoformat(),
                        "target_month": data["close_choices"]["target_month"],
                        "filename": data["request"]["sources"][0]["document"][
                            "filename"
                        ],
                        "policy_id": data["close_choices"]["policy_id"],
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidMonthlySnapshot(
                    "The monthly snapshot index cannot be verified."
                ) from exc
        return {
            "snapshots": items,
            "next_cursor": (
                str(runs[PAGE_SIZE - 1].id) if len(runs) > PAGE_SIZE else None
            ),
        }
