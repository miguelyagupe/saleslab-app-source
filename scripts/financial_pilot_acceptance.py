"""Offline by default. Live generation requires --generate and an explicit key."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saleslab_v2.product.pilot import offline_acceptance
from saleslab_v2.product.pilot_client import PilotFailure, PilotHTTP, run_pilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--generate", action="store_true")
    group.add_argument("--job-id")
    parser.add_argument("--attempt-key")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not args.base_url and (args.generate or args.job_id or args.attempt_key):
        parser.error("Live options require --base-url.")
    try:
        if not args.base_url:
            result = offline_acceptance()
        else:
            transport = PilotHTTP(
                args.base_url, os.environ.get("SALESLAB_PILOT_TOKEN", "")
            )
            result = run_pilot(
                transport,
                generate=args.generate,
                attempt_key=args.attempt_key,
                job_id=args.job_id,
                timeout=args.timeout,
            )
    except (PilotFailure, ValueError, KeyError, TypeError) as exc:
        # Do not print transport exceptions, tokens, request bodies or provider text.
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": (
                        str(exc)
                        if isinstance(exc, PilotFailure)
                        else "Pilot acceptance failed. Check API access, deployment version and the documented input options; reuse the same attempt key when retrying."
                    ),
                    "rollout_authorized": False,
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"pass", "preview_pass"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
