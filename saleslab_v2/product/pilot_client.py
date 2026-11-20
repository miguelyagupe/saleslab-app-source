"""Opt-in HTTP acceptance; no credentials in arguments, output or redirects."""

import json
import time
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

from saleslab_v2.analyst.context import unpack

from .pilot import DATASET_ID, FACTS, GOLDENS, SOURCE_SHA256, check_metrics, sample


class PilotFailure(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PilotHTTP:
    def __init__(self, base_url, token):
        url = urlsplit(base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise PilotFailure(
                "Use an HTTPS API base URL without credentials, query or fragment."
            )
        if not token or any(ord(c) < 32 for c in token):
            raise PilotFailure("Set SALESLAB_PILOT_TOKEN to an existing session token.")
        self.base, self.token, self.opener = (
            base_url.rstrip("/"),
            token,
            build_opener(NoRedirect()),
        )

    def call(self, path, data=None, content_type=None):
        headers = {"X-Token": self.token}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.base + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                content = response.read(12_000_001)
                if len(content) > 12_000_000:
                    raise PilotFailure("The response exceeded the acceptance limit.")
                return content
        except HTTPError as exc:
            raise PilotFailure(
                f"Pilot API returned HTTP {exc.code}; no automatic resubmission was made."
            ) from None
        except (URLError, TimeoutError, OSError):
            raise PilotFailure(
                "The pilot API request failed; resume with the same attempt key or job ID."
            ) from None


def multipart(case, choices):
    boundary = "saleslab-" + uuid4().hex
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="options"\r\n\r\n'
            + json.dumps(choices)
            + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{case["filename"]}"\r\nContent-Type: text/csv\r\n\r\n'
        ).encode()
        + case["content"].encode()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, "multipart/form-data; boundary=" + boundary


def verify_report(report, run_id):
    observations = [
        unpack(r["observation"]) for r in report.get("metric_observations", [])
    ]
    metrics = check_metrics(observations)
    decisions = {
        d["capability"]: d["decision_status"]
        for d in report.get("capability_decisions", [])
    }
    sources = report.get("source_index", [])
    valid = (
        report.get("run_id") == str(run_id)
        and report.get("module") == "financial"
        and metrics["pass"]
        and report.get("audit_result", {}).get("status")
        in {"approved", "approved_with_warnings"}
        and len(sources) == 1
        and sources[0].get("content_hash") == SOURCE_SHA256
        and decisions.get("calculate") == "allowed"
        and decisions.get("compare") == "allowed"
        and decisions.get("forecast") == "blocked"
        and decisions.get("recommend_material") == "blocked"
        and not report.get("forecasts")
        and not report.get("recommendations")
        and all(
            r.get("scope") == "consolidated"
            and not r.get("dimensions")
            and r.get("period", {}).get("label") == "2024"
            for r in observations
        )
    )
    return {"pass": bool(valid), "metrics": metrics}


def run_pilot(
    transport,
    *,
    generate=False,
    attempt_key=None,
    job_id=None,
    timeout=600,
    clock=time.monotonic,
    sleep=time.sleep,
):
    if not 1 <= timeout <= 1800 or (generate and job_id):
        raise PilotFailure(
            "Use a timeout of 1–1800 seconds and choose generation or job resume."
        )
    if generate and not attempt_key:
        raise PilotFailure("Generation requires an explicit UUID attempt key.")
    if attempt_key:
        attempt_key = str(UUID(attempt_key))
    if job_id:
        job_id = str(UUID(job_id))
    case = sample()
    base = {
        "dataset_id": DATASET_ID,
        "source_sha256": SOURCE_SHA256,
        "semantic_quality": "not_certified",
        "rollout_authorized": False,
    }

    def read(path):
        return json.loads(transport.call(path))

    if not job_id:
        if not read("/financial-v2/capabilities").get("enabled"):
            raise PilotFailure(
                "This authenticated workspace is not enabled for the pilot."
            )
        remote = read("/financial-v2/sample")
        if (
            remote.get("dataset_id") != DATASET_ID
            or remote.get("sha256") != SOURCE_SHA256
            or remote.get("content") != case["content"]
            or remote.get("choices") != case["choices"]
        ):
            raise PilotFailure(
                "The deployed public dataset differs from this acceptance version."
            )
        body, content_type = multipart(case, case["choices"])
        preview = json.loads(
            transport.call("/financial-v2/preview", body, content_type)
        )
        rows = {r["location"]: r for r in preview.get("rows", [])}
        if (
            not preview.get("ready")
            or preview.get("source_hash") != SOURCE_SHA256
            or set(preview.get("planned_metrics", [])) != set(GOLDENS)
            or len(rows) != len(FACTS)
            or any(
                rows.get(f[0], {}).get("value") != str(int(f[4]) * 1000000)
                or rows.get(f[0], {}).get("currency") != "EUR"
                for f in FACTS
            )
        ):
            raise PilotFailure(
                "The deployed preview differs from the source-bound reference figures."
            )
        if not generate:
            return {
                **base,
                "status": "preview_pass",
                "full_report_checked": False,
                "provider_calls": 0,
            }
        choices = {
            **case["choices"],
            "preview_hash": preview["preview_hash"],
            "attempt_key": attempt_key,
        }
        body, content_type = multipart(case, choices)
        queued = json.loads(
            transport.call("/financial-v2/generate", body, content_type)
        )
        job_id = str(UUID(queued["job_id"]))
    deadline = clock() + timeout
    while True:
        result = read("/financial-v2/jobs/" + job_id)
        if result.get("status") != "pending":
            break
        remaining = deadline - clock()
        if remaining <= 0:
            return {
                **base,
                "status": "pending",
                "job_id": job_id,
                "attempt_key": attempt_key,
                "full_report_checked": False,
            }
        sleep(min(2, remaining))
    if result.get("status") != "completed":
        raise PilotFailure(
            "The pilot job did not publish an approved report; inspect its authenticated status."
        )
    run_id = str(UUID(result["run_id"]))
    report = read(f"/financial-v2/reports/{run_id}/json")
    checked = verify_report(report, run_id)
    if not checked["pass"]:
        raise PilotFailure(
            "The approved report differs from the public-data acceptance expectations."
        )
    html = transport.call(f"/financial-v2/reports/{run_id}/html")
    pdf = transport.call(f"/financial-v2/reports/{run_id}/pdf")
    if not html or not pdf.startswith(b"%PDF"):
        raise PilotFailure("The approved report artifacts are unavailable.")
    return {
        **base,
        "status": "pass",
        "job_id": job_id,
        "run_id": run_id,
        "full_report_checked": True,
        "metrics": checked["metrics"],
        "html_sha256": sha256(html).hexdigest(),
        "pdf_sha256": sha256(pdf).hexdigest(),
    }
