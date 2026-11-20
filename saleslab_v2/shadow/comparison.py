"""Compare declared goldens, not free-form model judgments or guessed business thresholds."""

import re
from decimal import Decimal, localcontext

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.domain.normalization import canonical_decimal

from .contracts import SHADOW_VERSION


def legacy_number(value, *, unit, currency):
    """Only the existing legacy Portuguese display grammar is comparable."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\u00a0", " ")
    if unit == "percentage":
        if not text.endswith("%"):
            return None
        text = text[:-1].strip()
    elif unit == "monetary":
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            return None
        prefix = "€" if currency == "EUR" else currency + " "
        if not text.startswith(prefix):
            return None
        text = text[len(prefix) :].strip()
    else:
        return None
    if len(text) > 256 or not re.fullmatch(
        r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?", text
    ):
        return None
    return Decimal(text.replace(".", "").replace(",", "."))


def compare_case(case, legacy, v2, *, configurations, v2_duration_ms):
    checks = []

    def check(key, expected, actual, code):
        checks.append(
            {
                "check_id": key,
                "status": "pass" if expected == actual else "fail",
                "reason_code": code,
                "expected": expected,
                "actual": actual,
            }
        )

    check("v2/state", case.expected_v2_status, v2["workflow_status"], "shadow.v2_state")
    check(
        "legacy/state",
        case.expected_legacy_status,
        legacy["status"],
        "shadow.legacy_state",
    )
    quality = unpack(v2["checkpoint"]["quality"])
    decisions = {d["capability"]: d["decision_status"] for d in quality["decisions"]}
    for cap, expected in sorted(case.required_capabilities):
        check("capability/" + cap, expected, decisions.get(cap), "shadow.capability")
    issue_codes = {i["code"] for i in quality["issues"]}
    for code in sorted(set(case.expected_issue_codes)):
        check("issue/" + code, True, code in issue_codes, "shadow.expected_issue")
    if case.expected_audit_status is not None:
        check(
            "audit/status",
            case.expected_audit_status,
            (v2["audit"] or {}).get("status"),
            "shadow.audit_status",
        )
    metrics = {m["key"]: unpack(m["observation"]) for m in v2["checkpoint"]["metrics"]}
    baseline = (legacy.get("output") or {}).get("report", {})
    legacy_metrics = baseline.get("metrics", baseline.get("kpis", []))
    if not isinstance(legacy_metrics, list):
        legacy_metrics = []
    differences = []
    for expected in sorted(case.metrics, key=lambda e: e.step_key):
        metric = metrics.get(expected.step_key)
        want = {
            "value": canonical_decimal(expected.value),
            "unit": expected.unit,
            "currency": expected.currency,
        }
        actual = (
            None
            if metric is None
            else {
                "value": canonical_decimal(Decimal(metric["value"])),
                "unit": metric["unit"],
                "currency": metric["currency"],
            }
        )
        check("metric/" + expected.step_key, want, actual, "shadow.exact_metric")
        if expected.legacy_metric_id is None:
            differences.append(
                {
                    "step_key": expected.step_key,
                    "legacy_metric_id": None,
                    "comparison_status": "unsupported",
                    "reason_code": "shadow.legacy_metric_not_mapped",
                    "expected": want,
                    "v2": actual,
                    "legacy_value": None,
                    "legacy_minus_expected": None,
                    "absolute_tolerance": None,
                    "legacy_matches_expected": None,
                }
            )
            continue
        matches = [
            m
            for m in legacy_metrics
            if isinstance(m, dict) and m.get("metric_id") == expected.legacy_metric_id
        ]
        observed = (
            legacy_number(
                matches[0].get("value"), unit=expected.unit, currency=expected.currency
            )
            if len(matches) == 1
            else None
        )
        # Compare each implementation against the declared truth, separately.
        with localcontext() as ctx:
            ctx.prec = 2048
            delta = observed - expected.value if observed is not None else None
            matches_expected = (
                delta is not None and abs(delta) <= expected.legacy_absolute_tolerance
            )
        differences.append(
            {
                "step_key": expected.step_key,
                "legacy_metric_id": expected.legacy_metric_id,
                "comparison_status": (
                    "available" if observed is not None else "unavailable"
                ),
                "reason_code": "shadow.legacy_metric_comparison",
                "expected": want,
                "v2": actual,
                "legacy_value": (
                    canonical_decimal(observed) if observed is not None else None
                ),
                "legacy_minus_expected": (
                    canonical_decimal(delta) if delta is not None else None
                ),
                "absolute_tolerance": canonical_decimal(
                    expected.legacy_absolute_tolerance
                ),
                "legacy_matches_expected": matches_expected,
            }
        )
        # A legacy mismatch is a measured migration difference, not evidence the
        # V2 is wrong. It remains visible and always requires review.
    report = v2["report"]
    if v2["workflow_status"] == "completed":
        check("publication/exists", True, report is not None, "shadow.publication")
        check(
            "publication/approval",
            True,
            bool(
                report
                and report["audit_result"]["status"]
                in {"approved", "approved_with_warnings"}
            ),
            "shadow.publication_approval",
        )
    else:
        check("publication/absent", None, report, "shadow.blocked_publication")
    policy = (legacy.get("output") or {}).get("deterministic_policy")
    forecast = (policy if policy is not None else baseline).get("forecast_allowed")
    if type(forecast) is not bool:
        forecast = None
    policy_differences = {
        "legacy_forecast_allowed": forecast,
        "v2_forecast_decision": decisions.get("forecast"),
        "legacy_recommendation_count": (
            len(baseline["recommendations"])
            if isinstance(baseline.get("recommendations"), list)
            else None
        ),
        "v2_recommendation_count": len(report["recommendations"]) if report else None,
    }
    return {
        "schema_version": "1",
        "shadow_version": SHADOW_VERSION,
        "case_id": case.case_id,
        "dataset_version": case.dataset_version,
        "case_hash": semantic_hash(case.payload()),
        "configurations": configurations,
        "status": "pass" if all(c["status"] == "pass" for c in checks) else "fail",
        "checks": checks,
        "metric_differences": differences,
        "policy_differences": policy_differences,
        "measurements": {
            "legacy_duration_ms": legacy["duration_ms"],
            "v2_duration_ms": v2_duration_ms,
            "legacy_usage": (legacy.get("output") or {}).get("usage"),
            "v2_usage": None,
            "monetary_cost": None,
        },
        "migration_differences_require_review": legacy["status"] != "completed"
        or v2["workflow_status"] != "completed"
        or any(not d["legacy_matches_expected"] for d in differences)
        or (
            forecast is not None
            and forecast != (decisions.get("forecast") == "allowed")
        )
        or (
            report is not None
            and policy_differences["legacy_recommendation_count"] is not None
            and policy_differences["legacy_recommendation_count"]
            != policy_differences["v2_recommendation_count"]
        ),
        "comparison_scope": configurations["legacy"].get("mode", "external_runner"),
        "semantic_quality": "not_certified",
        "rollout_authorized": False,
    }
