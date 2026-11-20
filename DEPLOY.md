# SalesLab deployment

The application has two deployments: Vercel serves the frontend and Railway runs the Python API with PostgreSQL. A green Vercel preview does not establish which backend revision is running. Verify both before the financial pilot.

## Railway backend

Inspect the existing service's source configuration: repository `miguelyagupe/saleslab-api`, intended branch/revision, repository root, deployment logs and any start-command override. The checked-in `Procfile` starts:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

`main.py` is the entry point. Instructions to copy `api.py`, run `uvicorn api:app` or authenticate with `ACCESS_CODE` belong to the old application and must not be used for the current release. Redeploying must use the reviewed revision; the existence of a merged PR alone does not prove that Railway deployed it.

The existing backend requires `DATABASE_URL`, `ANTHROPIC_API_KEY`, `JWT_SECRET` and `ADMIN_SECRET`. The two application secrets must each contain at least 32 characters. Preserve the configured secrets. `ALLOWED_ORIGIN` is the comma-separated frontend-origin allowlist. Existing Google authentication, email and billing configuration remains managed in the service; never copy secret values into the frontend or repository.

Before enabling V2 persistence, inspect the database and its Alembic state using the operator procedure in [docs/MIGRATIONS.md](docs/MIGRATIONS.md). Review schema drift, backups and the upgrade plan in the authorized environment. Do not add migrations to the web startup command. This guide does not establish that the production schema has been adopted or upgraded.

## Verify the actual backend release

`GET /health` remains a public liveness check. New releases also return a full `revision` from Railway's [Git deployment variable](https://docs.railway.com/variables/reference), `RAILWAY_GIT_COMMIT_SHA`. An absent or invalid value returns `null`; it never falls back to a guessed version. The response uses `Cache-Control: no-store` and contains no model settings, credentials or workspace identifiers.

Run this from a checkout of the exact revision expected on Railway (for a merged PR, use the resulting merge commit):

```bash
python scripts/financial_deployment_check.py \
  --base-url https://web-production-74559.up.railway.app \
  --expected-revision "$(git rev-parse HEAD)"
```

The command makes only public GET requests to `/health` and `/openapi.json`. It exits 0 only when the service is healthy, its full revision matches, and all financial-product routes have the expected methods. A healthy older API, missing revision, wrong SHA, missing routes, redirects and invalid responses cannot pass. Exit 1 means blocked/error; the JSON receipt shows the missing routes and revision mismatch. This check needs no session token, database credentials or model calls. Route presence alone does not prove schema readiness, worker operation or authenticated pilot eligibility.

## Vercel frontend and authenticated pilot

Publish `index.html` and `financial-v2.js` together using the existing Vercel project. Confirm `BACKEND_URL` in `index.html` points to the intended Railway service, and that its origin is allowed by the backend. Validate desktop/mobile login and the financial interface against that same backend.

After the public deployment check passes and the database state is reviewed, configure the restricted workspace and models using [FINANCIAL_UPLOAD_PRODUCT.md](docs/saleslab-v2/FINANCIAL_UPLOAD_PRODUCT.md). Restart when enabling the upload worker. Use the existing owner/manager session for the no-generation preview check in [FINANCIAL_PILOT_ACCEPTANCE.md](docs/saleslab-v2/FINANCIAL_PILOT_ACCEPTANCE.md), then run the explicitly requested live generation with a stable attempt key. Inspect its actual narrative and approved HTML/PDF before deciding on rollout.

Rollback application code and database schema as separate operations. Disable new financial uploads first when appropriate; published-report reads require the underlying report flags and workspace authorization described in the product guide.

## Static frontend release fingerprints

After changing `financial-v2.js`, run `python scripts/sync_frontend_assets.py`
before committing. It updates the script URL in `index.html` with the exact
SHA-256 of the JavaScript bytes. Run with `--check` to detect a stale URL without
writing. The release regression test independently verifies this binding.

This prevents a newly loaded HTML page from reusing a previously cached script.
The public domain was observed serving a four-hour JavaScript cache lifetime via
Cloudflare despite the Vercel production alias already pointing to the correct
merge. HTML and the script also request revalidation in `vercel.json`; external
CDN settings may override these headers, so the content-specific URL is the
primary release safeguard. Already-open pages retain their loaded code until
navigation/reload. Do not purge user storage or sessions as part of a release.
