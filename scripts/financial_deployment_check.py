"""Check the backend release and financial routes using public GET requests only."""

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saleslab_v2.deployment import assess_deployment, revision


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_backend(base_url, expected_revision, *, opener=None):
    url = urlsplit(base_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or any(ord(c) < 33 for c in base_url)
    ):
        raise ValueError(
            "Use an HTTPS API base URL without credentials, query or fragment."
        )
    if revision(expected_revision) is None:
        raise ValueError(
            "Use the full 40-character commit SHA expected on the backend."
        )
    opener = opener or build_opener(NoRedirect())

    def read(path):
        request = Request(
            base_url.rstrip("/") + path,
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
        )
        try:
            with opener.open(request, timeout=15) as response:
                content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise ValueError(
                    "The backend response exceeded the deployment-check limit."
                )
            return json.loads(content)
        except HTTPError as exc:
            raise ValueError(f"Backend {path} returned HTTP {exc.code}.") from None
        except (URLError, TimeoutError, OSError):
            raise ValueError(
                "The backend could not be reached. Check its deployment and URL."
            ) from None
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError(
                "The backend returned invalid JSON. Check its deployment and URL."
            ) from None

    return assess_deployment(read("/health"), read("/openapi.json"), expected_revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    try:
        result = check_backend(args.base_url, args.expected_revision)
    except (ValueError, TypeError) as exc:
        result = {"status": "error", "message": str(exc), "rollout_authorized": False}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
