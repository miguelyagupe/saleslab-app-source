# SalesLab V2 — Planning Source of Truth

## Status

This directory contains the approved planning baseline for rebuilding the analytical core of SalesLab V2.

The documents are implementation directions, not evidence that the described components already exist. Before changing code, Codex must inspect the current repository and explicitly distinguish:

- verified current-state facts;
- planned V2 behavior;
- assumptions that still require confirmation.

## Reading order

1. `01_PRODUCT_ARCHITECTURE.md`
2. `02_DOMAIN_CONTRACTS.md`
3. `03_FINANCIAL_MODULE_SPECIFICATION.md`
4. `04_IMPLEMENTATION_PLAN_F01_F05.md`

If two documents conflict, stop and report the conflict before implementation. Do not silently choose one.

## Repositories

- Target: `miguelyagupe-cyber/saleslab-api`
- Base branch: `master`
- Pattern reference: `anthropics/financial-services`

The Anthropic repository is a source of architectural and agent-design patterns. It is not code to copy blindly into SalesLab.

## Non-negotiable constraints

- Preserve the currently functional authentication, workspaces, billing/Stripe, quotas, database and Railway deployment behavior.
- Build V2 in parallel with the legacy analytical pipeline, behind explicit feature flags.
- Use a modular monolith unless verified repository evidence justifies a different decision.
- Implement the deterministic foundation before model-driven agents.
- Do not add model calls in F-01 through F-05.
- Keep calculations, reconciliation, sufficiency gates, audit evidence and report values traceable and testable.
- Keep language separate from locale and support `pt`, `en`, `es` and `fr`.
- Use additive, reversible database migrations.
- Do not replace legacy endpoints or expose V2 reports to customers during F-01 through F-05.
- Never invent repository state, business rules, formulas, schemas or acceptance criteria that are not supported by code or these documents.

## Required workflow

1. Perform a read-only readiness audit of the current target repository.
2. Compare repository evidence with these documents and list any mismatch or unresolved decision.
3. Produce the proposed file-level change map, migrations, tests and feature flags for the next slice.
4. Implement one slice at a time, beginning with F-01.
5. Validate each slice before moving to the next.
6. Use small, reviewable pull requests and preserve rollback paths.

## Initial Codex instruction

> Read every file in `docs/saleslab-v2/` in the stated order. Then inspect the current `saleslab-api` repository read-only. Do not implement yet. Produce a readiness report that separates verified repository facts, V2 requirements, conflicts, assumptions and blockers. Include the exact file-level plan for F-01, the additive migration strategy, test strategy, feature-flag boundary and rollback approach. Use `anthropics/financial-services` only as a pattern reference and identify every pattern you propose to adapt. Do not assume details you have not verified.

## Lifecycle

Keep these documents versioned with the implementation. When V2 is complete, archive or replace them with durable architecture documentation rather than deleting the decision history.
