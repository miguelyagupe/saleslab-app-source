"""Require a complete declared corpus; passing arithmetic is not rollout authorization."""

from copy import deepcopy

from saleslab_v2.domain.idempotency import semantic_hash

from .contracts import SHADOW_VERSION, InvalidShadow


class FinancialShadowCampaign:
    def __init__(self, runner):
        self.runner = runner

    async def execute(self, cases, *, dataset_version, required_case_ids, campaign_key):
        cases = tuple(c.snapshot() for c in cases)
        required = tuple(required_case_ids)
        ids = [c.case_id for c in cases]
        if (
            not cases
            or len(cases) > 50
            or not required
            or len(set(required)) != len(required)
            or len(ids) != len(set(ids))
            or set(ids) != set(required)
        ):
            raise InvalidShadow(
                "campaign must contain its entire declared corpus exactly once"
            )
        if (
            not isinstance(campaign_key, str)
            or not campaign_key
            or len(campaign_key) > 40
            or campaign_key != campaign_key.strip()
        ):
            raise ValueError("invalid campaign key")
        if any(c.dataset_version != dataset_version for c in cases):
            raise InvalidShadow("campaign dataset versions differ")
        if len({(c.request.workspace_id, c.request.user_id) for c in cases}) != 1:
            raise PermissionError("campaign must have one scoped workspace and actor")
        configuration = deepcopy(self.runner.configuration)
        manifest = {
            "shadow_version": SHADOW_VERSION,
            "dataset_version": dataset_version,
            "configurations": configuration,
            "cases": {
                c.case_id: semantic_hash(c.payload())
                for c in sorted(cases, key=lambda c: c.case_id)
            },
        }
        # Short hash keys remain within the per-case limit, even for long case IDs.
        results = []
        for c in sorted(cases, key=lambda c: c.case_id):
            if self.runner.configuration != configuration:
                raise InvalidShadow("campaign runner configuration changed")
            result = await self.runner.execute(
                c,
                attempt_key=f"{campaign_key}:{semantic_hash([manifest, c.case_id])[:40]}",
            )
            if result.payload["configurations"] != configuration:
                raise InvalidShadow("campaign result configuration differs")
            results.append(result)
        outcomes = [r.payload for r in results]
        return {
            "schema_version": "1",
            "manifest": manifest,
            "manifest_hash": semantic_hash(manifest),
            "case_run_ids": [str(r.run_id) for r in results],
            "cases": outcomes,
            "automated_checks": (
                "pass" if all(r["status"] == "pass" for r in outcomes) else "fail"
            ),
            "migration_differences_require_review": any(
                r["migration_differences_require_review"] for r in outcomes
            ),
            "semantic_quality": "not_certified",
            "rollout_authorized": False,
        }
