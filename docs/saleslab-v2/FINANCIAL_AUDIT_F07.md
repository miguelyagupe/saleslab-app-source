# Financial statement audit and independent critic (F-07)

This internal slice consumes a persisted F-06 draft and produces a versioned
`AuditResult`. It re-executes the financial pipeline, then obtains a separately
configured, stateless semantic critique. The server computes approval, warning,
revision or blocking. Neither component rewrites a draft or publishes a report.

## Entry points

```python
from anthropic import AsyncAnthropic
from saleslab_v2.audit.provider import AnthropicFinancialCritic
from saleslab_v2.audit.service import FinancialAuditor
from saleslab_v2.audit.workflow import FinancialReviewWorkflow

async with AsyncAnthropic() as client:
    critic = AnthropicFinancialCritic(client, model="claude-sonnet-4-6")
    async with session_factory.begin() as session:
        result = await FinancialAuditor(session, settings, critic).execute(
            workspace_id=authenticated_workspace_id,
            user_id=authenticated_user_id, run_id=persisted_run_id,
            analyst_attempt_key="initial", attempt_key="initial",
        )
```

Execution requires V2 enabled/persist flags, workspace allowlist, owner/manager
membership and `SALESLAB_V2_FINANCIAL_AUDIT_ENABLED=true` (default false).
The internal caller authenticates the actor. Auditing an existing draft does not
require enabling new analyst calls; revisions require both analyst and audit
flags. No public endpoint or automatic activation is introduced.

The critic is separately injected with `role=independent_critic`, its own system
contract and versioned prompt/schema hashes. It may use the same model/provider;
role separation is not a claim of statistical independence between models.
No clients or credentials are constructed at import time.

## Verification and verdicts

Shared read-only evidence verification lives in `analyst/persistence.py`. F-06
and F-07 verify the checkpoint, stage chain, scoped source/metric/trace records,
reconciliations, coverage, issues and live capability decisions.

F-07 reconstructs the typed request from the normalization stage, checks its
fingerprint and re-executes F-02–F-05 with the original run timestamp. Every stage
must equal its stored result: merely rehashing an incorrect formula cannot pass.
The complete F-06 input/output contract is rechecked and findings are rebound to
their original values, units, periods, sources, traces and permissions.
Deterministic failures block without a model call; the critic cannot override them.

The critic must cover every finding exactly once in six families: claim support,
causality, counterevidence, scope, language and recommendation. Schemas reject
missing/duplicate checks, unknown finding IDs, extra fields and a model-supplied
overall approval. Revision/block judgments require actionable remediation.

The critic checks meaning: reported growth is not organic growth; a lease movement
does not prove expansion; aggregated costs are not logistics/waste; percentages
must preserve denominators and metric variants. Missing/contradictory evidence
cannot become certainty. The factual analyst cannot smuggle forecasts, hedging
or material commitments into prose. All source text and draft prose is untrusted
data, never instructions to the critic.

Overall precedence is `blocked > revision_required > approved_with_warnings >
approved`. The result binds the exact draft stage/hash and checkpoint, check IDs,
approved/rejected finding IDs, required revisions, configuration, timestamp,
revision count and retryability. Two existing stage rows preserve audit input and
output; no new tables or migration are needed.

| Outcome | Live run state | Effect |
| --- | --- | --- |
| approved / approved_with_warnings | compiling | Exact draft eligible for the future compiler. |
| revision_required | analyzing | Audit-authorized revision may be generated. |
| blocked | blocked | No publication; corrected run or human review required. |
| unavailable/invalid critic response | auditing | No approval; explicit retry key required. |

The original draft remains unchanged, `completed_at` stays unset and
`report_published=false`. F-08 now consumes this exact approval, preserves warnings
and enforces actual web/PDF parity as described in `FINANCIAL_REPORT_F08.md`.
The immutable F-07 snapshot itself does not claim report parity or publication.

## Controlled revisions

`FinancialReviewWorkflow(session, settings, analyst_model, critic).execute(...)`
accepts the same workspace/actor/run IDs plus `analyst_attempt_key` and
`review_key`. It audits, revises when authorized, and audits the new draft afresh.
At most **two revision attempts per run**, including committed failures, are
allowed. Continued revision requests at the limit become blocking.

Lower-level revision is explicit:
`FinancialAnalyst.execute(..., attempt_key="revision-v1", revision_of=audit_uuid)`.
Feedback/previous findings come from the stored audit and draft, never caller
text. Approved, blocked, foreign or superseded authority cannot start a revision.
Revision inputs bind parent IDs, hashes, feedback and ordinal. Prior stages stay
immutable. Repeating an attempt returns its stored result without model calls.
Ordinals and budget-exhaustion flags are recomputed from ordered persisted attempt
history on both audit and replay; consistently rehashing forged counters does not
authorize extra revisions. Later attempts do not invalidate an earlier replay.

An unavailable critic leaves the run awaiting audit; retry the current draft with
a new audit key. An unavailable reviser leaves it awaiting revision; a new analyst
key may retry under the same parent audit within the shared revision budget.

## Transactions and limits

A run row lock serializes audit/revision actions. Attempt hashes bind evidence,
draft, deterministic checks, configuration and keys. Changed inputs/configuration
or missing/corrupt input audit records fail closed. Workflow replay walks the same
immutable attempts without changing the current run state.

Each service uses a savepoint; unexpected persistence errors roll back its writes
and leave the session usable. The caller owns the outer transaction. Provider
calls are external: a crash/rollback after a response can cause another billed
call on retry. This internal slice does not promise durable jobs or exactly-once
provider billing. Context (384 KB), token budget and timeout are bounded; SDK
retries are disabled. Refusal, truncation and malformed output never approve, and
provider errors/secrets are not persisted.

Semantic critique is model judgment, not a proof of narrative truth or language
quality. Tests inject recording models to verify contracts/control flow; they do
not certify a live model's prose. Evaluation on the financial golden/adversarial
corpus remains necessary before product rollout.

Unit tests cover four languages, three scales, growth traces, altered formulas and
bindings, critic coverage, precedence, budget and transport failures. Real
PostgreSQL tests cover the whole pipeline, concurrent replay, approval/warnings/
blocking, revision/reaudit, failed-revision budgets, permissions, tampering,
immutable history and rollback/session recovery.

Legacy endpoints, auth, quotas, billing, frontend and deployment configuration
are unchanged. The build-agents guidance is applied within the established
Python/Anthropic stack; no additional agent framework is introduced.

## Critic comparison ordering (context v3)

The published JM pilot exposed a warning that reversed the CFO comparison:
2024 EUR 1,654,000,000 is below 2023 EUR 2,025,000,000. Context v3 supplies
exact decimal ordering, explicitly current relative to earlier comparative.
Pairs require unique normalized, issue-free sources with matching concept,
unit, currency, scope, dimensions, granularity and fiscal year-end month.
Overlapping periods and ambiguous pairs are omitted. Source values, periods,
document IDs, hashes and references accompany the ordering. No subtraction,
percentage, derived margin, forecast or capability authorization is added.

The prompt tells the critic to prefer a concrete limitation over unnecessary
directional comparisons in warnings. Exact ordering is deterministic; adherence
of free-form model prose is not certified by these tests. Existing published
reports remain immutable and are not repaired retroactively. Context v2 and
unversioned audits retain their original input shapes on replay.
