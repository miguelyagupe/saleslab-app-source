# Public-data pilot acceptance

This slice validates the existing product path using six selected figures from Jerónimo Martins' 2024 consolidated financial statements, with 2023 comparatives. It adds a curated CSV, explicit proposed mappings, an in-app example loader, authenticated canonical JSON export and an operator command. It does not activate production flags, supply credentials, authorize rollout or certify live model narrative quality.

## Source and interpretation

Primary sources, checked on 2026-10-22:

- [Consolidated income statement](https://reports.jeronimomartins.com/annual-report/2024/financial-statements/consolidated-financial-statements/consolidated-income-statement-by-functions.html)
- [Consolidated cash flow statement](https://reports.jeronimomartins.com/annual-report/2024/financial-statements/consolidated-financial-statements/consolidated-cash-flow-statement.html)

| Figure (€ million as published) | 2024 | 2023 | Mapping |
| --- | ---: | ---: | --- |
| Sales and services | 33,464 | 30,608 | revenue |
| Profit before non-controlling interests | 606 | 773 | net_income |
| Operating cash flow after tax | 1,654 | 2,025 | cfo |

The result uses consolidated profit, including non-controlling interests, rather than the parent shareholders' 599/756. CFO uses the operating cash flow statement, not management's alternative cash flow indicator. The CSV preserves the published labels and amounts; proposed concept mappings and source-row references are explicit. Source language and number locale are English/en-GB, currency EUR, scale millions. Annual periods end in December; 2023 is comparative to 2024. These selected figures do not cover the full statements, a balance-sheet reconciliation, organic growth, causality, forecasts, recommendations or the six complete JM-FIN business cases in the broader specification.

The checked-in dataset is `saleslab_v2/product/samples/jm-2024.csv`, version `jm-public-2024-v1`. Its SHA-256 is pinned in `product/pilot.py`; changed bytes fail rather than silently replacing the reference. Golden values are independent rational expectations: revenue and CFO in base euros, net margin `60600/33464`, reported revenue growth `285600/30608`. Division checks allow only the numerical error of the engine's existing 100-digit arithmetic; no business materiality threshold is introduced.

## In the application

An eligible pilot owner/manager can choose **Carregar exemplo real · JM 2024**. The original labels, proposed mappings, source links and basis explanation are visible. Loading and previewing consume no quota and make no model call. The user validates the file and reviews the normalized figures before the existing generation button can submit a job. Switching files or starting a new analysis clears the example's mappings and source attribution. The existing explicit generation flow publishes only an approved report and charges one analysis.

`GET /financial-v2/sample` uses the same upload eligibility check as preview. `GET /financial-v2/reports/{run_id}/json` returns the exact persisted `ReportResult`, including its canonical Decimal tags and calculation traces, after the same workspace authorization and compiler replay checks as HTML/PDF. It uses private/no-store headers and consumes no quota. Published JSON remains readable when new uploads are disabled, provided the core report flags and authorization remain valid.

## Operator checks

First verify the deployed backend revision and financial routes with `scripts/financial_deployment_check.py` as described in [DEPLOY.md](../../DEPLOY.md). A healthy legacy API or a successful frontend preview is insufficient. Database schema adoption/upgrade and workspace/model configuration must also be reviewed before live generation.

Run the deterministic public-data check without credentials, database writes or model calls:

```bash
python scripts/financial_pilot_acceptance.py
```

For a deployed pilot, configure the flags, model identifiers and workspace allowlist described in `FINANCIAL_UPLOAD_PRODUCT.md`. Provide an existing session token through `SALESLAB_PILOT_TOKEN`; never commit it or put it in a URL. With `--base-url https://YOUR_API`, the command checks capabilities, the deployed dataset version and preview. This is read-only and does not enqueue work:

```bash
python scripts/financial_pilot_acceptance.py --base-url https://YOUR_API
```

Live generation is opt-in and can incur provider costs and consume one product analysis when approved. It requires an explicit UUID attempt key so retries can reuse the same job:

```bash
python scripts/financial_pilot_acceptance.py --base-url https://YOUR_API --generate --attempt-key YOUR_UUID
```

The command polls for at most `--timeout` seconds (default 600, maximum 1800). HTTP calls have a 15-second timeout and bounded responses. A pending result includes its job ID; it does not cancel or resubmit the job. Resume using `--job-id YOUR_JOB_UUID`, which performs only reads and can read published reports after upload activation is disabled. HTTP redirects are refused so the session token is not forwarded to another origin. Transport errors are sanitized and no request or provider content is printed.

For a completed job, acceptance checks the approved canonical JSON against the source-bound expected values, period, scope, currency, source hash and capability restrictions, then downloads HTML/PDF and records their SHA-256 hashes. Exit 0 means the selected offline, preview or report checks passed; 2 means work remains pending; 1 means a check failed. All receipts retain `semantic_quality=not_certified` and `rollout_authorized=false`. A human still needs to inspect the actual live narrative, and customer-data acceptance remains separate. No live deployment, provider call or customer-data access is implied by CI using recording providers.
