# Financial report compiler and web/PDF parity (F-08)

`FinancialReportCompiler` consumes one persisted, approved F-07 audit and creates
the sole versioned `ReportResult`, HTML and PDF from one ordered document.
Publication is internal and default-off. It completes the existing V2 run after
both rendered channels pass the parity gate and all three stages are persisted.

```python
from uuid import UUID
from saleslab_v2.reports.service import FinancialReportCompiler

async with session_factory.begin() as session:
    report = await FinancialReportCompiler(session, settings).execute(
        workspace_id=authenticated_workspace_id,
        user_id=authenticated_user_id,
        run_id=persisted_run_id,
        audit_id=UUID(approved_audit_id),
    )
# Commit succeeds before serving report.payload or report.artifacts.
# artifacts: html, pdf_base64, html_hash, pdf_hash, parity.
```

Requires `SALESLAB_V2_ENABLED`, `SALESLAB_V2_PERSIST_STAGES`,
`SALESLAB_V2_FINANCIAL_AUDIT_ENABLED`, and
`SALESLAB_V2_FINANCIAL_REPORT_ENABLED=true`, a workspace in
`SALESLAB_V2_INTERNAL_WORKSPACES`, and owner/manager membership. Compilation does
not require enabling new analyst calls or supplying any model credentials.

## Approval and one writer

The compiler accepts UUIDs, never caller-provided draft, report or audit JSON.
It locks the scoped run, validates the original completed audit/input identities
and invokes F-07's exact replay validation using a critic that cannot generate.
This rechecks live source/metric/trace/quality records, independently re-executes
F-02–F-05, rebinds the complete F-06 draft, and verifies revision ancestry/budget.
Any attempted model invocation raises and rolls back the operation.

It reconstructs every per-finding semantic check through the strict critic schema
and recomputes the aggregate verdict. Only `approved` or
`approved_with_warnings`, exact full finding approval, no rejected objects, and
the latest completed draft/audit are eligible. A stored status string or freshly
rehashed forged approval is insufficient. Failed critics, blocking checks and
revision requests cannot publish.

The report binds the exact audit/draft/checkpoint hashes. Findings are copied as
audited objects; the original draft and audit remain immutable. In particular,
`audit_result.report_published=false` describes the original F-07 snapshot. The
completed report/render stages and run state are publication authority.

## Report contract

The strict versioned envelope contains identity, timestamps, language, independent
format locale, periods, coverage, capabilities, issues, metrics and traces,
evidence, approved findings, reconciliations, warnings, missing-data requests,
methodology, sources and approval bindings. Company profile remains explicitly
unknown because the internal pipeline does not collect one.

The executive summary references approved findings and limitations. Factual F-06
does not support forecasts, recommendations or measured materiality; those arrays
and rankings remain empty. The compiler introduces no additional narrative or
financial judgments. It only resolves approved evidence placeholders to canonical
Decimal values, units and the selected locale, without float conversion, silent
rounding or scale conversion. Full values, formulas and provenance remain present.

## Shared presentation and actual parity

`presentation.document()` supplies one ordered block sequence to both renderers.
The reader-facing sections show observations, warnings and missing data. The
complete contract is visibly retained in a technical evidence appendix; this
internal renderer favors inspectability over the final customer-facing design.

Headings and policy text are translated in pt/en/es/fr. Source text, original
critic messages and canonical technical codes are preserved. The renderer
explicitly identifies codes without approved translations instead of inventing a
translation. Complete localization of all upstream technical codes and a polished
customer UI remain product rollout work; this slice does not silently claim them.

HTML is escaped and self-contained; no external fonts/assets/scripts or model
text interpreted as markup. PDF uses bundled Vera TrueType with font coverage
checks and flow-based pagination. Unsupported glyphs raise rather than becoming
blank squares. The renderer refuses more than 6,000 blocks, 1.5 million text
characters, 120 PDF pages, 8 MB PDF or 4 MB HTML.

The gate compares every visible ordered HTML block and extracted PDF page text
against the shared document, validates report/version and page identity, and
rejects modified HTML or PDF presentation using deterministic rendering. PDF
whitespace changes from wrapping are normalized only for text comparison; the
deterministic byte check additionally catches presentation/visibility edits.
Actual artifact hashes, block hashes, report hash and page count are persisted.
The persisted artifact envelope has a strict, closed schema: unexpected fields,
incorrect value types and oversized encoded payloads fail before replay returns.
A matching manifest alone cannot pass an omitted warning, changed value, missing
page, reordering, or hidden HTML content. Exceptions block completion and leave
the run awaiting compilation.

## Persistence and replay

Three stages in the existing table: `financial_report_input`, `financial_report`,
`financial_report_render`. Stable run/compiler IDs enforce one report version per
run. Input identity includes the exact approval, evidence, locale and versions.
The run lock serializes concurrent requests; identical requests replay immutable
payload/artifact bytes. Replay revalidates current evidence, approval, the expected
report and actual stored channels before returning. Changed locale, missing
stages or altered output fail closed rather than silently replacing publication.

All writes and `compiling → completed` plus `completed_at` happen in one savepoint
inside the caller's transaction. Rendering/persistence failures roll back without
partial publication and the session can retry. No new migrations/tables or public
routes are introduced; legacy endpoints, quotas and billing are unchanged.

## Verification

- Pure contract and actual HTML/PDF tests in four languages, Decimal/locale,
  warnings/gaps, binding errors, markup, unsupported glyphs, forged approvals,
  artifact tampering and omitted/reordered PDF pages.
- Synthetic deterministic goldens with full document hashes and page-level
  raster layout fingerprints. ReportLab and test-only PDFium are pinned; changed
  baselines require explicit visual inspection, never automatic acceptance.
- Real PostgreSQL tests cover F-01 → F-08, corrected revision/reaudit, concurrent
  publication, immutable replay, flags/scoping, live data tampering, missing stages
  and renderer rollback/session reuse.
- `python scripts/render_financial_report_goldens.py` reproduces HTML, PDFs,
  individual page PNGs and contact sheets under `tmp/pdfs/f08/` for visual review.

Golden fixtures are synthetic contract/render regression, not certification of
live model accuracy or the entire future financial benchmark corpus. F-09 shadow
comparison, customer UI integration, production activation, and live model
evaluation remain separate milestones.

## Reader requirement labels (renderer v3)

New publications explain known missing-data requirement codes in Portuguese,
English, Spanish and French, while retaining the canonical code alongside each
label. The section explains that requirements for additional capabilities do not
by themselves invalidate the report's verified observations. Unknown codes remain
visible verbatim; the renderer never invents a translation or changes gates.

Renderer v3 preserves exact payloads and the same document in HTML and PDF.
Existing v1/v2 publications replay their original renderer, locale and artifacts.
The versioned critic preview remains pinned to renderer v2 to avoid changing
persisted audit inputs during replay. No provider call or migration is required.
