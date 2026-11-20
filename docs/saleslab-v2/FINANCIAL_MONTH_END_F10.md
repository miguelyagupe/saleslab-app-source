# F-10 — internal Month-End Closer

F-10 prepares a monthly close checklist from an existing, scoped F-01 → F-05
pipeline run. It distinguishes missing/invalid close data from reported movement
and material variations. It never attests an accounting close, creates journal
entries, modifies an ERP, or publishes a customer report. No LLM is involved.

## Compose the service

Enable `SALESLAB_V2_ENABLED`, `SALESLAB_V2_PERSIST_STAGES` and the new default-off
`SALESLAB_V2_FINANCIAL_MONTH_END_ENABLED` flag, with the workspace in
`SALESLAB_V2_INTERNAL_WORKSPACES`. The actor must be an owner or manager. The
analyst/audit/report/shadow flags are independent: preparing a checklist must
also work for blocked or mapping-required financial inputs.

```python
from datetime import date
from decimal import Decimal

from saleslab_v2.domain.value_objects import Period
from saleslab_v2.month_end.contracts import (
    ClosePolicy, ComparisonPlan, MonthEndRequest, TaskConfirmation,
)
from saleslab_v2.month_end.service import FinancialMonthEndCloser
from saleslab_v2.pipeline.contracts import InputRef

policy = ClosePolicy(
    policy_id="reviewed-monthly-policy", version="1", currency="EUR",
    scope="consolidated", dimensions=(), fiscal_year_start_month=1,
    required_statements=("income_statement", "balance_sheet", "cash_flow_statement"),
    required_reconciliations=("REC-BS-001",),
    required_tasks=("review_source_documents",),
    required_comparisons=("month_on_month", "month_year_on_year", "ytd_year_on_year"),
    absolute_variation_threshold=approved_absolute_threshold,  # Decimal
    percentage_variation_threshold=approved_percentage_threshold,  # Decimal
    materiality_basis="Approved internal close review policy",
)
request = MonthEndRequest(
    workspace_id=workspace_id, user_id=user_id, pipeline_run_id=pipeline_run_id,
    period=Period(date(2026, 10, 1), date(2026, 10, 31),
                  granularity="month", comparison_basis="current"),
    policy=policy,
    comparisons=(ComparisonPlan(
        "revenue-mom", InputRef("revenue_oct", source_id),
        InputRef("revenue_sep", source_id), "month_on_month",
    ),),
    confirmations=(TaskConfirmation(
        "review_source_documents", "complete", (InputRef("revenue_oct", source_id),),
    ),),
)
async with sessions.begin() as session:
    result = await FinancialMonthEndCloser(session, settings).execute(
        request, attempt_key="october-close-review-v1",
    )
```

The example intentionally configures only one of the three required comparisons:
the result requests the missing year-on-year and YTD evidence. Supply explicit
plans for each required comparison to resolve those gaps. Every reference must
identify an original source row in the selected pipeline run. A source outside
that run/workspace is rejected. An absent or incompatible row within the run
becomes a missing-data request rather than a guessed value.
The `required:` prefix is reserved for generated checklist identifiers and cannot
be used in configured comparison/movement keys.

Policy requirements and materiality thresholds are explicit, bounded and
versioned. No default amount, percentage, business calendar or required task is
chosen by a model. Thresholds must be finite positive Decimals. A variation is
material when its exact absolute delta **or** absolute percentage meets its
respective threshold; the percentage threshold is in percentage points. Threshold
equality requires review. There is no favorable/unfavorable interpretation.

## Evidence and comparisons

The checklist checks current-month statement presence within the declared
currency/scope/dimensions, required scoped reconciliation results, persisted data
quality issues, required comparison coverage, explicit movement expectations and
user task confirmations. Statement presence is not a certificate of complete
accounting records. The selected policy determines the checklist's scope; a
policy requiring only P&L and balance sheet does not imply cash-flow completeness.
Required reconciliations in `pass` or `rounding_pass` satisfy their checklist item;
`warning` requires review, while `critical`/`blocking` leave the close incomplete.

Comparisons support month-on-month, monthly year-on-year and YTD year-on-year.
The current period must match the target month/cutoff. Month-on-month requires
adjacent full calendar months, including December → January. Year-on-year requires
the same month/span and calendar one year earlier, including leap February. YTD
requires the declared fiscal start and exact monthly cutoff; no YTD is invented
by summing available months. Budget/forecast observations are not comparatives.
Currency, scope, dimensions, concept, statement type and monetary semantics must
match. Missing/conflicting data becomes a pending checklist item.

New immutable metric definitions in `month_end/metrics.py` use the existing
Decimal `MetricEngine` for monetary deltas and percentage changes. Explicit
`previous_month` and `prior_year` period rules extend that engine without changing
the original standard metric behavior. The canonical expression records concept
and period rule. Every computed value has a persisted, scoped MetricObservation
and CalculationTrace; source values retain their original row references.
Materiality compares exact deltas and rational cross-products before division.
The MetricEngine's recorded 100-significant-digit division policy still applies
to the displayed percentage; rounding it to a threshold does not by itself make
the underlying ratio material. A zero prior value preserves the
monetary delta, leaves the percentage null with a reason, and requires review.

F-10 requires the source `calculate` capability to be allowed for arithmetic,
then verifies comparability per close calculation using these versioned rules.
It preserves the original F-05 capability matrix in the result. It does not
enable a blocked report-level `compare`, forecast or recommendation capability:
these additional period proofs authorize only deterministic close preparation.
Blocked/mapping-required runs still return their gaps, without calculated changes.

`MovementExpectation` supports `nonzero_value` and
`changed_from_prior_month`. These are explicit expectations about a reported value
or net balance change, not proof of underlying transaction activity. Equal
opening/closing balances can contain offsetting transactions. Without declared
expectations, `movement_coverage=no_expectations_declared` makes that limit visible.
The service never invents expected turnover for an account.

Task confirmations require evidence from the target month and identify the
authorized submitting user. Their status is `confirmed_by_user`, distinct from
automatically `satisfied` items. The service verifies scope/reference validity;
it does not independently verify the human's accounting task. New evidence or
confirmations require a new attempt key; prior snapshots are immutable.

## Output, replay and isolation

`preparation_readiness` is:

- `incomplete` when any required item is pending;
- `review_required` when data is present but a material variation, undefined
  comparison percentage or reconciliation warning needs review;
- `ready_for_review` when all declared requirements are satisfied/confirmed.

None means “closed”. A decline alone cannot make a complete dataset incomplete.
The result always sets `performance_assessment=not_assessed`,
`accounting_close_attested=false`, `accounting_entries_created=false` and
`customer_publication_authorized=false`. Checklist labels support pt/en/es/fr;
financial values remain machine-readable canonical decimal strings.

An isolated `internal-financial-month-end` parent run stores input/result stages
and its calculations. Completing this preparation run never completes the source
financial run or changes legacy history, quota, billing or accounting records.
The caller owns the transaction. A savepoint rolls back all partial parent,
stage, metric and trace writes. Concurrent equal attempts serialize and replay.

Replay reloads and validates the source pipeline stages and live financial
records, checks the exact request/policy/language/evidence binding, recomputes the
deterministic result and validates the full persisted metric/trace set. It rejects
rehashed stage tampering, altered values or missing calculations without repairing
them. Adding analyst/audit/report stages to the source run does not change its
immutable deterministic checkpoint; changing that checkpoint invalidates replay.

## Verification and next acceptance work

The existing PostgreSQL 16 GitHub gate runs the full suite with skips prohibited.
F-10 tests include civil/fiscal YTD, calendar boundaries, exact materiality,
negative performance versus incomplete close, missing movements, blocked gates,
four languages, user evidence, concurrency, access scope, replay/tampering and
transaction rollback. Existing financial metric tests guard standard behavior.
No new dependency, table or migration is needed.

This slice supplies an internal service and checklist contract. Real monthly
business datasets, accountant review of the chosen policies, customer file
ingestion and the customer-facing checklist UI remain product acceptance and
integration work. It is not a PDF/report publication route or an ERP integration.

The product upload adapter now provides an opt-in, source-bound preparation
preview and checklist UI under the same default-off month-end flag. Its explicit
policy, original-row bindings and JSON evidence export are documented in
`FINANCIAL_UPLOAD_PRODUCT.md`. It is intentionally non-persisted and does not
replace this internal durable service or certify real-world business acceptance.


## Reconciliation discovery in the upload product

When the existing monthly-preview capability is enabled, authenticated
`GET /financial-v2/capabilities` also returns
`month_end_reconciliation_catalog` with schema
`financial-month-end-reconciliation-catalog-v1`. This read-only, no-store
metadata enumerates the 14 distinct IDs in the financial registry, their
supported versions and approved Portuguese, English, Spanish and French labels.
Future registered IDs use a visible canonical-ID label fallback. There are no
preselected requirements: users explicitly choose the IDs required by their
policy, up to the advertised maximum (currently 20). Engine versions remain
pinned by the existing source checkpoint; the catalog does not choose a version
or change the policy/snapshot contract.

Catalog membership is not execution evidence. The existing evaluator still
requires actual reconciliation results matching the target month, current
basis, currency and scope. For example, a working-capital opening result from
the previous month remains pending for the current monthly review. A selectable
rule with missing evidence or a material source difference cannot make the
preparation ready for review.

The product can refresh the catalog without changing financial inputs. Refresh
clears the current preparation and preserves selected requirements. An ID that
is no longer advertised remains visibly checked and blocks preparation until
explicitly reviewed and deselected. A malformed or unavailable catalog also
blocks preparation. Only an older backend that omits the field uses the original
six-rule fallback; previously chosen newer IDs still require explicit review.
Labels follow the report language and are rendered as text.

Discovery adds no migrations, model calls, quota usage, paid report history or
production flag activation. Catalog metadata is excluded from saved preparations
and semantic hashes; existing snapshots reopen and replay with their original
policy and result semantics.
