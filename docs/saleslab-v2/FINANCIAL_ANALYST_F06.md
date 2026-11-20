# Internal financial analyst (F-06)

This slice connects a persisted F-01–F-05 financial checkpoint to an async,
structured analyst. It creates an **unaudited draft** and advances the existing
run to `auditing`. Independent F-07 review and F-08 report compilation remain
separate stages. F-07 is now connected as described in `FINANCIAL_AUDIT_F07.md`;
F-08 remains the next slice. This analyst does not publish a report.

## Internal entry point

```python
from anthropic import AsyncAnthropic
from saleslab_v2.analyst.provider import AnthropicFinancialModel
from saleslab_v2.analyst.service import FinancialAnalyst

# Use the application's configured credential source. Model selection is explicit.
async with AsyncAnthropic() as client:
    model = AnthropicFinancialModel(client, model="claude-sonnet-4-6")
    async with session_factory.begin() as session:
        result = await FinancialAnalyst(session, settings, model).execute(
            workspace_id=authenticated_workspace_id,
            user_id=authenticated_user_id,
            run_id=persisted_pipeline_run_id,
            attempt_key="initial",
        )
```

All four controls are required: `SALESLAB_V2_ENABLED=true`,
`SALESLAB_V2_PERSIST_STAGES=true`,
`SALESLAB_V2_FINANCIAL_ANALYST_ENABLED=true`, and workspace inclusion in
`SALESLAB_V2_INTERNAL_WORKSPACES`. The analyst flag defaults to false.
An existing owner/manager membership is required. The internal caller must
authenticate the actor; this service accepts a run ID, never a caller-supplied
checkpoint, score, evidence index, model output or capability matrix.

## Evidence and capability boundary

Before calling the model, the service verifies the checkpoint hash, all four
stage hashes and their chain, semantic request, run/source bindings and equality
between stage results and checkpoint. It checks live workspace/run-scoped source,
metric, trace, reconciliation, coverage, issue and capability records as well.
A stale stage cannot hide a changed gate.

Blocked/mapping-required checkpoints return quality information without a model
call or new stages. Ready checkpoints permit only explicitly requested and
`allowed` capabilities. This initial schema supports `summarize`, `calculate`
and `compare`, as observations of existing evidence. Conditional permissions do
not become allowed. Diagnosis, causal hypotheses, forecasts, impacts and
recommendations require their own validated methods and later contracts.

The context contains canonical values, periods, scopes, variants, source hashes
and locations, complete calculation traces, reconciliations, all nine gates and
deterministic missing-data requests. Chained metrics resolve through scoped IDs
to their original sources. Filenames and references are untrusted data, never
system instructions. Original source text remains in the normalization stage;
the model receives a smaller canonical evidence projection.

## Structured draft

The model must return one `submit_financial_analysis` tool response conforming to
a strict Pydantic/JSON schema (`extra=forbid`). It selects evidence IDs, factual
claim type, capability, title and statement template. Statements cite values via
`{{evidence_id}}` placeholders. Validation rejects numeric literals, unknown or
duplicate references, invented fields, unsupported claims, mismatched gates,
mixed periods/scopes, duplicate findings and incorrect language identifiers.

The service binds each placeholder to the original canonical value, unit,
currency, period and provenance and assigns scoped finding/decision IDs.
Consumers must render the structured bindings; templates are not final prose.
No final formatter or ReportResult is introduced. Confidence/materiality scores
stay null because no scoring formula has been specified.

Report language comes from the run (`pt`, `en`, `es`, `fr`); source language and
parser locale remain independent. Language identifiers are validated; factual
truth, qualitative wording and linguistic quality still require F-07 review.
Binding numbers and evidence does not replace that independent review.

The draft includes data confidence, financial snapshot, reconciliations,
findings, missing-data requests, source index and provider/prompt/schema
configuration. Findings are `draft`, with `causal_status=observed` and
`requires_independent_audit=true`. Forecast/recommendation arrays are empty.
An audit approval or completed report is never fabricated.

## Transactions, replay and failure

A PostgreSQL run row lock serializes invocations. Two additional existing stage
rows preserve exact input/context/configuration and validated output or sanitized
structured failure. No migration is needed. The semantic attempt hash includes
the checkpoint/evidence, language, provider configuration and explicit attempt
key. A committed attempt replays a copy without another provider call.
Configuration changes under the same key conflict. A new attempt is allowed only
while the run is `calculating`. Success advances
`calculating → analyzing → auditing` and leaves `completed_at` unset.

The original deterministic checkpoint remains immutable and replayable after
continuation. Its recorded `calculating` status describes that checkpoint, not
the current live run.

Invalid output, provider errors, refusal, truncation and non-schema responses
fail closed: persist a sanitized failed attempt and leave the run `calculating`.
Repeating its key returns that failure; a new key requests another attempt. SDK
retries are disabled, timeout is bounded (default 60 seconds), output tokens are
bounded (default 8192), and context is limited to 256 KB without silent truncation.

Unexpected code/persistence errors roll back the invocation savepoint, leaving
the deterministic checkpoint and session usable. The caller owns the outer
transaction. The provider is outside PostgreSQL's transaction: a crash, rollback
or failure to commit after its response may require another billed request on
retry. This synchronous internal slice does not claim exactly-once external
billing or durable queue execution.

## Verification and scope

Unit tests cover bindings, languages, gates, hashes, provenance and transport
responses. Real PostgreSQL integration covers the merged pipeline through the
persisted draft, exact/concurrent replay, blocked data, invalid outputs/retry,
permissions, current-table tampering, configuration conflicts and rollback/session
recovery. Tests inject a recording model; they do not send source data to a live
model or claim evaluation of real model prose.

Legacy HTTP routes, auth, quota charging, billing, frontend and deployment
configuration remain unchanged. The build-agents guidance is applied within the
established Python/Anthropic stack; no additional agent framework is introduced.
