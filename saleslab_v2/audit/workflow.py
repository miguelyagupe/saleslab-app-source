"""Bounded continuation: audit → explicit revision → fresh independent audit."""

from uuid import UUID

from saleslab_v2.analyst.service import FinancialAnalyst

from .contracts import MAX_REVISIONS
from .service import FinancialAuditor


class FinancialReviewWorkflow:
    def __init__(self, session, settings, analyst_model, critic):
        self.session, self.settings = session, settings
        self.analyst_model, self.critic = analyst_model, critic

    async def execute(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        run_id: UUID,
        analyst_attempt_key="initial",
        review_key="initial",
    ):
        if (
            not isinstance(review_key, str)
            or not review_key
            or review_key != review_key.strip()
            or len(review_key) > 60
        ):
            raise ValueError("invalid financial review workflow key")
        current = analyst_attempt_key
        audits, revisions = [], []
        for turn in range(MAX_REVISIONS + 1):
            audit = await FinancialAuditor(
                self.session, self.settings, self.critic
            ).execute(
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                analyst_attempt_key=current,
                attempt_key=f"{review_key}:audit:{turn}",
            )
            audits.append(audit.payload)
            if audit.payload["status"] != "revision_required":
                break
            current = f"{review_key}:revision:{turn + 1}"
            revision = await FinancialAnalyst(
                self.session, self.settings, self.analyst_model
            ).execute(
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                attempt_key=current,
                revision_of=UUID(audit.payload["audit_id"]),
            )
            revisions.append(revision.payload)
            if revision.payload["status"] != "draft":
                break
        return {
            "run_id": str(run_id),
            "audits": audits,
            "revisions": revisions,
            "status": (
                revisions[-1]["status"]
                if revisions and revisions[-1]["status"] != "draft"
                else audits[-1]["status"]
            ),
            "report_published": False,
        }
