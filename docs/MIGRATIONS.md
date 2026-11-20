# SalesLab migration operations

## Scope and safety model

Alembic owns schema changes committed after the F-00 migration foundation. The
legacy schema predates Alembic and remains owned by the existing application
startup until a separately reviewed migration changes that behavior.

Revision `20260727_0001` is an **empty baseline marker**. It does not create,
alter, rename, or drop legacy tables. Stamping a database means only that an
operator has inspected it and recorded that it is an accepted legacy starting
point; `stamp` does not reconcile schema drift.

Never run the inspection, stamp, upgrade, or downgrade commands with production
credentials from a developer machine. Railway production adoption requires an
authorized operator, a backup, a maintenance plan, and review of the generated
schema report.

## Configuration

Every command obtains connection details from `DATABASE_URL`. No database URL or
credential belongs in `alembic.ini`.

Both Railway-style URLs and SQLAlchemy async URLs are accepted:

```bash
export DATABASE_URL='postgresql://user:password@host:5432/database'
# or postgresql+asyncpg://user:password@host:5432/database
```

## Read-only inspection

The inspector compares the registered SQLAlchemy legacy metadata with the
authorized PostgreSQL schema, reports the signals left by the three manual SQL
scripts, and reports `alembic_version`. It runs catalog `SELECT` statements only.

```bash
DATABASE_URL='postgresql://...' \
  python scripts/inspect_database_schema.py \
  --output schema-report.json
```

Review at least:

1. `comparison.missing_tables`;
2. `comparison.column_differences`;
3. `comparison.unexpected_tables`;
4. every entry in `manual_migration_signals`;
5. `alembic.version_table_present` and `alembic.versions`.

Each entry in `manual_migration_signals` has one of four states:

| State | Meaning | Operator action |
|---|---|---|
| `complete` | Every durable table, column definition, constraint, index and partial-index predicate created or altered by that script matches. | Continue the wider schema review. This state alone does not authorize a stamp. |
| `partial` | At least one expected object matches and at least one is absent. | Stop. Determine whether the script failed, was interrupted, or was only partly executed. |
| `not_applied` | None of the script's durable target changes is present. | Stop and establish whether the script is required for the deployed application before applying it in a controlled operation. |
| `inconsistent` | An expected object exists under the target identity but has a divergent definition, or is invalid/not ready. | Stop. Investigate the exact divergence; never overwrite it blindly. |

Every check includes an `object_id`, `object_type`, `result`, stable
`reason_code`, expected definition and observed definition. `missing` means the
expected object or alteration is absent. `divergent` means that an object is
present but its type, nullability, default, constraint target/action, index
columns/order, uniqueness, method, validity/readiness, or normalized predicate
does not match. For example, `INDEX_MISSING`, `CONSTRAINT_MISSING`,
`INDEX_COLUMNS_DIVERGENT`, and `INDEX_PREDICATE_DIVERGENT` identify different
recovery paths.

The quota migration also consolidates duplicate rows. Catalog inspection cannot
prove that historical data rewrite ran; this limitation is recorded under
`non_schema_effects` and requires separate data validation when adopting an
existing database.

Do not stamp while any manual migration is `partial` or `inconsistent`, while
an unexplained missing table/column or unknown Alembic revision remains, or
until a `not_applied` migration has been explicitly reconciled with the deployed
application version. Additional production-only objects are not automatically
wrong, but they must be identified and preserved.

## Empty database procedure

F-00 deliberately does not add legacy creation DDL to Alembic. For a genuinely
empty, disposable or new database:

1. Configure the normal application environment.
2. Start the current application once so its existing `Base.metadata.create_all()`
   startup initializes the legacy schema.
3. Stop the application.
4. Run the read-only inspector and review the report.
5. Stamp the accepted schema:

   ```bash
   alembic -c alembic.ini stamp 20260727_0001
   ```

6. Verify:

   ```bash
   alembic -c alembic.ini current
   python scripts/inspect_database_schema.py --output schema-report-after.json
   ```

This preserves current startup behavior. A later, separately approved slice may
move full empty-database provisioning into Alembic.

## Existing legacy database procedure

For Railway or any legacy PostgreSQL database:

1. Confirm the exact application/environment and take a restorable backup.
2. Run `scripts/inspect_database_schema.py` read-only with an authorized account.
3. Compare the report with the deployed application version and the three files
   under `migrations/`.
4. Resolve every partial manual migration or unexplained drift separately. Do
   not edit the empty baseline to make production match local models.
5. Confirm no unknown `alembic_version` exists.
6. Schedule the adoption operation and run:

   ```bash
   alembic -c alembic.ini stamp 20260727_0001
   ```

7. Run `alembic current` and the read-only inspector again.
8. Smoke-test `/health`, auth, one authorized legacy analysis in a non-production
   environment, history, checkout setup, and Stripe webhook verification.

The definitive Railway baseline remains blocked until its real schema report is
reviewed. F-00 does not connect to or change Railway.

## Upgrade procedure

Before every future upgrade:

```bash
alembic -c alembic.ini current
alembic -c alembic.ini heads
alembic -c alembic.ini upgrade head --sql > planned-upgrade.sql
```

Review the SQL, test upgrade and downgrade against a disposable PostgreSQL clone,
take a backup, deploy application code with new behavior disabled, and only then
run `alembic -c alembic.ini upgrade head` in the authorized environment.

`--sql` is planning output. Asyncpg-specific parameter rendering may require an
online dry run for revisions whose DDL cannot be represented safely offline.

## Rollback and recovery

Application rollback and schema downgrade are separate decisions:

1. Disable the feature that uses the new schema.
2. Roll back the application artifact.
3. Preserve database evidence and logs.
4. Restore from backup for destructive or partially applied DDL; do not improvise
   reverse SQL in production.
5. Run `alembic downgrade <previous-revision>` only if that exact downgrade was
   tested and no active application depends on the newer schema.
6. Re-run the inspector and smoke tests.

Downgrading the empty baseline to `base` changes no legacy object. Operators
normally leave an adopted database stamped rather than removing its history.

## Disposable PostgreSQL validation

The integration suite rejects remote hosts and requires a database name containing
`test`, `f00`, or `disposable`:

```bash
export TEST_DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:5432/saleslab_f00_test'
python -m unittest tests.test_alembic_foundation -v
```

The suite creates and drops legacy tables in that database. Never point
`TEST_DATABASE_URL` at staging or production.

## Railway-specific controls

- Do not add `alembic upgrade head` to the web startup command: multiple replicas
  could race during deployment.
- Use a single authorized release/pre-deploy job after backups and schema review.
- Keep `Procfile` unchanged until the Railway release mechanism is confirmed.
- Deploy additive schema before enabling code that depends on it.
- Treat the Railway schema report as sensitive operational metadata; do not commit
  reports containing names or details that should remain private.
- A failed adoption must stop before any future upgrade. `stamp` cannot repair
  drift and must never be used to conceal a failed revision.
