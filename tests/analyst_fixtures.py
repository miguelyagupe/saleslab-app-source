"""Synthetic evidence and a recording model; never calls a paid provider."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from saleslab_v2.analyst.context import build_context, verify_checkpoint
from saleslab_v2.domain.idempotency import canonicalize, semantic_hash
from saleslab_v2.pipeline.contracts import STAGES
from saleslab_v2.pipeline.evaluator import evaluate


def model_output(context):
    language = context["language"]
    title, statement = {
        "pt": ("Observação financeira", "O valor observado é"),
        "en": ("Financial observation", "The observed value is"),
        "es": ("Observación financiera", "El valor observado es"),
        "fr": ("Observation financière", "La valeur observée est"),
    }[language]
    return {
        "schema_version": "1",
        "language": language,
        "findings": [
            {
                "title": title,
                "statement": statement + " {{" + e["evidence_id"] + "}}.",
                "claim_type": (
                    "fact" if e["capability"] == "summarize" else "calculation"
                ),
                "capability": e["capability"],
                "evidence_ids": [e["evidence_id"]],
            }
            for e in context["evidence"]
        ],
    }


class RecordingModel:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform
        self.configuration = {"provider": "test", "model": "recording-v1"}

    async def generate(self, context):
        self.calls.append(deepcopy(context))
        raw = model_output(context)
        return self.transform(raw, context) if self.transform else raw


def context_fixture(request):
    run_id = uuid4()
    evaluation = evaluate(request, run_id, datetime.now(UTC))
    run = SimpleNamespace(
        id=run_id,
        workspace_id=request.workspace_id,
        source_ids=[str(s.document.source_id) for s in request.sources],
        input_hashes=[request.fingerprint],
        language_context=request.language.to_dict(),
    )
    stages, previous = {}, request.fingerprint
    for name, payload in zip(STAGES, evaluation.payloads):
        payload = canonicalize(payload)
        stages[name] = SimpleNamespace(
            run_id=run_id,
            status="completed",
            input_hash=previous,
            output_hash=semantic_hash(payload),
            payload=payload,
        )
        previous = stages[name].output_hash
    payload = {
        "run_id": str(run_id),
        "request_hash": request.fingerprint,
        "ready": evaluation.ready,
        "workflow_status": evaluation.workflow_status.value,
        "requested_capabilities": [c.value for c in request.requested_capabilities],
        "metrics": evaluation.payloads[1]["metrics"],
        "quality": evaluation.payloads[3],
        "reconciliations": evaluation.payloads[2]["results"],
        "stage_hashes": {n: s.output_hash for n, s in stages.items()},
    }
    payload = canonicalize(payload)
    stages["checkpoint"] = SimpleNamespace(
        run_id=run_id,
        status="completed",
        input_hash=request.fingerprint,
        output_hash=semantic_hash(payload),
        payload=payload,
    )
    sources = [
        SimpleNamespace(
            id=b.document.source_id,
            filename=b.document.filename,
            content_hash=b.document.content_hash,
            parser_version=b.document.parser_version,
            language_metadata=[x.value for x in b.document.languages],
        )
        for b in request.sources
    ]
    checkpoint = verify_checkpoint(run, stages)
    return run, stages, sources, build_context(run, checkpoint, stages, sources)
