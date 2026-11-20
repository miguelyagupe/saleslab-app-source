# SalesLab

> Portfolio source snapshot prepared on 20 November 2026 from the current application and API files. Earlier private commit history is not included. The live service is deployed from the original development repository, not this snapshot.

SalesLab is a financial analysis application with a public website, a signed-in web app, and a Python API. This repository contains the application and API; the separate `saleslab-website` repository contains the marketing site.

- Application: https://app.saleslab.cc
- Website: https://saleslab.cc
- API liveness and deployed revision: https://web-production-74559.up.railway.app/health

## What it does

The application accepts structured financial data, validates and maps it, computes traceable metrics, and prepares analysis and reports. The newer financial upload path includes a deterministic preview before generation, explicit source mappings, durable jobs, and approved report output. That path is a **restricted pilot** controlled by server flags and workspace permissions. Its presence in this repository or in the API route list does not establish that every visitor can use it.

The implementation uses FastAPI, PostgreSQL, Anthropic model calls for the analyst and critic stages, and a browser frontend. Vercel serves the frontend; Railway runs the API. Calculations and quality gates are separate from generated language. See [the deployment guide](DEPLOY.md) and [financial product contract](docs/saleslab-v2/FINANCIAL_UPLOAD_PRODUCT.md).

## Current evidence and limits

As checked on 20 November 2026, the public app and website returned HTTP 200. The public API health endpoint reported revision `0bd4fa73b240dd1278a9e5d3d7d08e1878968a21`. The financial sample and capabilities routes required authentication. This verifies a deployed application and API revision, **not** an end-to-end authenticated pilot, report quality, customer adoption, or commercial results.

The [public-data pilot guide](docs/saleslab-v2/FINANCIAL_PILOT_ACCEPTANCE.md) describes a reproducible example based on selected published financial figures. It explicitly leaves live narrative review and wider rollout pending. Use only approved public or synthetic data in demonstrations.

## Portfolio use

The project was conceived and directed by the owner, with AI assistance during development. A recruiter can view the public app, but needs a short reviewed walkthrough or authorized test access to see the signed-in financial flow. The source and deployment notes provide supporting technical evidence.

A useful walkthrough should show the input file, validation and mapping, an actual preview, and the resulting report only if that generation was verified. Keep account details, credentials, workspace identifiers and any customer records out of screenshots and recordings.

## Repository map

- `main.py`, `auth.py`, `database.py`: API entry point, authentication and persistence.
- `saleslab_v2/`: financial domain, normalization, metrics, quality gates, analyst, audit, reports and product integration.
- `index.html`, `financial-v2.js`: application frontend.
- `tests/`: automated checks.
- `docs/saleslab-v2/`: product contracts, implementation history and pilot guidance.
- `DEPLOY.md`: release and verification procedure.

No license file is present. Public access to this repository does not grant permission to reuse its code.
