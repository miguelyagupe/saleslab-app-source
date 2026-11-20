# F-09 — internal financial shadow evaluation

F-09 adds an internal, default-off evaluation runner around the F-01 → F-08
pipeline. It compares explicit golden expectations with V2 and a selected legacy
baseline using the same source-bound rows. It does not change the customer
endpoint, history, quotas, billing, or release routing.

## Enable and execute

All of these switches must be enabled, with the workspace explicitly allowlisted:

```text
SALESLAB_V2_ENABLED=true
SALESLAB_V2_PERSIST_STAGES=true
SALESLAB_V2_INTERNAL_WORKSPACES=<workspace UUID>
SALESLAB_V2_FINANCIAL_ANALYST_ENABLED=true
SALESLAB_V2_FINANCIAL_AUDIT_ENABLED=true
SALESLAB_V2_FINANCIAL_REPORT_ENABLED=true
SALESLAB_V2_FINANCIAL_SHADOW_ENABLED=true
```

An owner or manager supplies a typed `ShadowCase` and an explicit attempt key.
There is deliberately no public HTTP endpoint or automatic activation.
An application already importing legacy `main` can compose the service as follows:

```python
from decimal import Decimal

from saleslab_v2.pipeline.contracts import InputRef
from saleslab_v2.shadow.contracts import MetricExpectation, ShadowCase
from saleslab_v2.shadow.legacy import DeterministicLegacyRunner
from saleslab_v2.shadow.service import FinancialShadowRunner

# request is an existing FinancialRequest; references must identify original rows.
source_id = request.sources[0].document.source_id
case = ShadowCase(
    case_id="reviewed-margin-case",
    dataset_version="internal-reviewed-v1",
    request=request,
    legacy_bindings=(
        ("revenue_current", InputRef("revenue", source_id)),
        ("net_result", InputRef("net_income", source_id)),
    ),
    expected_v2_status="completed",
    expected_legacy_status="completed",
    metrics=(
        MetricExpectation(
            "margin", Decimal("10"), "percentage", None,
            "net_margin_pct", Decimal("0.05"),
        ),
    ),
    required_capabilities=(
        ("calculate", "allowed"), ("forecast", "blocked"),
        ("recommend_material", "blocked"),
    ),
    expected_audit_status="approved",
)
async with sessions.begin() as session:
    result = await FinancialShadowRunner(
        session, settings, analyst_model, independent_critic,
        DeterministicLegacyRunner(main.build_deterministic_analysis),
    ).execute(case, attempt_key="reviewed-margin-v1")
```

The example's 10% and 0.05 percentage-point tolerance belong to this example's
reviewed golden only. No default business thresholds or prices are invented.
Every successful case requires at least one exact metric expectation. Required
capabilities are explicit, including deliberately blocked capabilities. Negative
cases can assert mapping/blocking and required issue codes without any metrics.

## Two explicit legacy modes

`DeterministicLegacyRunner` calls the actual current legacy financial helper.
Its configuration records `mode=deterministic_baseline`, hashes of the helper and
its module, and the adapter version. It performs no LLM call and makes no claim
to evaluate the legacy narrative.

For a live model evaluation, inject an asynchronous provider client and an
explicit model:

```python
from saleslab_v2.shadow.legacy import LegacyFinancialModelRunner

legacy = LegacyFinancialModelRunner(
    main, async_anthropic_client, model=selected_legacy_model,
    max_tokens=6000, timeout_seconds=60,
)
```

This mode records `mode=legacy_model_shadow`, prompt/schema/module hashes and
provider configuration. Both adapters capture the helper's deterministic forecast
policy separately from the model report, so an omitted or invented model field
cannot hide a forecast-permission difference. It uses the current legacy financial schema, context,
deterministic helper and postprocessor, with shared original rows and projected
form fields. It does **not** exercise the customer upload, file extraction, HTTP,
payment or quota path. The legacy prompt remains Portuguese; the V2 report uses
the requested language. Narrative comparison across languages requires human
review. Transport retries are disabled and timeout/token/output bounds apply.

The projection accepts only valid, normalized, original monetary rows with the
declared concept and comparison basis. It refuses mixed currencies/scopes and
multiple periods for one legacy role. Scale conversion comes from normalization;
comma decimals avoid the legacy parser mistaking decimal dots for grouping.
Source rows with no equivalent legacy field remain in the shared evidence; no
legacy formula or missing metric is fabricated. Unrepresentable bindings fail
before model calls. Finite legacy floats are preserved as explicit representation
objects, never silently converted into approved V2 financial numbers.

## Stored comparison and replay

Each attempt creates a scoped parent run with purpose `internal-financial-shadow`
and four immutable stages: input, legacy capture, V2 capture and comparison.
A separate UUID-derived child key runs the full V2 workflow, including bounded
revision and report compilation only after approval. Original analysis runs are
not advanced. All writes share a savepoint; the caller owns the outer commit.
Concurrent identical attempts serialize and replay the same evidence.

The input snapshot binds the case/dataset, original source hashes, shared inputs,
legacy/analyst/critic configuration, output locale and child run. Reusing a key
with different inputs or configuration fails. Replay validates hashes, recomputes
the comparison, reloads current child evidence and revalidates report artifacts;
it never calls the providers again. Legacy failures are sanitized to a reason
code, without exception text. The known zero-revenue legacy error tracked in
issue #26 is captured as an expected legacy failure, not repaired by this slice.

`status=pass` means the declared automated expectations passed. Numeric checks
compare V2 exactly against the golden value/unit/currency. Optional legacy
display comparisons use the declared tolerance, separately against the golden.
Two implementations agreeing on an incorrect number cannot pass the V2 check.
Legacy disagreements, unsupported metrics and forecast-policy differences remain
visible and require review; legacy is never treated as the source of truth.

V2 duration covers deterministic pipeline execution plus analyst/audit/compiler
execution; it excludes legacy time and shadow-parent persistence. Legacy duration
covers its adapter call. Measurements use a monotonic clock and are preserved on
replay. Legacy model token usage is recorded when available. Current V2 adapters
do not expose usage, so V2 usage and monetary cost remain `null`. These measurements
are observations, not enforced service-level thresholds. Failed outer transactions
cannot undo provider charges; a retry after rollback can repeat external calls.

## Complete campaigns and release gate

`FinancialShadowCampaign(runner).execute(cases, dataset_version=...,
required_case_ids=..., campaign_key=...)` requires the exact declared nonempty
corpus once, one workspace/actor and a consistent model configuration. It sorts
cases, hashes a versioned manifest and derives stable case attempt keys. Missing,
duplicate, extra or mixed-version cases are rejected. Results expose every case
run ID and all measured differences. Use one outer transaction for an atomic
campaign; per-case evidence is persisted, not a separate campaign table.

The checked-in tests use synthetic cases and recording analyst/critic transports:
positive comparisons, numerical truth independent of legacy, four source
languages/three scales, missing mapping, failed reconciliation, zero revenue,
revision, concurrency, replay, tampering, access gates and rollback. They verify
the evaluation mechanism, not financial narrative quality from live models.

The full reviewed business corpus (including JM-FIN-001…006, liquidity/working
capital, conflicting currency/scale, sparse P&L and incomplete monthly closes),
live legacy/V2 runs, measured cost and human narrative adjudication remain the
release acceptance work. Results always say `semantic_quality=not_certified` and
`rollout_authorized=false`; passing synthetic checks cannot authorize migration.
F-10 (Month-End Closer) and the public ingestion/product integration remain
separate implementation work.

## Validation

```bash
python -m pytest tests/test_saleslab_v2_shadow.py -q
TEST_DATABASE_URL=postgresql://...@127.0.0.1/..._test \
    python -m pytest tests/test_saleslab_v2_shadow_postgres.py -q
```

Database tests require disposable local PostgreSQL 16 and recreate its schema.
The existing GitHub gate executes the entire suite with PostgreSQL 16 and treats
skipped tests as failures. No new tables, migrations or dependencies are needed.
