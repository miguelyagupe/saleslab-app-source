# F-05 quality gate contract

F-05 evaluates coverage and authorizes the nine financial capabilities before
downstream analysis. It does not expose a new public analysis pipeline.

## Hedge request profile

The caller selects `financial_hedge_v1` for a hedge request. The default
`financial_v1` retains generic financial requirements. `requirement_for` rejects
unknown profiles and adds fixed/variable debt mix, currency, maturity and current
hedge coverage to impact quantification and material recommendation requirements
for the hedge profile. The decision engine enforces these requirements even if
the supplied coverage profile has a narrower required-input set. No manually
constructed issue is necessary to block a hedge with missing exposure data.

```python
profile = "financial_hedge_v1"
required = requirement_for(Capability.QUANTIFY_IMPACT, profile)
coverage = assess_coverage(
    run_id, required.required_inputs, present_inputs, profile=profile
)
decisions = decide_capabilities(run_id, coverage)
```

Profile identity is recorded by the existing rule-version fields:
`financial-quality-v1` or `financial-quality-hedge-v1`. Persistence recomputes the
matrix using that policy and rejects decisions supplied for a different profile.
The existing scoped unique keys include rule version, so both policies can be
audited independently without a schema migration.

Issues remain run-scoped and apply to every policy for that run. Persistence
loads existing run issues before validating a new gate, so omitting an existing
blocker cannot authorize another profile. A new blocker that would invalidate
an older immutable decision is rejected as an idempotency conflict; changed
evidence requires a new run. A workspace-scoped `FOR NO KEY UPDATE` run-row lock
serializes writers across policy versions while remaining compatible with
parent-key locks from earlier stage writes in the same transactions.

## Canonical coverage

`assess_coverage` is the single calculator for coverage partitions, quality and
score components. Input identifiers are nonempty strings. Collections represent
sets: ordering and duplication have no semantic meaning. All output collections
are sorted and deduplicated. Ambiguous inputs are missing, never covered.

`validate_coverage` reconstructs the assessment from required, present and
ambiguous inputs. It rejects inconsistent missing inputs, quality, scores,
out-of-profile fields, unsupported modules and unsupported policy versions.
It returns a canonical assessment preserving its original ID and timestamp.
An exported frozen dataclass is transport data, not proof of validity.

Both `decide_capabilities` and `persist_quality_gate` validate coverage before
using it. Decision run identity must match coverage. Persistence additionally
checks the owning workspace, the stored run module, all nine decisions and their
recomputed semantics before inserting any gate row.

## Replay and transactions

Equivalent input permutations and duplicate identifiers produce equivalent
coverage and decisions. Persistence stores canonical collections and compares
the canonical content on replay. Changed coverage content remains a conflict.
Issue and decision iterators are materialized once so validation and writing
observe the same data. The existing savepoint makes gate writes atomic and
keeps the caller's session usable after rejected input or an incompatible replay.

Transient `issue_id` values must identify exactly one canonical semantic payload
within a gate. Equivalent duplicates are deduplicated; conflicting reuse is
rejected before either deriving decisions or writing rows. Validation, stable
identity hashing and persisted issue content share `canonical_issue_payload`.

This correction needs no migration: migration 0006 already stores these fields.
Revisions 0001–0006 and the legacy schema remain unchanged.

## Acceptance evidence

- `tests/test_saleslab_v2_f05_invariants.py`: coverage partitions across all 256
  states of four inputs; order/duplicate invariance; each derived field and every
  capability; stale complete assessments; run/module/version rejection; iterator
  equivalence.
- `tests/test_saleslab_v2_f05_postgres.py`: canonical concurrent and sequential
  replay; independent rejection of inconsistent coverage before first insert
  and on replay; unchanged row counts and usable sessions after rejection;
  existing blocker, rollback, scope and concurrency scenarios; migration
  0005 → 0006 → 0005 → 0006 and ORM/schema alignment.
- The repository PostgreSQL 16 workflow runs the full suite and fails on skips.
  A local run without a database is supplementary evidence only.
- `tests/test_saleslab_v2_f05_hedge_identity.py`: hedge gates without manual
  blockers, each missing/ambiguous exposure input, complete hedge inputs,
  unaffected generic capabilities and conflicting transient issue-ID reuse.
  PostgreSQL tests also check profile isolation, generic-decision rejection,
  concurrent hedge replay, and evidence integrity after rejected identity reuse.
