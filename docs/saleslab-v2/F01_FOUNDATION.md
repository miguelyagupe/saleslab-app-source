# F-01 foundation decisions

SalesLab V2 is an isolated modular-monolith package and is not imported by any
legacy endpoint. Its three additive tables retain authorization and lookup
fields relationally while immutable structured context is JSONB.

The domain starts internal runs at `received` so fixtures and repositories do
not pretend that quota was reserved. F-01 never reserves or consumes quota; a
failed retry is represented by a new run rather than reopening a failed run.
Runs normally require at least one existing source in the same workspace. The
domain's `allow_empty_sources` opt-in exists only for explicitly source-less
internal fixtures; it does not imply or simulate quota reservation.

Currency validation is deliberately **structural only**: exactly three uppercase
ASCII letters. It does not claim a complete ISO 4217 registry. Units likewise
retain caller-provided canonical code and family without inventing a taxonomy.

Run idempotency persists one deterministic SHA-256 request fingerprint. Its
canonical semantic payload includes `module`, `schema_version`,
`pipeline_version`, ordered `source_ids`, ordered `input_hashes`,
`analysis_language`, `report_language`, ordered `source_languages`, and
`accounting_context`. `format_locale` is presentation-only and is deliberately
excluded. Run/user IDs, status, timestamps, failure, legacy comparison, and the
workspace/purpose/key scope are also excluded because they are generated output,
operational state, or the unique-constraint lookup scope rather than request
semantics. The unique constraint remains `(workspace_id, idempotency_purpose,
idempotency_key)`; the fingerprint only validates equivalent reuse.
Critical terminology has no silent fallback when strict i18n is enabled. The
fixture is synthetic metadata only and contains no report, PDF, PII, or company
financial figures.

The immutable foundation migration `20260727_0002` follows `20260727_0001`.
F-01.1 adds the fingerprint through `20260727_0003`, whose frozen migration-local
canonicalizer safely backfills existing runs before enforcing `NOT NULL`; it
does not require `pgcrypto` or any extension. These additive migrations must
only be adopted on a real database after operational inspection and backup.
They were not run or stamped on Railway or production as part of F-01/F-01.1.
