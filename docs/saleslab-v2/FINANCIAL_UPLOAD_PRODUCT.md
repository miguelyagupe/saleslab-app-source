# Validated financial upload: initial product integration

The Financial module can now use the persisted F-01–F-08 chain through an authenticated upload, deterministic preview, explicit mapping, durable job, approved HTML/PDF report and existing product history. This is a restricted pilot; enabling it requires server configuration. Existing modules and workspaces retain their current flow.

## Pilot configuration

All of these flags must be `true`: `SALESLAB_V2_ENABLED`, `SALESLAB_V2_PERSIST_STAGES`, `SALESLAB_V2_FINANCIAL_ANALYST_ENABLED`, `SALESLAB_V2_FINANCIAL_AUDIT_ENABLED`, `SALESLAB_V2_FINANCIAL_REPORT_ENABLED`, `SALESLAB_V2_FINANCIAL_PRODUCT_ENABLED`.

Add the workspace UUID to `SALESLAB_V2_INTERNAL_WORKSPACES`. Its authenticated actor must be an owner or manager. Set `SALESLAB_V2_ANALYST_MODEL`, `SALESLAB_V2_CRITIC_MODEL` and the existing `ANTHROPIC_API_KEY`; model identifiers are explicit deployment choices. No flags, credentials or production models are enabled by this change. Shadow and month-end flags are independent and not required by this upload path.

The application starts a database-backed upload worker when the product flag is enabled at startup. Restart after changing worker activation. Each process may run a worker: PostgreSQL row locks and `SKIP LOCKED` coordinate claims. Missing model configuration or database outages pause consumption; queued work remains durable. Turning off uploads preserves report reads while the underlying analyst/audit/report flags and workspace authorization remain enabled.

## Accepted file contract

One UTF-8 CSV (optional BOM; semicolon, comma or tab) or one-sheet XLSX, at most 5 MiB and 500 data rows. XLSX formulas, additional worksheets, extra columns, XML entity declarations and oversized ZIP expansion are rejected. PDFs, images, arbitrary workbook layouts and multiple files require a separate extraction/validation phase and are not accepted here.

The eight headers, in order, are:

```csv
line;amount;period;statement_type;scope;currency;scale;comparison_basis
receita;1000;2026;income_statement;consolidated;EUR;raw;current
resultado líquido;100;2026;income_statement;consolidated;EUR;raw;current
total do ativo;100;2026;balance_sheet;consolidated;EUR;raw;current
total do passivo;60;2026;balance_sheet;consolidated;EUR;raw;current
capital próprio;40;2026;balance_sheet;consolidated;EUR;raw;current
```

The downloadable template uses Portuguese labels and integer values. Select Portuguese as the source language for that template. File number locale and report language are separate choices. Metadata values follow the existing normalizer's controlled vocabulary: `income_statement`, `balance_sheet` or `cash_flow_statement`; `consolidated` scope only; explicit currency and scale; `current` or `comparative` basis. Unsupported statement types and comparison bases return validation errors. Segment/dimension ingestion is intentionally unavailable in this initial contract.

Preview retains original labels and locations and offers only canonical mappings compatible with each row's statement type. A changed file, filename, mapping, target period, language, locale or tolerance requires a new preview. Unsupported normalization, ambiguous operands, incompatible currencies and invalid denominators are handled by existing deterministic gates. No provider runs during preview and preview consumes no quota.

The initial plan supports revenue, net margin, operating cash flow and structurally comparable prior-year revenue growth when the required operands exist. Balance-sheet identity is tested when its three operands exist. Default tolerance is exact equality; an explicit nonnegative absolute rounding allowance uses base currency units, not a guessed business materiality percentage. Reports disclose the evidence available; this integration does not claim complete financial-statement coverage, close attestation or customer acceptance on real datasets. F-09 shadow and F-10 close workflows are not automatically invoked.

## Persistence, quota and failures

`POST /financial-v2/preview` validates without writes. `POST /financial-v2/generate` requires the preview hash plus a UUID attempt key and returns HTTP 202 with a job ID. It stores the original file (base64), hash, original row labels, mappings, authenticated actor and pinned provider configuration using existing V2 runs/stages. This includes sensitive customer data in the existing database and backup lifecycle; no new expiry/delete policy is introduced. Treat pilot access and retention accordingly.

`GET /financial-v2/jobs/{id}` resumes work after page refresh. The public pending state means queued or processing. A job lock remains held until its result commits. Cancellation or process/database failure rolls back work, leaving the committed job pending for another worker. Provider calls can repeat after a crash; this does not guarantee exactly-once external provider billing. A provider exception or rejected/invalid report produces a sanitized terminal failure and permits a new attempt.

A workspace has at most three pending uploads and twenty new upload attempts per rolling hour. The hourly limit persists across application instances and restarts and counts failed/rejected jobs as well as successes. Duplicate attempt keys reuse the same job only with identical inputs and can be resumed after reaching either limit. Product publication uses an exclusive transaction advisory lock per workspace and rechecks current quota before provider calls. Approved report persistence, existing `Analysis` history, publication receipt and quota consumption share one transaction. Failed or rejected work creates no paid history and consumes no product quota. A replay consumes no additional quota. The existing atomic quota update also prevents overruns when legacy analyses run concurrently; an exhausted quota rolls back publication. Database driver and pool timeouts propagate through the worker transaction so the committed job remains pending for recovery.

`GET /financial-v2/reports/{run_id}/html` and `/pdf` authenticate workspace membership and replay the compiler's evidence checks before returning stored approved artifacts. Reads do not call models or consume quota. The UI embeds HTML in a sandboxed iframe and downloads the compiler's PDF. `GET /financial-v2/history` lists only this workspace's financial V2 reports for owners/managers on all plans, in pages of 50 with a scoped UUID cursor. The Financial module exposes these saved reports after refresh and after upload activation is disabled. Team dashboard history entries also reopen this same report; legacy KPI evolution charts omit V2 receipts rather than infer values from incompatible schemas.

## Validation

Unit tests cover locale/language replay, explicit mappings, preview binding, period selection, normalization failures, rounding allowances, CSV/XLSX bounds and chunked HTTP limits. PostgreSQL tests cover authenticated upload through report download, concurrent duplicates/workers, the last quota slot, failed audit, transactional rollback, worker cancellation/restart, authorization, flag changes and tampered report artifacts. Tests use recording providers and disposable PostgreSQL 16; they do not establish live-model quality or customer-data acceptance.

## Calculated preview metrics

Preview now returns `calculated_metrics` for planned steps, derived solely from
the existing deterministic evaluation. Successful rows include exact decimal
strings, report-language formatted display values, periods, source references
and calculation trace IDs. Failed or mapping-blocked steps carry no value or
trace ID and expose their sanitized reason code. This is a preliminary calculation
view, not an audit or publication. A failed reconciliation still blocks generation
even if individual metric rows calculated successfully.

The UI renders every metric field as text and clears the calculation view when
any input or mapping changes or a validation request fails. It accepts previews
from an older backend without the new optional field. No planning, preview hash,
source contract, quota, provider calls or report persistence semantics change.

## Validation summary before generation

The optional `validation_summary` preview field projects the existing coverage
level, capability decisions and planned reconciliation results. It adds no score,
calculation, permission or persistence. Capability status, reason codes and exact
missing requirement codes are preserved; readable labels explain known gaps.
Preview-only labels are separate from the immutable publication renderer catalog.

Reconciliation rows expose original expected/observed amounts, absolute deltas,
source references and result hashes, with display amounts formatted in the report
language. A planned but unexecuted check is `not_evaluated` with null values, not
an assumed pass. An empty list means no identity was verified from these inputs.
The UI distinguishes exact equality, accepted rounding tolerance and blocking
results. Coverage is explicitly limited to evaluated capabilities; it does not
certify full financial statements or publication approval. The existing `ready`
field remains the generation gate. Input changes and validation failures clear
both metric and validation panels. Older backends without the optional field keep
the panel hidden. The script fingerprint in the HTML is updated with this release.

## Inspecting a preview calculation

Each successful calculated metric now optionally includes `calculation_evidence`,
projected from its actual engine trace. It contains the canonical expression,
definition version, exact output, rounding and division policies, trace hash and
ordered operands. Each operand retains its exact decimal value, period, unit,
currency, source references and original imported rows. Mapped labels never replace
the original file labels in this evidence. Original amounts and scales are shown
separately from the expanded base-currency values actually used in the formula.
Failed or unexecuted metrics carry null evidence.

The UI exposes this through native expandable **Conferir cálculo** sections. It
renders all source text as text, explains display rounding and finite division
precision, and preserves long decimal strings without JavaScript arithmetic.
Evidence clears with the existing preview invalidation. Older backends lacking
the optional field continue to show the metric table. This is a read-only preview
projection; formulas, normalization, preview hashes, publication replay, generation
gates, provider calls, quota and persistence are unchanged.

## Versioned upload planning and EBIT margin

`planning_version` is an optional choice, accepting `"1"` and `"2"`. Absence
means the original four-metric planner. Version 1 omits the field from the bound
choice payload, preserving the original preview hashes and queue input hashes.
Previously queued choices without the field therefore resume with version 1.
New version 2 choices retain the version in both the preview binding and durable
queue input; workers execute that selected plan, rather than a changing default.
Unknown versions are rejected. No schema migration is needed.

Version 2 additionally plans `ebit_margin_pct@1` when exactly one normalized,
issue-free EBIT and one revenue observation exist for the selected current period.
It uses the existing definition `confirmed_ebit/revenue*100`, operand adapter,
rounding policy and all existing currency, period, denominator and sufficiency
checks. Negative EBIT remains negative; operating profit and unverified EBITDA
are not silently treated as confirmed EBIT. Duplicate candidates are not selected.
No missing EBIT is fabricated and no benchmark or profitability judgement is added.
The preview shows localized labels and the same source-bound calculation details;
approved reports preserve the calculation and trace through the existing compiler.

Capabilities now advertise `planning_versions`. The frontend opts into version 2
only when advertised, and omits the new choice against an older backend. Existing
API clients continue to use version 1 unless they explicitly select version 2.
The upload schema, immutable metric definitions, terminology and publication
renderers are unchanged. Before downgrading to a binary that predates versioned
choices, finish version 2 queued jobs with a compatible worker; old binaries
cannot parse new version 2 choices. Saved report reads retain their existing path.

## Planning version 3: basic free cash flow from signed source cash flows

Version 3 retains the version 2 EBIT plan and adds `free_cash_flow@2` when a
single normalized CFO and a single normalized purchases-of-fixed-assets cash flow
exist for the selected current period. The new immutable metric version computes
`cfo + reported_capex_cash_outflow_negative`: the source purchase cash flow must
be nonpositive, including zero. The metric engine enforces that operand constraint
even for direct callers; the existing normalizer independently requires confirmation
for a positive purchase amount. No absolute-value conversion or guessed sign is
performed. A negative CFO remains negative.

This is basic free cash flow under the displayed CFO/fixed-assets definition,
not proof of complete capex coverage, distributable cash or a valuation measure.
Missing capex is not zero; missing/duplicate operands do not create a calculation.
Period/currency/scope checks, exact Decimal arithmetic and source traces remain
mandatory. Preview labels identify the fixed-assets basis in all four languages,
and expandable evidence shows original negative amounts, original scales and
normalized operands separately. The existing `free_cash_flow@1` positive-outflow
definition remains unchanged and still resolves by default for old callers.

The frontend selects version 3 only when advertised, otherwise version 2 or the
legacy path. Queue choices pin all three plans. Independent pre-change hash
regressions protect versions 1 and 2; neither starts planning this new calculation.
The same rollback rule applies: pending version 3 jobs require a compatible worker
before returning to an older binary. No new migration, provider call or quota
charge is introduced by validation or report reads.

## Metric availability and concrete data gaps

The optional preview-only `metric_availability` list explains every metric
supported by the selected upload plan. It distinguishes actual calculated
results, planned-but-blocked calculations, missing target periods and unplanned
metrics. Unplanned metrics identify missing operands, source rows requiring
confirmation, multiple candidates and comparisons waiting for a unique current
revenue. Requirements use report-language labels and preserve actual source
references and normalization issue codes; unknown labels are not guessed into
specific concepts. The prior-revenue matcher is shared with the existing planner.

This list does not calculate, select new operands, promise comparability or alter
the existing `ready` gate. A planned calculation that fails currency, denominator
or period checks remains blocked, rather than being relabeled as missing data.
A calculated metric remains calculated when a separate reconciliation blocks
generation. Missing capex never becomes an assumed zero. Only the metrics of the
selected plan are considered; older plans are not asked for newly supported data.

The UI shows the unavailable metrics and their required data before generation.
It renders all labels and source references as text and clears the list with
other preview panels on input changes or validation failure. Missing optional
data from an older backend hides this panel. Preview bindings, queue choices,
report persistence/replay, permissions, quota and model calls are unchanged.

## Explicit accounting-parentheses convention

Uploads optionally accept strict JSON `accounting_parentheses`: `true` confirms
that parentheses indicate negative values, `false` confirms that parentheses do
not determine the sign, and omitted/null retains the existing ambiguous-sign
block. Written minus signs retain their meaning. The choice applies to the whole
file and is passed to the existing `SourceMetadata`/number parser; no source amount
or uploaded byte is rewritten. For example, a confirmed `(80)` fixed-asset cash
outflow contributes `-80` to the version 3 basic free-cash-flow calculation.
Explicit `false` does not bypass the nonpositive purchase-cash-flow constraint.

The choice is bound into the preview hash, source metadata and durable queue
choices. Changing it requires validation again. Omitted/null is omitted from the
bound choice serialization so old preview/queue hashes and jobs remain unchanged;
explicit `false` is preserved rather than treated as absent. The public sample
keeps its previous choices shape for existing operator clients.

The frozen parser's sign operation can round source digits beyond its active
Decimal precision. Explicit new conventions therefore compare ordinary parsing
with parsing at sufficient precision for the bounded source cell and reject a
value that would lose digits. This guard does not alter legacy requests or the
immutable parser. No rounded source value is accepted under the explicit choice.

Capabilities advertise `accounting_parentheses_supported`. The UI shows and sends
the option only when supported. It invalidates the preview on changes and resets
confirmation for a different file, the public sample or a new analysis. Source
language, number locale and report language remain separate settings. No new
migration, planning version, model call or validation quota charge is introduced.
As with new planning versions, jobs containing an explicit new choice require a
compatible worker before downgrading to a binary that predates this option.

## Row-level source review and normalization guidance

Each preview row optionally includes `original_input`, the imported row fields
and CSV/XLSX location before explicit concept mapping, and `validation_guidance`,
a list keyed to its actual normalization issue codes and fields. Guidance uses
the report language (pt/en/es/fr), independently of the source language and number
locale. It explains parentheses/sign conflicts, number formatting, missing or
conflicting currency/scale, period ambiguity, concept mapping, duplicate candidates
and unit/ratio requirements. Unknown future issue codes retain their code/field
and get a localized generic instruction; issue details are not interpolated into
messages. No missing issue is inferred and no data repair is performed.

The frontend shows imported amounts, currency, scale, original period and row
location next to the normalized value, with expandable instructions for affected
rows. Labels, original values, guidance and codes are rendered as text. The row
table is in a keyboard-focusable scroll region. Older backends can omit both
fields and retain the previous presentation. These are read-only projections:
normalization, planning, ready gates, source bytes, preview/request bindings,
queue inputs, published reports, quota and model calls keep their existing behavior.

## Explicit fiscal year-end month

Uploads optionally accept strict JSON integer `fiscal_year_end_month` from 1 to
12. Omitted/null preserves the original behavior and is excluded from bound
choice serialization so existing preview hashes and queued requests do not
change. An explicit month is passed to every imported `FinancialRow` through
the existing fiscal-period normalizer and bound to the preview, request and
saved job. It does not rewrite the uploaded bytes or original period labels.
The public sample retains its previous choices shape.

The fiscal year label denotes the year in which the exercise ends: with month 6,
`FY 2026` means 2025-07-01 through 2026-06-30; `FY 2026 Q1` means July–September
2025 and `FY 2026 Q2 YTD` means July–December 2025. Existing pt/en/es/fr fiscal
markers use the same normalizer. Explicit calendar years/months keep their
existing semantics; confirming a fiscal month does not reinterpret every row
as fiscal. An unmarked quarter with fiscal context, a monthly YTD without a
clear fiscal basis, a missing YTD cutoff or mixed fiscal/calendar comparison
remains blocked. No fiscal calendar, date cutoff or comparability is inferred.
Fiscal shifts outside supported date boundaries are rejected as invalid uploads.

Capabilities advertise `fiscal_calendar_supported`. The frontend shows the
file-wide month choice only when supported and sends an integer only after an
explicit selection. Changes invalidate the preview; loading another file, the
public sample or a new analysis clears the confirmation. An older backend
receives no new choice. Row guidance explains the fiscal-calendar and ambiguous
quarter checks. Report dates and calculation evidence retain the resolved fiscal
period through the existing queue, normalization and report compiler.

There is no new metric plan, parser version, migration, live model call or
validation quota charge. Pending jobs with an explicit fiscal month require a
compatible worker before downgrading to a binary predating this choice. Legacy
requests and published report replay keep their existing behavior.

## Explicit source currency and scale confirmation

Uploads optionally accept `source_currency` (an exact supported monetary ISO code)
and `source_scale` (`raw`, `thousands`, `millions`). Both are strict nullable
choices with no guessed default. They populate the existing `SourceMetadata`
fields for the whole file, independently of source/report language and number
locale. The normalizer can resolve blank row metadata from these confirmations;
explicit row or embedded-value conflicts still block. No per-row override,
currency conversion, numeric sign change or source-cell rewriting is introduced.
For example, confirming EUR/thousands for blank currency/scale fields converts
the original amount `1000` into a normalized EUR `1000000`, with the original
blank fields and `1000` preserved as imported evidence.

The choices, source metadata and normalized values are bound to the existing
preview/request and durable queue. Changing either choice requires validation
again and cannot reuse a stale preview or attempt. Omitted/null fields are
excluded from bound serialization, preserving legacy hashes and queued inputs.
The public sample retains its previous choices shape. Monetary currency policy,
parser, metric plans, publication renderers and database schema are unchanged.

Capabilities advertise `source_metadata_supported` and the exact existing
`source_currencies` allowlist. The frontend shows the optional file-wide controls
only when supported, sends only explicit selections and resets them for another
file, the public sample or a new analysis. The UI states that confirmations must
apply to the whole file and cannot override conflicting source values. Blank or
older-backend choices are omitted. Imported fields remain separate from normalized
values in the row preview and calculation evidence. Validation performs no model
call and consumes no quota. Queued jobs with an explicit new choice require a
compatible worker before downgrading to a binary predating these fields; archived
report reads retain the existing path.

## Planning version 4: reported investing and financing cash flows

Plan 4 retains all plan 3 metrics and adds `cfi@1` and `cff@1`, immutable identity
definitions for reported signed investing and financing cash flow. Each requires
exactly one normalized, issue-free source observation for its own concept in the
selected current period. The existing terminology already recognizes both
concepts in pt/en/es/fr; no terminology, alias or parser change is required. The
operand contracts accept the actual `cfi`/`cff` identities under `reported_cfi`/
`reported_cff` roles and reject CFO, capex, revenue or the other cash-flow concept.
Positive, negative and explicit zero values retain their source meaning and exact
normalized scale. There is no absolute-value conversion or guessed outflow sign.

The read-only preview shows localized labels and the existing calculation/source
inspector, with original amounts, scales and references. The gap projection adds
these two metrics only for plan 4. Missing, duplicate, comparative-only or
unconfirmed candidates are not selected; missing values never become zero.
Identity metrics retain their own source currencies and periods; no cross-flow
sum or currency conversion is introduced. CFI is not substituted for capex or
free cash flow. CFF alone does not identify borrowing, dividends or their causes.
These entries do not prove a reconciled change in cash: opening/closing cash and
FX/other movements remain separate requirements for that reconciliation.

Capabilities advertise plan 4 through `planning_versions`. The frontend chooses
4 only when advertised, otherwise falls back to 3, 2 or the original omission.
The default API plan remains 1 and saved jobs pin their chosen plan. Plans 1–3
keep their metric selection and bindings, including an independently recorded
plan 3 hash with CFI/CFF source rows present. Old queued jobs are not upgraded.
New plan 4 calculations persist through the existing queue and report compiler,
without changing archived renderer versions or reads. Pending plan 4 jobs need a
compatible worker before rollback to a binary without this plan. No migration,
new upload format, live provider call or validation quota charge is introduced.

## Pinned normalization version 2: exact signed source values

`normalization_version` is independent of the metric planning version. The API
continues to default to `1`. Version 1 keeps the existing `locale-number-v2`
parser, context-sensitive sign behavior and explicit-convention precision guard.
Version 2 uses `locale-number-v3`, which delegates to the frozen grammar inside
an isolated Decimal context with enough precision for every source character,
unrestricted supported exponent bounds and no clamp. The bounded upload cell is
at most 2000 characters. This preserves the full coefficient when applying a
written minus or confirmed accounting parentheses, independently of the caller's
precision, exponent settings or rounding traps, without changing its context.
The old parser implementation remains unchanged.

All existing locale, grouping, currency, scale, percentage, missing-value and
sign-conflict checks still apply. Parentheses still require explicit confirmation;
positive purchase outflows remain blocked. There is no inferred sign, numeric
format, missing amount or financial fact. Version 2 observations identify
`financial-normalizer-v2`; source amount text and uploaded bytes remain unchanged.
Scaling uses the existing exact power-of-ten operation. Metric formulas and
existing division/rounding policies are unchanged: exact source parsing does not
claim unbounded-precision division or change display rounding.

The chosen version is pinned in upload choices and per-source `SourceMetadata`,
then carried through preview bindings, queued input, normalization evidence and
independent report replay. Version 1 is omitted from the bound choices and
`SourceBatch.payload` metadata, retaining the exact old serialized shape, hashes
and pending jobs. Old persisted requests lacking the field restore as version 1;
they are never upgraded. The public sample keeps its previous choices shape.
Capabilities expose `normalization_versions`; the frontend chooses 2 only when
advertised and otherwise omits the choice for the legacy path. No technical
version selector is added to the customer flow.

No database migration, terminology/metric plan change, published renderer change,
provider call or validation quota charge is introduced. Workers and report readers
must support normalization version 2 while its jobs/reports need processing or
replay. Before downgrading to a binary without version 2, preserve a compatible
worker/reader path for those records. Previously published version 1 reports keep
their existing interpretation and read path.

## Planning version 5: current ratio with pinned current classifications

Plan 5 retains plan 4 metrics and adds the existing `current_ratio@1` definition:
`current_assets / current_liabilities`, with the engine's existing ratio display
rounding and currency, period, scope and nonzero-denominator checks. It requires
one normalized current-assets row and one current-liabilities row in the selected
current period. Total assets/liabilities are never substituted; missing or duplicate
current classifications are never guessed or aggregated. There is no liquidity
benchmark, threshold or assertion that the ratio alone proves ability to meet
obligations. Cash availability, timing, maturities and other diagnostic requirements
retain their existing gates.

The plan pins `financial-terminology-v2`, an additive catalog containing all v1
entries unchanged plus explicit current-assets/current-liabilities balance-sheet
labels in pt/en/es/fr. It does not modify the original catalog or broaden generic
asset, liability, receivable or payable labels. Explicit user mappings to the new
concepts are allowed only in plan 5 and must match balance-sheet context. Source
labels, amounts, scales and references remain intact in imported evidence and the
ratio's existing calculation inspector.

`SourceMetadata.terminology_version` independently pins the selected catalog for
normalization and replay. Plan 5 selects version 2; plans 1–4 retain version 1.
The default v1 field is omitted from `SourceBatch.payload` metadata to preserve
old semantic request shapes and hashes. Old queued requests are not upgraded and
old stored requests lacking the field restore as v1. A pre-change plan 4 hash with
normalization v2 is pinned independently as a regression. Numerical normalization
versions remain separate from terminology selection.

The gap projection includes the current ratio only for plan 5, with localized
operand labels and actual missing, duplicate or confirmation statuses. Planned
ratios blocked by zero denominators or currency mismatch remain blocked. The UI
negotiates plan 5 only when advertised, otherwise falls back through 4/3/2/legacy.
No new customer-facing technical version control, upload column, formula,
migration or renderer version is introduced. Validation consumes no model call or
quota. Pending plan 5 jobs and report replay require compatible workers/readers
before a downgrade to binaries without the new plan/catalog; v1 report semantics
and existing authorization/quota paths remain unchanged.

For non-v1 catalogs, the independently built analyst/audit context also records
the actual terminology versions observed. The compiled report methodology names
the single catalog, or lists the catalogs for a mixed-source run, instead of
claiming v1 for every report. All-v1 contexts and report methodology keep their
previous exact shape; existing report golden/hash checks remain unchanged.


### Plan 6: explicit cash movement reconciliation

Capabilities now advertise planning version `6`. The UI prefers it and falls back
through 5, 4, 3, 2 and the original unversioned contract. The API default remains 1.
Plan 6 retains plan 5 metrics and pins source terminology to `3`; catalogs 1 and 2
are unchanged. Numeric normalization remains independently pinned to version 1 or 2.
The new `cash_fx_other@1` identity records the explicitly reported aggregate of
foreign-exchange effects and other cash movements. It is never a balancing residual.

`REC-CF-001@1` verifies opening cash + CFO + CFI + CFF + FX/other = closing cash,
using the existing reconciliation engine and its compatibility checks. Every operand
must have one unique, normalized, issue-free source row. Missing FX/other is never
zero; upload an explicit zero only when the source confirms it. Ambiguous or missing
components produce a preview gap, with no claim that the bridge was verified.
A planned failed bridge blocks generation through the existing quality gates.

Select the target flow period explicitly if the opening balance introduces another
current period. A unique CFO anchors the interval. Opening and closing cash use
`cash_and_equivalents` in `balance_sheet`, with the same period granularity and fiscal
calendar as the flows. Opening cash ends the day before the interval starts; closing
cash ends on the interval's last day. For an annual 2026 bridge, use cash rows labeled
2025 and 2026. Both have `comparison_basis=current` because both belong to this
analysis; a `comparative` row is never silently reclassified. Fiscal annual bridges
also require the explicit fiscal closing month and adjacent fiscal years.

Optional `cash_rounding_tolerance` is a strict bounded finite nonnegative decimal
string in base currency units; omitted/null means exact equality. It is separate
from balance-sheet `rounding_tolerance`. Only plan 6 uses it, and changing it changes
the preview/request contract. The UI clears it to zero on file replacement, sample
load and new analysis. Original source values and all six references remain in the
calculation/reconciliation evidence and persisted JSON, HTML and PDF reports.

Deploy compatible API, workers and report readers before advertising plan 6.
Do not downgrade workers while plan-6 jobs are pending or remove terminology-3 and
cash-FX metric support while those published reports remain available. Old plans,
absent optional fields, legacy request/preview hashes and queued inputs keep their
original behavior. No live provider calls are needed to validate this feature.


## Inspecting an executed preview reconciliation

Each executed preview check optionally includes `reconciliation_evidence`.
The read-only projection uses the actual `ReconciliationResult` input references,
equation results and policy payload, plus the exact pinned rule definition. It
does not resolve new operands, recalculate totals, modify tolerance or decide
whether a check passes. Rule/engine versions, policy and input fingerprints,
exact expected/observed values, signed and absolute deltas, relative-delta kind,
operand identities/statuses/provenance and calculation trace references are
preserved. Equation terms retain their registered roles and coefficients.

Inputs include report-language labels and formatted values, original imported
rows and source references. Original labels, written amounts, source scales and
period labels remain separate from base-currency values used by the engine.
Calculated FX/other retains its actual calculation trace and original source.
Absent original rows leave an empty source-content list and retain references;
no source content is inferred. A planned but unexecuted check carries null
evidence. Failed numeric checks retain their executed evidence and failed status.

The frontend offers **Conferir reconciliação** for both balance and cash checks,
showing the equation, actual values/deltas, this check's absolute rounding
allowance and a source table. The table is a keyboard-focusable horizontal scroll
region on small screens. All source and evidence fields are rendered as text.
Input changes and validation failures clear these inspectors with the existing
validation panel. Older backends can omit the optional evidence field.

This projection adds no plan/version, upload choice, API gate, migration or
publication renderer change. Existing preview/request hashes, queue inputs,
published reports, quota and model-call behavior remain unchanged. A pre-change
plan-6 hash and the existing earlier-plan hash regressions verify this boundary.

## Plan 7: grouped profitability, EBITDA variants and independent bridges

The UI negotiates plan 7, falling back through 6/5/4/3/2/legacy. The API still
defaults to plan 1. Plan 7 retains plan 6 and pins terminology v4, an additive
catalog of explicit income-statement concepts. Generic EBITDA and unconfirmed
operating profit keep their original ambiguous classifications. No generic
EBITDA alias is promoted to statutory or adjusted. Mapping a reported statutory
EBITDA explicitly confirms the EBIT + compatible D&A definition shown in the
mapping label; adjusted EBITDA requires its explicit net adjustment bridge.

The bundle adds source identities for cost of sales, total operating costs,
confirmed EBIT, combined depreciation/amortization, net EBITDA adjustments,
net other operating income/expense, net finance cost and income tax. It calculates
original catalog gross profit/margin, statutory EBITDA, adjusted EBITDA, both
EBITDA margins, both CFO/EBITDA cash-conversion percentages, and adjustments as
a percentage of calculated statutory EBITDA. Statutory/adjusted denominators
are always named separately in the preview and retained in trace variants.
No benchmarks, cause, savings or cash-health inference is added.

Cost of sales, total operating costs and D&A are nonnegative magnitudes in this
upload convention. New source-identity definitions enforce the additive
`non_negative` operand constraint; negative values fail rather than being flipped
or silently absolutized. Existing definitions and constraints retain their
previous behavior. Net other operating income/expense, finance cost, tax and
adjustments keep their signed values: positive net finance cost/tax are deducted;
negative values represent income/benefits in the explicit equation. Cost of
sales and total operating costs are separate inputs and are never substituted
for one another. D&A is combined only when explicitly provided as one source row.

Reported statutory and adjusted EBITDA have separate source concepts and
identity definitions (`ebitda_statutory@2`, `ebitda_adjusted@2`). Calculated EBITDA
continues to use the unchanged v1 formulas. Step keys, labels, original rows,
definition versions and traces distinguish the independent reported values from
calculated totals. A reported total never supplies missing EBIT or D&A and is
never used as a balancing residual.

Two unchanged rule-v1 bridges are now wired to the upload:

- `REC-PL-001`: revenue − total operating costs + net other operating result =
  source EBIT; source EBIT − net finance cost − income tax = source net income.
- `REC-EBITDA-001`: source EBIT + D&A = independently reported statutory EBITDA;
  reported statutory EBITDA + explicit net adjustments = independently reported
  adjusted EBITDA. Reported observations are traced identity calculations, not
  the derived totals being tested against themselves.

Every input must be uniquely identified, valid and in the selected current
period. Missing fields (including zero-valued adjustments or other income) are
not assumed to be zero. The preview exposes all missing source requirements
and whether each bridge ran; an unavailable bridge is never a verified identity.
Planned incompatibilities and differences above the allowance block generation
through the existing gates. Individual metric values may remain visible while
generation is blocked. Existing source/perimeter/currency/calendar checks apply.

Optional `profitability_rounding_tolerance` is a strict bounded finite nonnegative
decimal string in base currency units, separately applied to both profitability
bridges. Omitted/null means exact equality and is excluded from legacy bound
choices. It does not reuse balance or cash tolerance. The UI shows it only for
plan 7, invalidates stale previews when changed and resets it on a new file,
sample or analysis. All four bridge inspectors use the existing evidence view.

Plans 1–6 keep their pinned catalogs and exact source/request contracts, including
the independently recorded plan-6 hash regression. Pending plan-7 jobs and
published reports require a compatible worker/reader supporting terminology v4,
the new identity definitions and nonnegative contracts. Deploy compatible workers
and readers before advertising plan 7; preserve that path during rollback. No
migration or new publication renderer is introduced. The existing analyst limit
of 20 findings is unchanged: it selects from available evidence while the report
retains all calculated observations and traces. Preview remains free of provider
calls and quota charges; tests use recording providers. Live model prose and
production rollout still require the existing acceptance process.

## Plan 8: liquidity, debt, leases and working capital

Plan 8 retains plan 7 and pins additive terminology v5. It exposes explicit
eligible quick assets/quick ratio, operating working capital, gross debt excluding
leases, net debt excluding/including leases, four net-debt/EBITDA combinations
(statutory/adjusted and lease-exclusive/inclusive), three gross-interest coverage
variants, DSO/DIO/DPO and the cash-conversion cycle. Labels name the numerator,
lease/EBITDA variant and actual period. No ratio is annualized and no benchmark,
liquidity diagnosis, funding recommendation or hedging assumption is introduced.
The UI negotiates 8→7→6→5→4→3→2→legacy; API defaults remain unchanged.

### Explicit eligibility and averages

- Quick assets sum independently declared unrestricted eligible cash, eligible
  receivables and other permitted liquid assets. All three are required, including
  an explicit zero when appropriate. Total current assets and generic receivables
  are not substitutions for these classifications.
- Net debt uses the explicitly eligible unrestricted cash amount, never a silent
  substitution of total/restricted cash. Gross debt is explicitly lease-exclusive;
  the inclusive variant adds the independently declared lease liability.
- Operating working-capital assets exclude cash/non-operating assets, and its
  liabilities exclude financial debt/leases. The current-asset/current-liability
  totals used by the current ratio do not supply these operating components.
- DSO/DIO/DPO require source amounts explicitly classified as period averages.
  Closing balances and an inferred average of opening/closing figures are not
  substituted. DPO requires period purchases; COGS is not used as a proxy.
- `days_in_period@1` counts inclusive calendar days from the traced financial
  period's start/end dates. Each working-capital metric has its own traced period
  anchor (revenue, cost of sales or purchases). Leap years, months, quarters,
  fiscal years and YTD use their actual dates, not 30/360 conventions. The source
  financial amount remains an anchor in the trace; it is not a supplied day count.

`current_ratio@2`, `quick_ratio@2`, DSO/DIO/DPO@2 and net-debt/EBITDA@2 use strict
nonnegative components/positive denominators. Negative or zero EBITDA is not a
leverage denominator. Negative net debt is retained as net cash. Gross interest
expense is a separate input; net finance cost is not substituted. Existing v1
formulas/contracts keep their previous behavior. The new calendar-count and
three-component sum operations are used only by newly registered definitions.

### Typed detail rows and movement bridges

The eight-column CSV/XLSX contract still uses one sheet, consolidated scope,
explicit current/comparative basis and the existing row/size limits. Plan 8 alone
accepts three additional `statement_type` categories:

- `working_capital_detail`: explicitly reported period-average receivables,
  inventory/payables and purchases;
- `debt_schedule`: explicit aggregate drawdowns, positive repayments and signed
  FX/other debt movements;
- `lease_schedule`: explicit additions, net modifications, interest, positive
  payments and signed FX/other movements.

These detail categories preserve noncash movements; lease additions and FX are
not classified as cash-flow-statement lines. Debt/lease stock balances remain
balance-sheet rows. Terminology v5 and normalized-observation validation jointly
pin these categories; plans 1–7 and earlier catalogs continue rejecting them.
Mappings must match the source category. These are explicit aggregate movement
and working-capital inputs, not an instrument-level debt/maturity/rate import.
No sum of individual instruments, fixed/variable-rate share or maturity risk is
inferred. Schedules with multiple candidate aggregate lines require clarification.

`REC-DEBT-001@2` checks opening debt + drawdowns − repayments + FX/other against
independent closing debt. `REC-LEASE-001@2` checks opening lease liability +
additions + modifications + interest − payments + FX/other against independent
closing lease liability. These v2 rules preserve the v1 equations and operands
while matching canonical stock snapshots by adjacent opening/end dates. This
supports within-year fiscal quarters and annual opening snapshots for monthly
flows. All movements share one actual interval, and stocks retain their original
calendar, fiscal context and current-analysis basis; comparatives are not silently
reclassified. Original rule-v1 fiscal/granularity checks remain unchanged and are
covered by explicit regression cases.

Opening balances require a unique valid drawdown/addition row to anchor the
selected interval, and end immediately before its start. Closing stocks must
belong to the selected current period. Missing movements (including FX/other),
ambiguous/invalid candidates, incompatible calendars/currencies or incorrect
stock dates never become verified bridges. Numeric differences above the
explicit allowance block generation. A passing lease bridge still emits the
existing causal-evidence requirement: it cannot establish expansion or authorize
causal findings/material recommendations.

Optional `capital_rounding_tolerance` is a bounded finite nonnegative decimal
string in base currency units, applied separately to the two capital bridges.
Omitted/null means exact equality and is omitted from bound legacy choices.
Balance, cash and profitability tolerances are independent. The UI exposes the
capital allowance only for plan 8 and resets it on file/sample/new analysis;
changes invalidate the preview. Missing bridge components and all actual inputs,
formulas, deltas, source references and policy fingerprints remain inspectable.

### Bounded narrative evidence without removing calculations

The complete example calculates 63 observations. A new optional immutable
`FinancialRequest.analyst_metric_keys` pins which metric steps may supply analyst
findings. Plan 8 selects 33 business observations in the capital example while leaving intermediate source
identities, opening-stock helpers and calendar counts in the full calculation and
reconciliation chain. All 63 observations/traces remain in persisted stages and
the JSON/HTML/PDF metric appendix; dependent business traces still resolve their
source provenance through every helper. All reconciliation/quality checks execute
before this evidence selection, and the critic independently re-executes them.
Selection cannot hide a failed bridge or grant a blocked capability. An upload
with only helper calculations is not offered for generation.

The selected keys are sorted, restricted to known request steps, included in the
request/preview hash, restored by replay and documented in report methodology as
`metric-step-allowlist-v1`. Helper-only evidence IDs cannot be cited by the model.
Omitted selection preserves the exact legacy request/context/report shape; all
plans 1–7, the pre-change plan-7 hash and earlier golden reports remain unchanged.
The 256 KB analyst and 384 KB critic input limits and maximum 20 findings remain
unchanged. The full fixture fits these bounds across source languages and scales.
No financial evidence, finding count limit or validation gate is relaxed to fit.

Compatible APIs, workers and readers must support terminology v5, the typed detail
categories, new metric definitions/operations, debt/lease rule v2, renderer v4 and
the optional bound evidence selection before advertising plan 8. Preserve compatible replay
and read paths for its pending jobs and stored reports during rollback. No database
migration, live model call or production activation is introduced. Schema persistence remains the existing scoped JSON contracts.


### Complete bounded publication

New publications with explicit evidence selection use `financial-render-v4`.
Its technical appendix prints repeated structured content once and identifies
later occurrences by the exact path of the identical earlier content. Scalar
arrays are grouped without dropping values. Reference matching compares complete
canonical JSON, not labels, identities, rounded values or a lossy hash. Paths
escape `~` and `/` as JSON Pointer segments. A localized notice explains references;
all original fields remain in the JSON report and every appendix occurrence is
recoverable from the displayed values and backward references.

The HTML/PDF manifest and parity verification include reference blocks and the
new renderer version. Existing v1–v3 publications replay their original presentation
unchanged, and new unselected legacy analyses retain renderer v3. The existing
6,000-block, 1.5-million-character, 120-page and artifact-byte bounds remain in
force. Acceptance covers complete channel generation, appendix reconstruction,
near-duplicate source values and escaped nested paths, alongside legacy goldens.

When cash movements are also supplied, the intermediate `cash_fx_other` identity
is likewise kept in calculations and the cash reconciliation but excluded as a
standalone narrative root. The complete 67-metric cash/capital example retains
36 narrative observations and stays below the critic budget with 20 large
findings, million-unit sources, long filenames and all four report languages.

## Explicit tabular source preparation

The original eight-column CSV / one-sheet XLSX contract remains unchanged when
`tabular_import` is absent. Compatible backends advertise
`tabular_import_versions: ["1"]`; older backends keep the template-only UI.

Authenticated `POST /financial-v2/inspect` accepts the original CSV/XLSX file and
an explicitly selected CSV delimiter. It returns a source hash, deterministic
inspection hash, ordered worksheet inventory (including hidden sheets), dimensions,
formula/merged-range information and a bounded sample of the first 20 rows. It
performs no mapping, financial calculation, persistence, model call or quota charge.
Its responses are private/no-store and use the same multipart body limit as preview
and generation. A sample is a grid view, never a semantic extraction proposal.

A confirmed `tabular_import` binds 1–12 table selections to that exact file and
delimiter. Each selects a worksheet by its original index, header row and explicit
first/last data rows. Every canonical financial field must be associated with a
source column or a user-confirmed common value. Rubric and amount always require
source columns. Periods and statement types are never inferred from headers or
sheet names. Wide exports use separate explicit selections for each amount column
and its corresponding confirmed period/basis. Different fields cannot reuse one
column; the same physical amount cell cannot appear in two selections.

The UI exposes every worksheet and requires confirmation that omitted content was
reviewed. Selection changes clear mappings and invalidate preview approval. The
preview reports selected tables, ranges, omitted columns/sheets and skipped wholly
blank rows. Unselected content does not become analysis evidence. The original
file and every choice remain in the durable job; the publication receipt retains
the layout manifest and per-field source-cell/common-value bindings. The canonical
source reference `s2!C4` identifies amount cell C4 on the second original worksheet
(or `s1!B2` in a CSV grid); normalization, calculations, audit and report evidence
retain these references with the original file hash. The preview also displays the
source sheet name and each field's coordinate or confirmed common value.

Preparation allows up to eight worksheets, 1000 physical rows per sheet, 32 columns
and 20000 grid cells, while the existing aggregate limit of 500 selected financial
rows and 5 MiB source size remains unchanged. Existing ZIP expansion/member and XML
entity protections apply before workbook processing. Selected formulas, booleans,
nonfinite values, overlapping amount selections and selected merged cells are
rejected. Unselected formulas are displayed as source text and never evaluated.
Date/time cells are not reinterpreted as numeric financial amounts. Chart sheets
and aliased worksheet relationships are rejected so worksheet identity remains
unambiguous. XLSX numeric XML values are read exactly as decimal text rather than binary floats;
numeric cells use the chosen input locale without reinterpreting literal text cells.

The complete layout choice is included in preview approval, queue identity and the
source UUID's canonical selection fingerprint. Changing even a header/range that
happens to yield identical numbers requires a new preview. Dictionary ordering
alone does not change identity. Legacy choices omit the new optional field and
retain their pinned hashes. Rollback requires compatible workers for pending
prepared imports; old template jobs and published report readers remain compatible.
PDF/image extraction, cross-file merging and instrument-level schedule import remain
separate extraction and validation phases.

Empty worksheets remain in the inventory and can be excluded while populated
worksheets are imported. Merged-cell intersection checks cover the selected
header and data interval separately; formatting in an explicitly excluded gap
between them does not prevent import.

## Confirmed text-PDF preparation

Compatible POSIX hosts advertise `pdf_import_versions: ["1"]` and accept text PDFs
through a separate optional `pdf_import` contract. The original tabular-import-v1
contract, prepared CSV/XLSX identities and legacy requests remain unchanged. PDF
preparation requires exact normalization version 2; older normalization semantics
are not accepted for this new source format.

Inspection takes explicitly ordered page numbers (up to eight from a document of
at most 300 pages, within the existing 5 MiB upload limit). It returns extracted
text with numbered lines, an explicit inventory of excluded pages and a grid split
at user-selected Unicode character boundaries. Boundaries are not inferred from
financial labels, years or amounts. The user re-inspects after changing pages or
boundaries, then associates every field with a text column or confirmed common
metadata. Header 0 permits data beginning on the first extracted line.

Text extraction does not reproduce a PDF's visual layout reliably. Before any
financial calculation, preview therefore returns `financial-pdf-review-v1` with
`ready=false`, source text, page/line/character origins and every proposed row.
It contains no financial metrics and makes no model calls or quota changes. Users
must compare each extracted row with their original PDF and confirm every row,
then validate again. There is no automatic confirmation of all rows. A server
review hash binds the file, extraction result, page/column choices, selected table
bindings, quoted values and source interpretation. Missing confirmations or a stale
review return the source-review stage; generation rejects them before enqueueing.

Only then does the unchanged deterministic pipeline normalize confirmed financial
rows, request ambiguous concept mappings, reconcile identities and expose the
financial preview. A source review cannot grant calculation capabilities, replace
missing values or bypass failed reconciliations. Locale, sign, currency, scale and
calendar changes require fresh source confirmation; semantic concept mappings and
report language still require financial preview approval in the ordinary way.

Canonical references such as `p17:L6:B` mean column B on extracted text line 6 of
original PDF page 17. Publication receipts preserve the quoted line and each field's
zero-based, half-open character span. The source index records both
`financial-pdf-text-v1` and the actual pypdf version. The inspection hash includes
the original byte hash, decoder version and extracted-text hash. Original file,
layout, review hash and exact confirmed references remain bound to the durable job
and replay. Existing published reports re-execute their persisted financial rows;
they do not re-extract the PDF when read.

The decoder runs in a separate process with a 512 MiB address-space ceiling,
5-second CPU soft limit (6-second hard limit), 12-second parent timeout and a
restricted environment that excludes provider credentials. Each application process
runs at most one decoder at a time; overlapping preparations receive a busy/retry
message instead of spawning additional decoders. Queued jobs keep their pending
state on this transient condition and retry without consuming quota or calling
providers. Worker parsing runs in a thread pool so the API event loop remains
responsive while the child decoder runs. Child processes disable core dumps.
Decoded selected-page
streams are bounded to 4 MiB each / 16 MiB total; extracted UTF-8 text to 200 KB,
1000 lines per page and 2000 characters per line. Prepared grids retain the 20000
cell and 500 selected-financial-row bounds; the complete source-review response
is additionally bounded to 256 KB. Failures are sanitized and never become guessed
values. Encrypted, textless, rotated or undecodable selected pages are rejected;
this version performs no OCR, image transcription, password handling or model-based
extraction. Unselected pages remain explicit exclusions, not inferred evidence.

Workers processing queued PDF imports must retain compatible extraction behavior.
A changed decoder/layout/text hash requires a new inspection and source review;
there is no silent replay using a different extraction. Roll back new uploads by
stopping PDF capability advertisement while keeping compatible workers and report
readers for pending jobs and existing publications. No migration or production
feature activation is introduced.

## Source-bound monthly close preparation preview

`POST /financial-v2/month-end/preview` accepts the same bounded `file` and
`options` as financial validation plus a separate strict `month_end` JSON
contract, version 1. It requires the existing product workspace/owner-or-manager
access and the default-off `SALESLAB_V2_FINANCIAL_MONTH_END_ENABLED` flag.
Capabilities advertise `month_end_preview_versions: ["1"]` only when that flag
is enabled; older backends keep the preparation panel hidden. No flags are
activated by this change.

The policy supplies an explicit target month, ID/version, currency, fiscal start,
required statements/reconciliations/tasks/comparison relations, positive absolute
and percentage materiality thresholds, and their approved basis. No threshold,
required coverage or completed task is selected automatically. Decimal policy
thresholds use a dot, independently of the file's numeric locale. The fiscal
start must match the file's confirmed fiscal end, or civil January when the
source uses calendar periods. Existing ambiguous fiscal/month/YTD source labels
remain unresolved; this adapter does not reinterpret them.

Every comparison, movement expectation and task confirmation selects original
row locations in this upload. The server attaches authenticated workspace/actor
and source IDs. A required financial preview hash rejects stale source,
interpretation or mappings. Prepared CSV/XLSX retain cell origins; PDF rows must
already have passed their separate source-confirmation contract. Inputs may be
financially blocked: preparation returns the missing-data checklist instead of
calling a model or inventing comparisons.

The existing F-10 evaluator checks monthly, monthly-year-on-year and YTD cutoffs,
concept/currency/scope compatibility, required scoped reconciliations, explicit
movement expectations and human task evidence. It never invents YTD by adding
available months. Materiality uses the declared exact policy and does not classify
performance: falling revenue can be complete data requiring review. A zero
comparison base preserves the monetary delta and leaves the percentage unavailable.
All close calculations carry their existing versioned metric definitions and
CalculationTrace in the response.

This endpoint is a **non-persisted preparation preview**, distinct from the
internal durable F-10 service and audited financial report publication. It writes
no runs, stages, history, quota or accounting entries, and makes no provider calls.
The private/no-store response includes the checklist, source capability decisions,
concrete missing-data requests, calculations, original rows/import origins, both
sets of choices, a source/choice/actor binding hash, timestamp and payload checksum.
The JSON download preserves this evidence for review. The checksum detects content
changes; it is not a signature, durable server receipt or accounting certificate.
Repeated equal inputs retain the binding hash; operational timestamps/checksums
can differ. No run IDs in this ephemeral response imply a persisted report.

The UI distinguishes incomplete, review-required and ready-for-review preparation,
keeps human confirmations visibly separate from engine checks, and invalidates
exports when source or policy decisions change. It does not put the snapshot in
report history. Existing financial generation and its quota rules are unchanged.
Rollback by disabling the existing month-end flag; financial uploads/reports and
internal persisted F-10 snapshots remain compatible. No migration or dependency
is introduced.

The monthly UI uses a separate two-decimal ROUND_HALF_UP presentation projection
in the report language locale. Exact canonical comparison values, traces and
materiality decisions remain intact in `result` and the JSON snapshot. Source
hashes and canonical reason identifiers are available in expandable evidence;
the checklist presents human-readable reasons and keeps confirmations distinct.

## Plan 9: distinct growth bases and an explicit monetary contribution bridge

Plan 9 retains plan 8 and pins exact normalization 2 and additive terminology 6.
The API continues defaulting to plan 1; clients negotiate 9 only when both plan 9
and exact normalization 2 are advertised. Plans 1–8, their aliases, persisted
requests and pinned hashes remain compatible.

New monetary source concepts are `revenue_constant_currency`,
`revenue_like_for_like`, `growth_lfl_contribution`,
`growth_expansion_acquisition_contribution`, `growth_fx_contribution` and
`growth_other_contribution`, all explicitly mapped as income-statement flows.
They do not alias generic growth, percentages or organic growth. Current and
comparative constant-currency revenues and LFL revenues are separate bases under
their own confirmed method/perimeter. Derived `revenue_growth_cc_pct` and
`revenue_growth_lfl_pct` use `(current-prior)/abs(prior)*100`, exact monetary
inputs, comparable periods/currency/scope and versioned percentage display
rounding. A published percentage without its monetary bases remains unavailable;
it is never treated as revenue or a bridge contribution.

Each new source row requires `growth_definitions[location]`, an exact, trimmed
quotation of 10–2000 characters from its original **row label** describing the
method/perimeter or the reported contribution bridge basis. This first contract
does not fetch definitions from another note, page, URL or invented user prose.
An unrecognized label first requires explicit concept mapping. Recognized growth
rows without confirmed definitions return a private/no-store
`financial-growth-review-v1` source-only preview (`ready=false`) before financial
calculation. Partial confirmations retain every original row for review. Missing,
foreign, non-verbatim or conflicting quotations cannot proceed. The current and
comparative rows of each method must quote the same definition; all contribution
rows must quote the same reported bridge basis. The normalizer independently
requires the quotations and rejects inconsistent methods. Quotation matching
proves source anchoring and records the user's semantic confirmation; it does
not certify the accounting or FX methodology.

The source identity, financial request, preview hash, queued choices and original
rows bind these decisions. A keyword-only optional `FinancialRow.definition_quote`
is omitted from source serialization when absent, preserving historical contracts.
Definitions follow the verified checkpoint into analyst/critic context and
approval-bound report methodology, then into JSON, HTML and PDF with original
source IDs, content hashes and locations. Changing a quote requires a new preview;
published reports replay their original approved definitions.

`revenue_change_reported@1` uses the additive `growth_delta` operation: exact
current-minus-prior monetary revenue with the same structural comparison proof
as reported growth. `REC-GROWTH-001@2` reconciles independently supplied signed
monetary LFL, expansion/acquisition, FX and other contributions to this reported
change. It leaves the frozen v1 percentage-point rule unchanged. The explicit
`growth_rounding_tolerance` is in base currency units and defaults to exact
matching. Missing components are not zero. Currency, period or definition
conflicts block the affected calculation/bridge; a bridge difference above the
accepted allowance blocks generation.

A rate on an LFL perimeter is not its contribution on the reported group base.
The engine does not subtract reported/CC/LFL rates, derive contribution amounts
from them, infer FX effects, or assert that all disclosed bases share a perimeter.
This monetary bridge does not establish an additional CC/LFL bridge or authorize
an organic-growth classification: that causal gate remains blocked without the
existing independent explicit capability evidence.

The UI asks users to quote the source definitions, shows actual inputs/traces,
bridge residuals and concrete data gaps, and clears stale confirmations when
source interpretation or mapping changes. Confirmed definitions cannot bypass
normalization, sufficiency or independent audit. The synthetic JM-FIN-005
proportions test 9.3% reported, 4.9% CC and 0.6% LFL separately; they are not a
business benchmark or a newly transcribed public filing.

No migration, dependency, model call or quota is added to preparation. Publication
keeps the existing job/idempotency/atomic-quota rules. Rollback by stopping plan-9
advertisement while retaining compatible workers and readers for plan-9 jobs and
publications. No production flag is activated by this change.

Successfully executed reported, CC or LFL growth pairs each establish the
comparison prerequisites independently. A CC/LFL rate is retained in analyst
evidence and the approved financial snapshot even when no reported-revenue
comparative exists. Failed, missing or incompatible pairs do not grant this
authorization; the original sufficiency and source checks still apply.

## Plan 10: aggregate debt principal, contractual rates and maturities

Plan 10 requires exact normalization 2 and adds terminology 7. Plans 1–9 retain
pinned source contracts, selections and optional-field omission. Capability-aware
clients prefer 10 only when normalization 2 is advertised and fall back to 9/earlier.

Five explicit `debt_schedule` monetary stocks share the same consolidated scope,
currency, reporting period and user-confirmed principal definition:
`debt_eligible_principal`, `debt_fixed_principal`, `debt_variable_principal`,
`debt_due_within_12_months`, `debt_due_after_12_months`. Principal excludes leases;
rate type is contractual, before hedging. The maturity window is the twelve months
AFTER the reporting date, with all remaining principal in the second bucket.
These are explicit aggregate disclosures, not automatically derived instrument
schedules, cash interest estimates, refinancing needs or net hedged exposures.
Published totals or rate labels alone do not establish contractual protections.

A private, source-only `financial-debt-review-v1` response requires a verbatim
quotation (10–2000 characters) from each original row label. Every selected debt
classification must quote the same documented basis. The quote records a human
semantic confirmation; it does not certify an agreement or accounting policy.
Absent source wording must be corrected in the source, not invented in the UI.
Foreign, stale, non-verbatim or inconsistent quotations are rejected; partial
confirmations survive ordinary quote editing. Source/import/layout/mapping changes
clear them. Source identity, preview hash and queued choices bind the confirmations.

`REC-DEBT-RATE-001@1` checks fixed + variable = eligible principal;
`REC-DEBT-MATURITY-001@1` checks due within twelve months + due thereafter = eligible
principal. Each percentage requires its complete partition, including an explicitly
reported zero if applicable. Missing, duplicate, negative or invalid components are
not zero, are not residuals and are never inferred. Currency/period compatibility
is independently enforced by the metric and reconciliation engines. A nonzero
partition residual blocks publication unless covered by the explicit
`debt_rounding_tolerance` in base currency units (default 0, exact equality).

`fixed_variable_mix@2` is fixed principal / eligible principal × 100;
`variable_rate_share_pct@1` is variable principal / eligible principal × 100;
`maturity_concentration@2` is principal due within twelve months / eligible principal
× 100. Existing metric versions remain frozen. Their calculation traces preserve
numerators, denominators, source rows and definitions. Confirmed source quotations
are included in the independent analyst/audit context and report methodology, with
one presentation shared by HTML and PDF. Gross borrowing balances remain separate:
this principal schedule is not silently substituted for carrying-value debt.

No capability for hedging, material recommendations, refinancing or debt-service
forecasts is enabled. No database migration, dependency, provider call or production
activation is added. Stop advertising plan 10 to roll back new uploads; retain
compatible workers/readers for pending plan-10 jobs and existing reports.

## Plan 11: paid capex classification and outstanding commitments

Plan 11 requires exact normalization 2 and terminology 8. Earlier plans keep their
pinned inputs and payload omission. Clients select 11 only when normalization 2 is
advertised and otherwise fall back to 10 or an earlier supported plan.

The explicit `capex_schedule` concepts are `capex_cash_total`,
`capex_cash_maintenance`, `capex_cash_growth` and
`capex_commitments_outstanding`. Cash amounts are positive magnitudes of actual
fixed-asset purchase payments for the source period, under a user-confirmed
maintenance/growth definition. Commitments are a separate nonnegative stock at the
reporting date. They do not become cash payments, expenditure dates, funding needs,
a project return or a forecast. No IFRS/local-GAAP classification is inferred.

A private source-only `financial-capex-review-v1` response collects verbatim
quotations of 10–2000 characters from original row labels. All cash classification
rows must cite the same documented payment/classification basis. If an independent
`capex` purchase cash-flow row is present, that row must also cite this basis before
being tied to the classified total. Outstanding commitments cite their own separate
definition. Missing wording must be corrected in the source. Foreign, non-verbatim,
inconsistent or stale definitions are rejected; quotations are human semantic
confirmations rather than certification of accounting treatment or contracts.

`REC-CAPEX-001@1` checks maintenance + growth = confirmed cash total.
`REC-CAPEX-002@1` checks that total against the magnitude of an independent signed
purchase cash outflow when available. Missing components are not zero or residuals;
no maintenance/growth percentage is produced without the complete partition.
Missing independent purchases remain a visible reconciliation gap. Duplicate,
negative, invalid or incompatible cash classifications are not selected silently.
The explicit `capex_rounding_tolerance` uses base currency units, defaults to exact
zero and is independent of cash movement, capital and debt tolerances.

Four immutable identity metrics retain each source amount separately. Two immutable
percentage metrics divide maintenance/growth cash purchases by the confirmed total.
An explicitly zero total and zero components can reconcile as zero amounts, while
shares remain unavailable with `ZERO_DENOMINATOR`; no zero percentage is invented.
The historical signed `capex` input and basic free-cash-flow formulas are unchanged.
The new positive classified total never silently replaces those sources, and
commitments never enter cash capex or free cash flow.

Source identity, preview, durable queue decisions, independent analyst/audit context
and report methodology bind the quotations. HTML and PDF share the same definitions
and provenance. Source, import interpretation and mapping changes clear stale
confirmations, while ordinary quote editing retains partial review. Separate manual
growth clearing now clears only its own review; source resets clear all review
families. UI fallback hides unsupported controls and choices.

No migration, dependency, live model call or production activation is introduced.
Material investment, financing and return recommendations remain gated. Roll back
new uploads by stopping plan-11 advertisement; retain compatible workers/readers for
pending plan-11 jobs and existing publications.

## Saved monthly preparations

An eligible owner/manager can explicitly save a reviewed month-end preparation with
`POST /financial-v2/month-end/snapshots`. The multipart request carries the same
file, upload choices and `month_end` policy as preview, plus `save` containing the
reviewed `binding_hash` and a UUID `attempt_key`. Source, policy or confirmation
changes require a fresh preview. Incomplete and review-required preparations can
be preserved without changing their status or attesting an accounting close.

This is a product preparation snapshot, separate from an audited financial report
and from the existing internal F-10 persisted service. Its schema is
`financial-month-end-product-snapshot-v1`; `preparation_schema` identifies the
unchanged preview-v1 contract. The result retains exact calculation traces, source
rows, import decisions, language/locale, thresholds, tasks and confirmations.
`snapshot_id` identifies storage; the deterministic preparation ID inside `result`
retains its source/policy binding. `persisted=true` and `quota_consumed=false` are
explicit. No paid `Analysis`, product quota, provider call or accounting entry is
created. The existing source/run/stage tables store the immutable input and output;
no migration or new feature flag is introduced.

Save is atomic and idempotent per workspace/attempt. A short workspace lock protects
concurrent saves; matching retries return the identical stored JSON. Reusing an
attempt with different inputs returns 409. Each snapshot input/output is bounded to
2 MB. The operational limit is twenty new saves per workspace/hour, matching the
existing upload attempt ceiling but tracked separately; matching retries remain
readable at the limit. New saves require existing upload eligibility and the monthly
preparation flag. A save never invokes financial report generation.

`GET /financial-v2/month-end/snapshots` returns a workspace index of at most ten
items with a scoped UUID cursor. The index exposes bound source/policy metadata;
it does not assess readiness. `GET /financial-v2/month-end/snapshots/{snapshot_id}`
checks current workspace membership/role, run/stage hashes and source registration,
then independently restores the typed financial request and recalculates the
monthly preparation. Changed results are rejected even if their output hashes
were recomputed. Reads return the exact originally saved JSON. All successful
snapshot responses use private/no-store caching.

Existing snapshots remain readable by allowlisted owners/managers while new upload
or monthly preparation is disabled, provided V2 and stage persistence remain enabled.
Foreign workspace IDs, revoked roles and foreign history cursors do not expose data.
Read paths do not modify snapshots, create versions or consume quota. There is no
edit, delete, attestation, ERP write or publication action in this slice.

The capability-negotiated UI keeps active preparation and saved-history views
separate. Saving requires an explicit button press after preview; a failed response
retains its attempt UUID for a safe retry. Policy/source edits invalidate the active
save confirmation. Saved preparations can be reopened and downloaded after refresh
without reuploading a file, including when new uploads are disabled. Source text is
rendered as text, and stale replies are discarded after session or history navigation changes.
Older servers without snapshot capabilities retain the preview/download flow.

## Plan 12: source-confirmed period ROIC

Planning version `12` requires exact normalization `2` and terminology `9`.
It retains plans 1–11 unchanged and adds `roic_schedule` with two monetary rows:
`nopat_with_explicit_tax_assumptions` (reported operating profit after operating
taxes) and `average_invested_capital` (explicitly reported period-average capital).
The planner does not calculate taxes, choose a tax rate, construct an average from
closing balances, substitute EBIT/net income, or annualize interim returns.

`roic_sources` contains `nopat_location`, `nopat_definition`,
`tax_assumptions_location`, `tax_assumptions`, `average_capital_location` and
`average_capital_definition`. Every quotation is copied verbatim from an original
selected row label (10–2000 characters). The tax quotation can share the NOPAT row
or refer to another row in the same source, period and scope. Concept mappings
are independently confirmed; quoting a row alone does not certify its semantics.
After both operands are uniquely mapped, missing confirmation returns a private,
no-store `financial-roic-review-v1` response before any metric or provider
execution. Incomplete or ambiguous mappings first return the ordinary mapping
preview, with unconfirmed ROIC values blocked, so users can correct them. Unrecognized labels retain the
existing explicit mapping workflow. Ambiguous or mismatched source roles are
rejected rather than selecting or summing rows automatically.

The source choices and exact original rows are bound to the file SHA-256 and
source identity. Terminology-9 normalization independently rechecks this binding,
including row metadata and amounts, during preview and persisted-request replay.
The operands must have identical period identities, fiscal calendars, scope and
currency, be confirmed monetary values, and use a positive average denominator.
Normalization handles explicitly declared scales before division; no FX is
inferred. Zero or negative NOPAT remains valid; zero/negative capital, percentage
operands, stale evidence, incompatible bases or missing operands block ROIC.
The registered `roic@2` calculation is `NOPAT / average invested capital * 100`;
`roic@1` keeps its prior contract. Exact values and calculation traces remain
separate from display rounding.

The private preview exposes operand gaps and includes ROIC in analyst evidence
only when calculated. Source quotations, row hashes and file hashes follow the
checkpoint into the audited report methodology in HTML and PDF. They are
user-confirmed evidence, not an independent certification of the tax assumptions.
The browser negotiates plan 12, clears source confirmation when import selections,
mappings or source interpretation change, preserves partial quotation edits, and
falls back to older plans without sending unsupported fields. Asset URLs retain
content-addressed cache busting.

Queue inputs preserve `roic_sources`; the existing idempotent publication and
report integrity replay apply without new tables or migrations. Reopening an
approved report neither calls providers nor consumes an additional analysis.
All production activation settings remain opt-in.

## Plan 13: operating working capital disclosure reconciliation

Planning `13` pins terminology `10` and exact normalization `2`, retaining plans
1–12 and their request/replay hashes. Two explicit `working_capital_detail`
concepts are added: `reported_operating_nwc` (a signed closing stock) and
`reported_operating_nwc_change` (the signed published closing-minus-opening
balance change). Existing `current_operating_assets` and
`current_operating_liabilities` remain the component identities, excluding cash /
non-operating assets and debt / leases respectively. Generic current assets or
liabilities and period-average receivables/inventory/payables are not substitutes.

When either new disclosure is present, `working_capital_definitions` supplies the
same source-verbatim operating perimeter / balance-change definition for all
selected disclosure and operating-component rows, including opening stocks.
Quotations are 10–2000 characters and must occur in the original selected labels.
Unrecognized mappings first return the ordinary mapping preview. Once mappings
are resolved, incomplete confirmation returns a private, no-store
`financial-working-capital-review-v1`, preserving partial confirmations. Unknown
locations, invented definitions or inconsistent quoted perimeters are rejected.
Terminology-10 normalization independently requires these quotes, their consistency
and nonnegative operating asset/liability components during deterministic replay.
Net working capital and its change may legitimately be negative or zero.

The planner never infers missing stocks/components or replaces them with zero.
Opening stocks must be explicitly current-source observations whose reporting end
immediately precedes the closing period's start, in the same fiscal calendar.
Annual, monthly and fiscal-quarter intervals are supported without annualizing,
summing averages or treating prior-year comparative rows as opening balances.
The new `operating_nwc_change@1` metric subtracts adjacent opening from closing
reported NWC using the versioned `opening_stock` period rule. Exact Decimal
arithmetic, scope/dimension/currency checks and original references are traced by
the existing engine. Earlier metric period rules are unchanged.

Three additive reconciliation rules provide independent source tie-outs:

- `REC-WC-CLOSING-001`: closing operating assets minus operating liabilities equals
  closing reported operating NWC.
- `REC-WC-OPENING-001`: opening components equal the opening reported NWC, with
  matching canonical source periods across those three stocks.
- `REC-WC-CHANGE-001`: opening reported NWC plus the reported interval balance
  change equals closing reported NWC. The flow interval anchors reporting dates,
  comparison basis and fiscal calendar; an unrelated prior period is rejected.

`working_capital_rounding_tolerance` is a bounded finite nonnegative decimal
string in base currency units, defaulting to exact equality. It is independent
of balance-sheet, cash, profitability, capital, debt, growth and capex allowances.
Material differences block publication through existing quality gates; missing
operands appear as explicit reconciliation/metric gaps. A missing disclosure does
not certify a computed subtotal or change as independently reconciled.

**These are balance changes, not cash flow effects.** They can contain FX,
acquisitions, disposals, reclassifications and other noncash/perimeter changes.
No automatic sign inversion, CFO attribution, cash release, savings estimate,
forecast or funding recommendation is authorized. A verified balance/disclosure
bridge does not demonstrate cash absorption or explain CFO without separate
source evidence. Labels and report methodology retain this distinction in all
four languages, and HTML/PDF use the same approved report payload.

The negotiated frontend adds perimeter confirmation and a dedicated allowance,
clears decisions on changes to source/import/mapping/interpretation, preserves
partial edits, and omits unsupported fields on older backends. Queue inputs,
source identity, preview hashes, audit, report publication and read replay retain
the same evidence. Reopening a report consumes neither a new analysis nor a model
call. No new tables, migrations, dependencies or production flag activation are
introduced. Rollback must retain compatible workers/readers for plan-13 queued
jobs and existing terminology-10 publications.



## Saved monthly checklist CSV export

Eligible snapshot readers can download
`GET /financial-v2/month-end/snapshots/{snapshot_id}/checklist.csv` after the same
current workspace/role checks and independent result replay used by JSON reads.
Capability `month_end_checklist_csv_versions: ["1"]` advertises the saved-history
button; older servers keep their JSON download. Existing snapshots remain
exportable when new upload/monthly preparation is disabled, while snapshot reads
remain authorized. Each download rechecks authorization and integrity. Foreign,
revoked or altered snapshots cannot be exported.

The deterministic UTF-8 BOM, semicolon-delimited, fully quoted file has one row per
checklist item. Canonical headers, statuses and reason codes retain their identity;
titles retain the saved report language. Every row includes snapshot ID/payload
hash, source hash/name, target month, policy/version, preparation readiness and
`accounting_close_attested=false`. `evidence_json` preserves each item's source
references. Potential spreadsheet formula prefixes in text, including leading
whitespace or invisible characters, receive a leading apostrophe. CSV consumers
must retain that protection when copying cells into spreadsheets.

This is a checklist projection for review. The unchanged JSON snapshot remains the
complete evidence artifact with exact comparison values, calculation traces and
policy thresholds. CSV export does not update a snapshot, confirm tasks, attest
an accounting close or publish a financial report. Downloads use private/no-store
caching and attachment headers, invoke no models and consume no analysis quota.
No migration, dependency, production activation or stored schema change is added.


## Monthly checklist presentation in the saved report language

Monthly preview and reopened snapshot results now display readiness, checklist
statuses, statements, all 14 current reconciliation labels, missing-data reasons,
comparison relations, threshold/zero-base notices and evidence headings in the
result's saved `language` (`pt`, `en`, `es`, `fr`). Reopened download labels and
success messages use that same language, independently of the active upload
language selector. The result region declares its language for assistive tools.
Application navigation and source/policy entry controls retain their existing UI
language; this change localizes the preparation result rather than translating
user-authored task names, policy wording or source quotations.

Canonical IDs, reason codes, source text, original item titles and references
remain visible in evidence details. Unknown reason codes retain their code beside
a localized review request; an unsupported saved language visibly falls back to
English. Human task confirmation remains distinct from engine checks, and readiness
never means an attested accounting close. Display formatting continues to use the
existing server-provided presentation; exact values and materiality are unchanged.

The dictionaries are presentation-only and travel inside the existing hashed
frontend asset. No new request fields, API endpoints, snapshot fields, database
writes, quotas, provider calls or migrations are introduced. Existing JSON/CSV
exports and saved payload hashes remain unchanged. Browser tests reopen real
prepared fixtures in all four languages and check exact JSON downloads, hostile
source text and mobile layouts. Catalog completeness tests track current evaluator
reason codes and reconciliation labels, including fallback behavior.
