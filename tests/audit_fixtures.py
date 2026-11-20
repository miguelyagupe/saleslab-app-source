"""Synthetic F-07 fixtures and deterministic recording critic, no live LLM."""

from copy import deepcopy
from datetime import datetime

from analyst_fixtures import context_fixture, model_output

from saleslab_v2.analyst.contracts import ANALYST_VERSION, bind_findings
from saleslab_v2.audit.contracts import CRITIC_CHECKS
from saleslab_v2.domain.idempotency import semantic_hash


def critic_output(context):
    return {
        "schema_version": "1",
        "language": context["evidence_context"]["language"],
        "findings": [
            {
                "finding_id": f["finding_id"],
                "checks": [
                    {
                        "check_type": family,
                        "outcome": "pass",
                        "message": "Supported by the supplied evidence.",
                        "remediation": None,
                    }
                    for family in sorted(CRITIC_CHECKS)
                ],
            }
            for f in context["draft"]["findings"]
        ],
    }


class RecordingCritic:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform
        self.configuration = {
            "provider": "test",
            "model": "critic-v1",
            "role": "independent_critic",
        }

    async def generate(self, context):
        self.calls.append(deepcopy(context))
        raw = critic_output(context)
        return self.transform(raw, context) if self.transform else raw


def draft_fixture(request):
    run, stages, _, context = context_fixture(request)
    run.created_at = run.started_at = datetime.fromisoformat(
        context["quality"]["coverage"]["assessed_at"]
    )
    run.created_by_user_id = request.user_id
    run.idempotency_key = request.idempotency_key
    saved_input = {
        "context": context,
        "configuration": {"provider": "test", "model": "recording-v1"},
        "attempt_key": "initial",
        "analyst_version": ANALYST_VERSION,
    }
    draft = {
        "status": "draft",
        "run_id": str(run.id),
        "checkpoint_hash": context["checkpoint_hash"],
        "context_hash": semantic_hash(context),
        "configuration": saved_input["configuration"],
        "attempt_key": "initial",
        "language": context["language"],
        "data_confidence": context["quality"],
        "financial_snapshot": context["evidence"],
        "reconciliations": context["reconciliations"],
        "findings": bind_findings(model_output(context), context),
        "forecasts": [],
        "recommendations": [],
        "missing_data_requests": context["missing_data_requests"],
        "source_index": context["sources"],
        "requires_independent_audit": True,
        "audited": False,
    }
    return run, stages, context, saved_input, draft
