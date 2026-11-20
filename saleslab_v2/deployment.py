"""Public release identity and read-only checks of the deployed API surface."""

import re
from collections.abc import Mapping

FINANCIAL_ROUTES = {
    "/financial-v2/capabilities": "get",
    "/financial-v2/sample": "get",
    "/financial-v2/history": "get",
    "/financial-v2/preview": "post",
    "/financial-v2/generate": "post",
    "/financial-v2/jobs/{job_id}": "get",
    "/financial-v2/reports/{run_id}/{channel}": "get",
}


def revision(value):
    return (
        value.lower()
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value)
        else None
    )


def health_payload(values: Mapping[str, str]):
    # Railway supplies this for GitHub deployments. Never return arbitrary env values.
    return {
        "status": "ok",
        "service": "SalesLab API v3",
        "revision": revision(values.get("RAILWAY_GIT_COMMIT_SHA")),
    }


def assess_deployment(health, schema, expected_revision):
    expected = revision(expected_revision)
    if expected is None:
        raise ValueError(
            "Use the full 40-character commit SHA expected on the backend."
        )
    if not isinstance(health, dict) or not isinstance(schema, dict):
        raise TypeError("The backend must return JSON objects for health and OpenAPI.")
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        paths = {}
    missing = [
        f"{method.upper()} {path}"
        for path, method in FINANCIAL_ROUTES.items()
        if not isinstance(paths.get(path), dict)
        or not isinstance(paths[path].get(method), dict)
    ]
    observed = revision(health.get("revision"))
    checks = {
        "health_ok": health.get("status") == "ok"
        and health.get("service") == "SalesLab API v3",
        "revision_matches": observed == expected,
        "financial_routes_present": not missing,
    }
    return {
        "status": "pass" if all(checks.values()) else "blocked",
        "expected_revision": expected,
        "observed_revision": observed,
        "checks": checks,
        "missing_routes": missing,
        "authenticated_pilot_checked": False,
        "provider_calls": 0,
        "rollout_authorized": False,
    }
