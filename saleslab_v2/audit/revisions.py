"""Explicit, persisted, bounded revision authority. No caller-supplied feedback."""

from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import select

from saleslab_v2.analyst.context import InvalidCheckpoint
from saleslab_v2.analyst.contracts import ANALYST_VERSION
from saleslab_v2.domain.idempotency import IdempotencyConflict, semantic_hash
from saleslab_v2.metrics.trace import TRACE_NAMESPACE
from saleslab_v2.persistence.models import StageResultModel

from .contracts import AUDITOR_VERSION, MAX_REVISIONS, assemble_result


async def _parent(session, run_id, audit_id):
    stage = await session.get(StageResultModel, audit_id, populate_existing=True)
    if (
        stage is None
        or stage.run_id != run_id
        or stage.stage_name != "financial_audit"
        or stage.stage_version != AUDITOR_VERSION
        or stage.status != "completed"
        or stage.output_hash != semantic_hash(stage.payload)
    ):
        raise InvalidCheckpoint("a completed scoped audit is required for revision")
    result = stage.payload
    inputs = list(
        (
            await session.scalars(
                select(StageResultModel)
                .where(
                    StageResultModel.run_id == run_id,
                    StageResultModel.stage_name == "financial_audit_input",
                    StageResultModel.stage_version == AUDITOR_VERSION,
                    StageResultModel.output_hash == stage.input_hash,
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    if (
        len(inputs) != 1
        or inputs[0].status != "completed"
        or inputs[0].input_hash != stage.input_hash
        or semantic_hash(inputs[0].payload) != stage.input_hash
    ):
        raise InvalidCheckpoint("parent audit input integrity mismatch")
    stored = inputs[0].payload
    draft = await session.get(
        StageResultModel, UUID(result["draft_stage_id"]), populate_existing=True
    )
    if (
        draft is None
        or draft.run_id != run_id
        or draft.stage_name != "financial_analyst"
        or draft.status != "completed"
        or draft.output_hash != result["draft_hash"]
        or semantic_hash(draft.payload) != draft.output_hash
    ):
        raise InvalidCheckpoint("parent audit is not bound to its original draft")
    expected = assemble_result(
        audit_id=stage.id,
        run_id=run_id,
        draft_stage_id=draft.id,
        draft_hash=draft.output_hash,
        checkpoint_hash=stored["checkpoint_hash"],
        checks=result["checks"],
        finding_ids={f["finding_id"] for f in draft.payload["findings"]},
        configuration=stored["configuration"],
        audited_at=datetime.fromisoformat(result["audited_at"]),
        revision_number=result["revision_number"],
        retryable=result["retryable"],
    )
    if (
        result != expected
        or result["status"] != "revision_required"
        or result["retryable"]
        or not result["required_revisions"]
    ):
        raise InvalidCheckpoint("parent audit does not authorize revision")
    return stage, draft


async def _revision_history(session, run_id):
    """Derive ordinals from immutable execution order, never stored counters."""
    first_authority = await session.scalar(
        select(StageResultModel.started_at)
        .where(
            StageResultModel.run_id == run_id,
            StageResultModel.stage_name == "financial_audit",
            StageResultModel.stage_version == AUDITOR_VERSION,
            StageResultModel.status == "completed",
            StageResultModel.payload["status"].astext == "revision_required",
        )
        .order_by(StageResultModel.started_at)
        .limit(1)
    )
    inputs = (
        await session.scalars(
            select(StageResultModel)
            .where(
                StageResultModel.run_id == run_id,
                StageResultModel.stage_name == "financial_analyst_input",
            )
            .order_by(StageResultModel.started_at, StageResultModel.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    revisions = []
    for stage in inputs:
        if (
            stage.stage_version != ANALYST_VERSION
            or stage.status != "completed"
            or stage.input_hash != stage.output_hash
            or stage.output_hash != semantic_hash(stage.payload)
            or stage.id
            != uuid5(
                TRACE_NAMESPACE,
                f"analyst-input:{run_id}:{stage.payload['attempt_key']}",
            )
        ):
            raise InvalidCheckpoint("revision attempt history integrity mismatch")
        revision = stage.payload.get("context", {}).get("revision")
        is_revision_attempt = (
            first_authority is not None and stage.started_at > first_authority
        )
        if not is_revision_attempt and revision is None:
            continue
        ordinal = len(revisions) + 1
        if (
            not isinstance(revision, dict)
            or type(revision.get("number")) is not int
            or revision["number"] != ordinal
            or revision.get("budget_exhausted") is not (ordinal > MAX_REVISIONS)
        ):
            raise InvalidCheckpoint(
                "revision budget differs from persisted attempt history"
            )
        revisions.append(stage)
    return revisions


async def revision_for_input(session, run_id, input_stage):
    history = await _revision_history(session, run_id)
    if any(s.id == input_stage.id for s in history):
        return input_stage.payload["context"]["revision"]
    return None


async def verify_revision_parent(session, run_id, revision, *, input_stage_id):
    if set(revision) != {
        "audit_stage_id",
        "audit_hash",
        "draft_stage_id",
        "draft_hash",
        "number",
        "required_revisions",
        "previous_findings",
        "budget_exhausted",
    }:
        raise InvalidCheckpoint("invalid revision metadata")
    stage, draft = await _parent(session, run_id, UUID(revision["audit_stage_id"]))
    if (
        revision["audit_hash"] != stage.output_hash
        or revision["draft_stage_id"] != str(draft.id)
        or revision["draft_hash"] != draft.output_hash
        or revision["required_revisions"] != stage.payload["required_revisions"]
        or revision["previous_findings"] != draft.payload["findings"]
    ):
        raise InvalidCheckpoint("revision feedback differs from authorized audit")
    history = await _revision_history(session, run_id)
    if not any(
        s.id == input_stage_id and s.payload["context"]["revision"] == revision
        for s in history
    ):
        raise InvalidCheckpoint("revision is not a persisted attempt in this run")
    return stage, draft


async def prepare_revision(session, run, audit_id, attempt_key):
    parent, draft = await _parent(session, run.id, audit_id)
    saved = await session.get(
        StageResultModel,
        uuid5(TRACE_NAMESPACE, f"analyst-input:{run.id}:{attempt_key}"),
        populate_existing=True,
    )
    if saved:
        revision = saved.payload.get("context", {}).get("revision")
        if not revision or revision["audit_stage_id"] != str(audit_id):
            raise IdempotencyConflict(
                "analyst attempt key has another revision authority"
            )
        await verify_revision_parent(session, run.id, revision, input_stage_id=saved.id)
        return deepcopy(revision)
    if run.workflow_status != "analyzing":
        raise IdempotencyConflict("run is not awaiting an authorized revision")
    latest = await session.scalar(
        select(StageResultModel.id)
        .where(
            StageResultModel.run_id == run.id,
            StageResultModel.stage_name == "financial_analyst",
            StageResultModel.status == "completed",
        )
        .order_by(StageResultModel.started_at.desc(), StageResultModel.id.desc())
        .limit(1)
    )
    if latest != draft.id:
        raise IdempotencyConflict("revision audit targets a superseded draft")
    count = len(await _revision_history(session, run.id))
    return {
        "audit_stage_id": str(audit_id),
        "audit_hash": parent.output_hash,
        "draft_stage_id": str(draft.id),
        "draft_hash": draft.output_hash,
        "number": count + 1,
        "required_revisions": deepcopy(parent.payload["required_revisions"]),
        "previous_findings": deepcopy(draft.payload["findings"]),
        "budget_exhausted": count >= MAX_REVISIONS,
    }
