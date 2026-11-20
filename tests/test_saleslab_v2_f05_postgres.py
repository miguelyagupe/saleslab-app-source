"""Live PostgreSQL 16 replay and concurrency coverage for F-05 quality gates."""

# ruff: noqa: E402

import asyncio
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("TEST_DATABASE_URL", "")
if os.environ.get("SALESLAB_REQUIRE_POSTGRES_TESTS") == "1" and not URL:
    raise RuntimeError("TEST_DATABASE_URL is required for F-05 PostgreSQL tests")
os.environ.setdefault("DATABASE_URL", URL or "postgresql://unused/f05_test")

import models  # noqa: F401
from database import Base
from models import User, Workspace
from saleslab_v2.domain.enums import LanguageCode, ModuleType, QualityLevel
from saleslab_v2.domain.idempotency import IdempotencyConflict
from saleslab_v2.domain.language import LanguageContext, LocaleTag
from saleslab_v2.domain.runs import AnalysisRun
from saleslab_v2.persistence.metadata import legacy_tables
from saleslab_v2.persistence.models import (
    CapabilityDecisionModel,
    CoverageAssessmentModel,
    DataQualityIssueModel,
)
from saleslab_v2.persistence.repositories import V2Repository
from saleslab_v2.quality.capabilities import decide_capabilities
from saleslab_v2.quality.coverage import assess_coverage
from saleslab_v2.quality.issues import blocking_issue


def async_url(value):
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def alembic(*args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": URL},
        check=True,
        capture_output=True,
        text=True,
    )


def make_run(workspace_id, user_id, module=ModuleType.FINANCIAL):
    return AnalysisRun(
        workspace_id,
        user_id,
        module,
        "1",
        "1",
        LanguageContext(
            (LanguageCode.PT,),
            LanguageCode.PT,
            LanguageCode.PT,
            LocaleTag.PT_PT,
        ),
        (),
        (),
        "f05-postgres",
        "quality-gate",
        allow_empty_sources=True,
    )


@unittest.skipUnless(
    URL, "TEST_DATABASE_URL is required for disposable PostgreSQL tests"
)
class F05PostgresTests(unittest.TestCase):
    async def policy_run(self):
        from saleslab_v2.quality.policies.financial_v1 import (
            HEDGE_REQUIREMENTS,
            REQUIREMENTS,
        )

        await self.reset()
        user_id, workspace_id = uuid4(), uuid4()
        async with self.sessions.begin() as session:
            session.add(
                User(id=user_id, email="f05-policy-run@example.test", password_hash="x")
            )
            session.add(Workspace(id=workspace_id, company_name="Policy Run"))
        run = make_run(workspace_id, user_id)
        async with self.sessions.begin() as session:
            await V2Repository(session, workspace_id).create_run(run)
        required = tuple(
            sorted(
                {
                    *HEDGE_REQUIREMENTS,
                    *(x for values in REQUIREMENTS.values() for x in values),
                }
            )
        )
        return run, workspace_id, required

    def test_run_blockers_apply_to_every_profile_in_both_write_orders(self):
        from saleslab_v2.domain.enums import Capability
        from saleslab_v2.quality.policies.financial_v1 import (
            DEFAULT_PROFILE,
            HEDGE_PROFILE,
        )

        async def scenario(first_profile, second_profile, scoped):
            run, workspace_id, required = await self.policy_run()
            issue = blocking_issue(
                "FIN-RUN-BLOCK",
                "financial",
                "run_block",
                (Capability.QUANTIFY_IMPACT,) if scoped else (),
            )
            first = assess_coverage(
                run.run_id, required, required, profile=first_profile
            )
            second = assess_coverage(
                run.run_id, required, required, profile=second_profile
            )
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                await repo.persist_quality_gate(
                    first, (issue,), decide_capabilities(run.run_id, first, (issue,))
                )
                with self.assertRaisesRegex(ValueError, "decisions do not match"):
                    await repo.persist_quality_gate(
                        second, (), decide_capabilities(run.run_id, second)
                    )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    1,
                )
                # Reconstructed evidence for the second profile maps back to the
                # stored run issue; new UUID4 values do not break replay.
                fresh = replace(issue, issue_id=uuid4())
                decisions = decide_capabilities(run.run_id, second, (fresh,))
                await repo.persist_quality_gate(second, (fresh,), decisions)
                self.assertFalse(
                    (await repo.persist_quality_gate(second, (fresh,), decisions))[2]
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    2,
                )
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    1,
                )
                rows = (await session.scalars(select(CapabilityDecisionModel))).all()
                self.assertEqual(len(rows), 2 * len(Capability))
                affected = [
                    r
                    for r in rows
                    if not scoped or r.capability == Capability.QUANTIFY_IMPACT.value
                ]
                self.assertTrue(all(r.decision_status == "blocked" for r in affected))
                self.assertTrue(
                    all(
                        r.reason_codes == ["blocking_issue_precedence"]
                        for r in affected
                    )
                )

        for first, second in (
            (DEFAULT_PROFILE, HEDGE_PROFILE),
            (HEDGE_PROFILE, DEFAULT_PROFILE),
        ):
            for scoped in (False, True):
                with self.subTest(first=first, scoped=scoped):
                    asyncio.run(scenario(first, second, scoped))

    def test_concurrent_policy_gates_cannot_introduce_stale_run_blockers(self):
        from saleslab_v2.domain.enums import Capability
        from saleslab_v2.quality.policies.financial_v1 import (
            DEFAULT_PROFILE,
            HEDGE_PROFILE,
        )

        async def scenario(blocked_profile, allowed_profile, scoped):
            run, workspace_id, required = await self.policy_run()
            issue = blocking_issue(
                "FIN-RACE-BLOCK",
                "financial",
                "race_block",
                (Capability.QUANTIFY_IMPACT,) if scoped else (),
            )
            barrier = asyncio.Barrier(2)

            async def persist(profile, issues):
                coverage = assess_coverage(
                    run.run_id, required, required, profile=profile
                )
                async with self.sessions.begin() as session:
                    # Model the parent-key lock acquired by earlier stage/FK
                    # writes in both outer transactions. Gate serialization
                    # must not deadlock while these compatible locks exist.
                    await session.execute(
                        text(
                            "SELECT id FROM analysis_runs_v2 WHERE id = :run_id FOR KEY SHARE"
                        ),
                        {"run_id": run.run_id},
                    )
                    await barrier.wait()
                    return await V2Repository(
                        session, workspace_id
                    ).persist_quality_gate(
                        coverage,
                        issues,
                        decide_capabilities(run.run_id, coverage, issues),
                    )

            outcomes = await asyncio.gather(
                persist(blocked_profile, (issue,)),
                persist(allowed_profile, ()),
                return_exceptions=True,
            )
            self.assertEqual(sum(not isinstance(x, Exception) for x in outcomes), 1)
            self.assertEqual(
                sum(isinstance(x, (ValueError, IdempotencyConflict)) for x in outcomes),
                1,
            )
            async with self.sessions.begin() as session:
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    1,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    len(Capability),
                )
                count = await session.scalar(
                    select(func.count(DataQualityIssueModel.id))
                )
                rows = (await session.scalars(select(CapabilityDecisionModel))).all()
                if count:
                    affected = [
                        r
                        for r in rows
                        if not scoped
                        or r.capability == Capability.QUANTIFY_IMPACT.value
                    ]
                    self.assertTrue(
                        all(r.decision_status == "blocked" for r in affected)
                    )
                else:
                    self.assertTrue(all(r.decision_status == "allowed" for r in rows))

        for first, second in (
            (DEFAULT_PROFILE, HEDGE_PROFILE),
            (HEDGE_PROFILE, DEFAULT_PROFILE),
        ):
            for scoped in (False, True):
                with self.subTest(first=first, scoped=scoped):
                    asyncio.run(
                        asyncio.wait_for(scenario(first, second, scoped), timeout=25)
                    )

    def test_new_policy_blockers_cannot_invalidate_an_existing_allowed_gate(self):
        from saleslab_v2.domain.enums import Capability
        from saleslab_v2.quality.policies.financial_v1 import (
            DEFAULT_PROFILE,
            HEDGE_PROFILE,
        )

        async def scenario(first_profile, second_profile, scoped):
            run, workspace_id, required = await self.policy_run()
            first = assess_coverage(
                run.run_id, required, required, profile=first_profile
            )
            second = assess_coverage(
                run.run_id, required, required, profile=second_profile
            )
            issue = blocking_issue(
                "FIN-NEW-POLICY",
                "financial",
                "new_policy",
                (Capability.QUANTIFY_IMPACT,) if scoped else (),
            )
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                await repo.persist_quality_gate(
                    first, (), decide_capabilities(run.run_id, first)
                )
                with self.assertRaisesRegex(
                    IdempotencyConflict, "run-scoped issues conflict"
                ):
                    await repo.persist_quality_gate(
                        second,
                        (issue,),
                        decide_capabilities(run.run_id, second, (issue,)),
                    )
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    1,
                )
                rows = (await session.scalars(select(CapabilityDecisionModel))).all()
                self.assertEqual(len(rows), len(Capability))
                self.assertTrue(all(row.decision_status == "allowed" for row in rows))

        for first, second in (
            (DEFAULT_PROFILE, HEDGE_PROFILE),
            (HEDGE_PROFILE, DEFAULT_PROFILE),
        ):
            for scoped in (False, True):
                with self.subTest(first=first, scoped=scoped):
                    asyncio.run(scenario(first, second, scoped))

    def test_hedge_policy_cannot_be_bypassed_by_generic_decisions_and_replays(self):
        from saleslab_v2.domain.enums import Capability, DecisionStatus
        from saleslab_v2.quality.policies.financial_v1 import (
            HEDGE_PROFILE,
            REQUIREMENTS,
        )

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f05-hedge@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="Hedge"))
            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            required = REQUIREMENTS[Capability.QUANTIFY_IMPACT]
            generic = assess_coverage(run.run_id, required, required)
            hedge = assess_coverage(
                run.run_id, required, required, profile=HEDGE_PROFILE
            )
            generic_decisions = decide_capabilities(run.run_id, generic)
            hedge_decisions = decide_capabilities(run.run_id, hedge)

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                with self.assertRaisesRegex(ValueError, "decisions do not match"):
                    await repo.persist_quality_gate(hedge, (), generic_decisions)
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    0,
                )
                await repo.persist_quality_gate(generic, (), generic_decisions)

            async def persist_hedge():
                value = assess_coverage(
                    run.run_id,
                    tuple(reversed(required)),
                    required,
                    profile=HEDGE_PROFILE,
                )
                async with self.sessions.begin() as session:
                    return await V2Repository(
                        session, workspace_id
                    ).persist_quality_gate(
                        value, (), decide_capabilities(run.run_id, value)
                    )

            first, replay = await asyncio.gather(persist_hedge(), persist_hedge())
            self.assertEqual(first[0].id, replay[0].id)
            self.assertEqual(sorted((first[2], replay[2])), [False, True])
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                # Generic decisions also cannot overwrite an existing hedge gate.
                with self.assertRaisesRegex(ValueError, "decisions do not match"):
                    await repo.persist_quality_gate(hedge, (), generic_decisions)
                self.assertFalse(
                    (await repo.persist_quality_gate(hedge, (), hedge_decisions))[2]
                )
                rows = (
                    await session.scalars(
                        select(CapabilityDecisionModel).where(
                            CapabilityDecisionModel.capability
                            == Capability.QUANTIFY_IMPACT.value
                        )
                    )
                ).all()
                self.assertEqual(
                    {r.rule_version: r.decision_status for r in rows},
                    {
                        generic.assessment_rule_version: DecisionStatus.ALLOWED.value,
                        hedge.assessment_rule_version: DecisionStatus.BLOCKED.value,
                    },
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    2,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    2 * len(Capability),
                )
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )

        asyncio.run(asyncio.wait_for(scenario(), timeout=20))

    def test_reused_issue_id_rejection_preserves_evidence_and_session_health(self):
        from saleslab_v2.domain.enums import Capability
        from saleslab_v2.persistence.repositories import _quality_issue_id

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(
                        id=user_id, email="f05-issue-id@example.test", password_hash="x"
                    )
                )
                session.add(Workspace(id=workspace_id, company_name="Issue ID"))
            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            coverage = assess_coverage(run.run_id, ("a",), ("a",))
            issue = blocking_issue(
                "FIN-FIRST",
                "financial",
                "first",
                (Capability.SUMMARIZE,),
                affected_refs=("a", "b"),
            )
            conflict = replace(
                issue, code="FIN-SECOND", blocking_capabilities=(Capability.FORECAST,)
            )
            decisions = decide_capabilities(run.run_id, coverage, (issue,))
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                for existing in (False, True):
                    if existing:
                        await repo.persist_quality_gate(coverage, (issue,), decisions)
                    for order in ((issue, conflict), (conflict, issue)):
                        with self.assertRaisesRegex(
                            ValueError, "conflicting.*issue_id"
                        ):
                            await repo.persist_quality_gate(coverage, order, decisions)
                    self.assertEqual(
                        await session.scalar(
                            select(func.count(DataQualityIssueModel.id))
                        ),
                        int(existing),
                    )
                    self.assertEqual(
                        await session.scalar(
                            select(func.count(CoverageAssessmentModel.id))
                        ),
                        int(existing),
                    )

                equivalent = replace(issue, affected_refs=("b", "a", "b"))
                replay = await repo.persist_quality_gate(
                    coverage,
                    (equivalent, issue),
                    decide_capabilities(run.run_id, coverage, (equivalent, issue)),
                )
                self.assertFalse(replay[2])
                stored = await session.scalar(select(DataQualityIssueModel))
                self.assertEqual(
                    stored.id, _quality_issue_id(workspace_id, run.run_id, issue)
                )
                self.assertEqual(stored.code, issue.code)
                self.assertEqual(stored.affected_refs, ["a", "b"])
                stored_decision = await session.scalar(
                    select(CapabilityDecisionModel).where(
                        CapabilityDecisionModel.capability == Capability.SUMMARIZE.value
                    )
                )
                self.assertEqual(stored_decision.supporting_issue_ids, [str(stored.id)])

        asyncio.run(scenario())

    def test_0005_0006_round_trip_preserves_existing_schema_and_matches_orm(self):
        import re

        def check_signature(expression):
            return (
                re.search(r"[a-z_]+", expression).group(),
                tuple(sorted(re.findall(r"'([^']*)'", expression))),
            )

        async def schema():
            def fingerprint(connection):
                inspector = inspect(connection)
                return {
                    name: {
                        "columns": [
                            (c["name"], str(c["type"]), c["nullable"])
                            for c in inspector.get_columns(name)
                        ],
                        "pk": tuple(
                            inspector.get_pk_constraint(name)["constrained_columns"]
                        ),
                        "fks": sorted(
                            (
                                tuple(f["constrained_columns"]),
                                f["referred_table"],
                                tuple(f["referred_columns"]),
                                f.get("options", {}).get("ondelete"),
                            )
                            for f in inspector.get_foreign_keys(name)
                        ),
                        "uniques": sorted(
                            tuple(c["column_names"])
                            for c in inspector.get_unique_constraints(name)
                        ),
                        "checks": sorted(
                            check_signature(c["sqltext"])
                            for c in inspector.get_check_constraints(name)
                        ),
                        "indexes": sorted(
                            tuple(c["column_names"])
                            for c in inspector.get_indexes(name)
                            if not c.get("duplicates_constraint")
                        ),
                    }
                    for name in inspector.get_table_names()
                }

            async with self.engine.connect() as connection:
                return await connection.run_sync(fingerprint)

        async def scenario():
            await self.reset()
            alembic("downgrade", "20260729_0005")
            before = await schema()
            alembic("upgrade", "20260727_0006")
            after = await schema()
            tables = (
                DataQualityIssueModel.__table__,
                CoverageAssessmentModel.__table__,
                CapabilityDecisionModel.__table__,
            )
            self.assertEqual(set(after) - set(before), {t.name for t in tables})
            self.assertEqual({name: after[name] for name in before}, before)
            for table in tables:
                expected = {
                    "columns": [
                        (
                            c.name,
                            str(c.type).replace("DATETIME", "TIMESTAMP"),
                            c.nullable,
                        )
                        for c in table.columns
                    ],
                    "pk": tuple(c.name for c in table.primary_key.columns),
                    "fks": sorted(
                        (
                            tuple(e.parent.name for e in f.elements),
                            next(iter(f.elements)).column.table.name,
                            tuple(e.column.name for e in f.elements),
                            f.ondelete,
                        )
                        for f in table.foreign_key_constraints
                    ),
                    "uniques": sorted(
                        tuple(c.name for c in constraint.columns)
                        for constraint in table.constraints
                        if constraint.__class__.__name__ == "UniqueConstraint"
                    ),
                    "checks": sorted(
                        check_signature(str(c.sqltext))
                        for c in table.constraints
                        if c.__class__.__name__ == "CheckConstraint"
                    ),
                    "indexes": sorted(
                        tuple(c.name for c in index.columns) for index in table.indexes
                    ),
                }
                self.assertEqual(after[table.name], expected)
            alembic("downgrade", "20260729_0005")
            self.assertEqual(await schema(), before)
            alembic("upgrade", "20260727_0006")
            self.assertEqual(await schema(), after)

        asyncio.run(scenario())

    def test_coverage_transport_permutations_replay_concurrently(self):
        from saleslab_v2.domain.enums import Capability

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(
                        id=user_id,
                        email="f05-coverage-order@example.test",
                        password_hash="x",
                    )
                )
                session.add(Workspace(id=workspace_id, company_name="Coverage Order"))
            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            coverage = assess_coverage(
                run.run_id, ("a", "b", "c", "d"), ("a", "b"), ("c", "d")
            )
            transport = replace(
                coverage,
                assessment_id=uuid4(),
                required_inputs=("d", "c", "b", "a", "a"),
                present_inputs=("b", "a", "b"),
                missing_inputs=("d", "c", "d"),
                ambiguous_inputs=("d", "c", "c"),
            )

            async def persist(value):
                issue = blocking_issue(
                    "FIN-ORDER", "financial", "order", (Capability.FORECAST,)
                )
                decisions = decide_capabilities(run.run_id, value, (issue,))
                async with self.sessions.begin() as session:
                    return await V2Repository(
                        session, workspace_id
                    ).persist_quality_gate(value, iter((issue,)), iter(decisions))

            first, replay = await asyncio.gather(persist(coverage), persist(transport))
            self.assertEqual(first[0].id, replay[0].id)
            self.assertEqual(sorted((first[2], replay[2])), [False, True])
            sequential = await persist(transport)
            self.assertFalse(sequential[2])
            async with self.sessions.begin() as session:
                stored = await session.scalar(select(CoverageAssessmentModel))
                self.assertEqual(stored.required_inputs, ["a", "b", "c", "d"])
                self.assertEqual(stored.present_inputs, ["a", "b"])
                self.assertEqual(stored.missing_inputs, ["c", "d"])
                self.assertEqual(stored.ambiguous_inputs, ["c", "d"])
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    1,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    len(Capability),
                )
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    1,
                )

        asyncio.run(asyncio.wait_for(scenario(), timeout=20))

    def test_inconsistent_assessments_are_rejected_before_any_write_on_insert_and_replay(
        self,
    ):
        from types import MappingProxyType

        from saleslab_v2.quality.policies.financial_v1 import REQUIREMENTS

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(
                        id=user_id,
                        email="f05-invalid-coverage@example.test",
                        password_hash="x",
                    )
                )
                session.add(Workspace(id=workspace_id, company_name="Invalid Coverage"))
            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            coverage = assess_coverage(run.run_id, ("a", "b", "c"), ("a",))
            prerequisites = tuple(
                sorted({x for values in REQUIREMENTS.values() for x in values})
            )
            complete = assess_coverage(run.run_id, prerequisites, prerequisites)
            forged = (
                replace(
                    coverage,
                    quality_level=QualityLevel.COMPLETE,
                    present_inputs=prerequisites,
                ),
                replace(coverage, quality_level=QualityLevel.COMPLETE),
                replace(coverage, missing_inputs=()),
                replace(coverage, present_inputs=("a", "b", "c")),
                replace(coverage, ambiguous_inputs=("outside_profile",)),
                replace(
                    coverage,
                    score_components=MappingProxyType({"coverage_ratio": "1.0"}),
                ),
                replace(coverage, required_inputs=("a", "b")),
            )
            issue = blocking_issue(
                "FIN-INVALID-INPUT", "financial", "invalid_input", ()
            )
            tables = (
                DataQualityIssueModel,
                CoverageAssessmentModel,
                CapabilityDecisionModel,
            )
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                for existing in (False, True):
                    if existing:
                        await repo.persist_quality_gate(
                            coverage, (), decide_capabilities(run.run_id, coverage)
                        )
                    before = []
                    for table in tables:
                        before.append(
                            await session.scalar(select(func.count(table.id)))
                        )
                    for invalid in forged:
                        # Do not call the decision engine with invalid coverage:
                        # exercise the independent persistence trust boundary.
                        with self.assertRaisesRegex(
                            ValueError, "inconsistent coverage"
                        ):
                            await repo.persist_quality_gate(
                                invalid,
                                (issue,),
                                decide_capabilities(run.run_id, complete),
                            )
                        after = []
                        for table in tables:
                            after.append(
                                await session.scalar(select(func.count(table.id)))
                            )
                        self.assertEqual(after, before)
                # A caught validation failure must leave the outer session usable.
                replay = await repo.persist_quality_gate(
                    coverage, (), decide_capabilities(run.run_id, coverage)
                )
                self.assertFalse(replay[2])

        asyncio.run(scenario())

    @classmethod
    def setUpClass(cls):
        parsed = make_url(async_url(URL))
        if parsed.host not in {"localhost", "127.0.0.1"} or not any(
            marker in (parsed.database or "").lower()
            for marker in ("test", "f05", "disposable")
        ):
            raise RuntimeError("refusing a non-local or non-disposable database")
        cls.engine = create_async_engine(async_url(URL), poolclass=NullPool)
        cls.sessions = async_sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.engine.dispose())

    async def reset(self):
        async with self.engine.begin() as connection:
            version = await connection.scalar(text("SHOW server_version_num"))
            if int(version) // 10000 != 16:
                raise RuntimeError(f"PostgreSQL 16 required, got {version}")
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=legacy_tables())
            )
        alembic("stamp", "20260727_0001")
        alembic("upgrade", "20260727_0006")

    def test_replay_divergence_concurrency_and_session_health(self):
        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f05@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="F05"))

            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            coverage = assess_coverage(run.run_id, ("revenue",), ("revenue",))
            decisions = decide_capabilities(run.run_id, coverage)

            async def persist():
                async with self.sessions.begin() as session:
                    return await V2Repository(
                        session, workspace_id
                    ).persist_quality_gate(coverage, (), decisions)

            first, second = await asyncio.gather(persist(), persist())
            self.assertEqual(first[0].id, second[0].id)
            self.assertEqual(sorted((first[2], second[2])), [False, True])

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                replay = await repo.persist_quality_gate(coverage, (), decisions)
                self.assertFalse(replay[2])
                divergent = assess_coverage(
                    run.run_id, ("revenue", "cost"), ("revenue",)
                )
                with self.assertRaises(IdempotencyConflict):
                    await repo.persist_quality_gate(
                        divergent,
                        (),
                        decide_capabilities(run.run_id, divergent),
                    )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    1,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    len(Capability),
                )

        from saleslab_v2.domain.enums import Capability

        asyncio.run(scenario())

    def test_incompatible_replay_rolls_back_all_partial_gate_writes(self):
        from saleslab_v2.domain.enums import Capability

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f05-atomic@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="F05 Atomic"))

            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            coverage = assess_coverage(
                run.run_id,
                ("validated_statement", "identified_period"),
                ("validated_statement", "identified_period"),
            )
            original = decide_capabilities(run.run_id, coverage)

            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).persist_quality_gate(
                    coverage, (), original
                )

            conflict_issue = blocking_issue(
                "FIN-LATE-CONFLICT",
                "financial",
                "late_conflict",
                (),
                remediation_key="resolve_late_conflict",
            )
            conflicting = decide_capabilities(
                run.run_id,
                coverage,
                (conflict_issue,),
            )

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                with self.assertRaises(IdempotencyConflict):
                    await repo.persist_quality_gate(
                        coverage,
                        (conflict_issue,),
                        conflicting,
                    )
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    1,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    len(Capability),
                )

            async with self.sessions.begin() as session:
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )

        asyncio.run(scenario())

    def test_reordered_scoped_issue_is_a_semantic_replay(self):
        from saleslab_v2.domain.enums import Capability

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f05-replay@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="F05 Replay"))

            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            coverage = assess_coverage(run.run_id, ("revenue",), ("revenue",))
            first_issue = blocking_issue(
                "FIN-HEDGE-SCHEDULE-INCOMPLETE",
                "financial",
                "hedge_schedule_incomplete",
                (Capability.QUANTIFY_IMPACT, Capability.RECOMMEND_MATERIAL),
                remediation_key="provide_hedge_schedule",
            )
            replay_issue = blocking_issue(
                "FIN-HEDGE-SCHEDULE-INCOMPLETE",
                "financial",
                "hedge_schedule_incomplete",
                (
                    Capability.RECOMMEND_MATERIAL,
                    Capability.QUANTIFY_IMPACT,
                    Capability.RECOMMEND_MATERIAL,
                ),
                remediation_key="provide_hedge_schedule",
            )

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                first = await repo.persist_quality_gate(
                    coverage,
                    (first_issue,),
                    decide_capabilities(run.run_id, coverage, (first_issue,)),
                )
            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                replay = await repo.persist_quality_gate(
                    coverage,
                    (replay_issue,),
                    decide_capabilities(run.run_id, coverage, (replay_issue,)),
                )
                self.assertFalse(replay[2])
                stored_issue = await session.scalar(select(DataQualityIssueModel))
                self.assertEqual(
                    stored_issue.blocking_capabilities,
                    ["quantify_impact", "recommend_material"],
                )
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    1,
                )
                self.assertEqual(first[0].id, replay[0].id)

        asyncio.run(scenario())

    def test_concurrent_reordered_issue_sets_are_deadlock_free_semantic_replays(self):
        from saleslab_v2.domain.enums import Capability

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f05-order@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="F05 Order"))

            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            required = ("baseline", "driver", "cost", "sensitivity")
            coverage = assess_coverage(run.run_id, required, required)

            def issue(code, refs):
                return blocking_issue(
                    code,
                    "financial",
                    code.lower(),
                    (Capability.QUANTIFY_IMPACT,),
                    remediation_key="provide_sensitivity",
                    affected_refs=refs,
                )

            first_a = issue("FIN-ORDER-A", ("ref:2", "ref:1"))
            first_b = issue("FIN-ORDER-B", ("ref:4", "ref:3"))
            replay_a = issue("FIN-ORDER-A", ("ref:1", "ref:2", "ref:1"))
            replay_b = issue("FIN-ORDER-B", ("ref:3", "ref:4", "ref:3"))

            async def persist(issues):
                async with self.sessions.begin() as session:
                    return await V2Repository(
                        session, workspace_id
                    ).persist_quality_gate(
                        coverage,
                        issues,
                        decide_capabilities(run.run_id, coverage, issues),
                    )

            first, replay = await asyncio.gather(
                persist((first_a, first_b)),
                persist((replay_b, replay_a, replay_b)),
            )

            self.assertEqual(first[0].id, replay[0].id)
            self.assertEqual(sorted((first[2], replay[2])), [False, True])
            async with self.sessions.begin() as session:
                stored = (
                    await session.scalars(
                        select(DataQualityIssueModel).order_by(DataQualityIssueModel.id)
                    )
                ).all()
                self.assertEqual(len(stored), 2)
                self.assertEqual(
                    {tuple(row.affected_refs) for row in stored},
                    {("ref:1", "ref:2"), ("ref:3", "ref:4")},
                )

        asyncio.run(asyncio.wait_for(scenario(), timeout=15))

    def test_persistence_requires_complete_capability_matrix(self):
        from saleslab_v2.domain.enums import Capability

        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(
                        id=user_id, email="f05-complete@example.test", password_hash="x"
                    )
                )
                session.add(Workspace(id=workspace_id, company_name="F05 Complete"))

            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            required = ("validated_statement", "identified_period")
            coverage = assess_coverage(run.run_id, required, required)
            complete = decide_capabilities(run.run_id, coverage)

            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).persist_quality_gate(
                    coverage, (), complete
                )

            issue = blocking_issue(
                "FIN-OMITTED-DECISION",
                "financial",
                "omitted_decision",
                (Capability.SUMMARIZE,),
                remediation_key="resolve_omitted_decision",
            )
            incomplete = tuple(
                decision
                for decision in decide_capabilities(run.run_id, coverage, (issue,))
                if decision.capability != Capability.SUMMARIZE
            )

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                with self.assertRaisesRegex(
                    ValueError, "requires the complete capability matrix"
                ):
                    await repo.persist_quality_gate(coverage, (issue,), incomplete)
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    1,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    len(Capability),
                )

        asyncio.run(scenario())

    def test_persistence_rejects_nonfinancial_policy_even_when_modules_match(self):
        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(
                        id=user_id,
                        email="f05-unsupported-module@example.test",
                        password_hash="x",
                    )
                )
                session.add(
                    Workspace(id=workspace_id, company_name="F05 Unsupported Module")
                )

            run = make_run(workspace_id, user_id, ModuleType.SALES)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            required = ("validated_statement", "identified_period")
            coverage = assess_coverage(
                run.run_id,
                required,
                required,
                module=ModuleType.SALES,
            )
            decisions = decide_capabilities(
                run.run_id, replace(coverage, module=ModuleType.FINANCIAL)
            )

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                with self.assertRaisesRegex(ValueError, "policy is not supported"):
                    await repo.persist_quality_gate(coverage, (), decisions)
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    0,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    0,
                )

        asyncio.run(scenario())

    def test_persistence_rejects_decisions_computed_without_supplied_issue(self):
        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f05-stale@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="F05 Stale"))

            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            required = ("validated_statement", "identified_period")
            coverage = assess_coverage(run.run_id, required, required)
            stale_decisions = decide_capabilities(run.run_id, coverage)
            issue = blocking_issue(
                "FIN-STALE-DECISION",
                "financial",
                "stale_decision",
                (),
                remediation_key="resolve_stale_decision",
            )

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                with self.assertRaisesRegex(
                    ValueError, "decisions do not match supplied coverage and issues"
                ):
                    await repo.persist_quality_gate(coverage, (issue,), stale_decisions)
                self.assertEqual(
                    await session.scalar(select(func.count(DataQualityIssueModel.id))),
                    0,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    0,
                )
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CapabilityDecisionModel.id))
                    ),
                    0,
                )

        asyncio.run(scenario())

    def test_persistence_rejects_coverage_for_a_different_run_module(self):
        async def scenario():
            await self.reset()
            user_id, workspace_id = uuid4(), uuid4()
            async with self.sessions.begin() as session:
                session.add(
                    User(id=user_id, email="f05-module@example.test", password_hash="x")
                )
                session.add(Workspace(id=workspace_id, company_name="F05 Module"))

            run = make_run(workspace_id, user_id)
            async with self.sessions.begin() as session:
                await V2Repository(session, workspace_id).create_run(run)

            coverage = assess_coverage(
                run.run_id,
                ("validated_statement", "identified_period"),
                ("validated_statement", "identified_period"),
            )
            mismatched = replace(coverage, module=ModuleType.SALES)
            decisions = decide_capabilities(run.run_id, coverage)

            async with self.sessions.begin() as session:
                repo = V2Repository(session, workspace_id)
                with self.assertRaisesRegex(
                    ValueError, "module does not match run module"
                ):
                    await repo.persist_quality_gate(mismatched, (), decisions)
                self.assertEqual(
                    await session.scalar(
                        select(func.count(CoverageAssessmentModel.id))
                    ),
                    0,
                )

        asyncio.run(scenario())
