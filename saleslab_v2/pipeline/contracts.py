"""Typed internal requests. Parsed rows are data; bindings are explicit plans."""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from uuid import UUID

from saleslab_v2.domain.enums import Capability
from saleslab_v2.domain.idempotency import semantic_hash
from saleslab_v2.domain.language import LanguageContext
from saleslab_v2.domain.sources import SourceDocument
from saleslab_v2.metrics.financial_v1 import FINANCIAL_V1
from saleslab_v2.normalization.financial import FinancialRow, SourceMetadata
from saleslab_v2.quality.requirements import rule_version_for
from saleslab_v2.reconciliation.contracts import TolerancePolicy
from saleslab_v2.reconciliation.financial_v1 import FINANCIAL_RULES_V1

PIPELINE_VERSION = "financial-internal-v1"
STAGES = ("normalization", "metrics", "reconciliation", "quality_gates", "checkpoint")


@dataclass(frozen=True)
class SourceBatch:
    document: SourceDocument
    metadata: SourceMetadata
    rows: tuple[FinancialRow, ...]

    def __post_init__(self):
        object.__setattr__(
            self, "rows", tuple(sorted(self.rows, key=lambda r: r.source_reference))
        )
        if not self.rows or len({r.source_reference for r in self.rows}) != len(
            self.rows
        ):
            raise ValueError("source rows must be nonempty with unique references")
        if (self.document.source_id, self.document.content_hash) != (
            self.metadata.source_document_id,
            self.metadata.content_hash,
        ):
            raise ValueError("source document and parser metadata disagree")
        if self.metadata.source_language not in self.document.languages:
            raise ValueError("parser language must be declared by its source")

    def payload(self):
        document = asdict(self.document)
        document.pop("uploaded_at")
        document.pop("created_at")
        metadata = asdict(self.metadata)
        if self.metadata.roic_binding is None:
            metadata.pop("roic_binding")
        # Version 1 predates this pin: preserve its exact persisted contract.
        if self.metadata.normalization_version == "1":
            metadata.pop("normalization_version")
        if self.metadata.terminology_version == "1":
            metadata.pop("terminology_version")
        return {
            "document": document,
            "metadata": metadata,
            "rows": [
                {
                    k: v
                    for k, v in asdict(r).items()
                    if k != "definition_quote" or v is not None
                }
                for r in self.rows
            ],
        }


@dataclass(frozen=True)
class InputRef:
    key: str
    source_id: UUID | None = None

    def __post_init__(self):
        if not self.key or self.key != self.key.strip():
            raise ValueError("input reference must be nonempty and trimmed")


def _bindings(value):
    value = tuple(sorted(value, key=lambda item: item[0]))
    if len({role for role, _ in value}) != len(value):
        raise ValueError("binding roles must be unique")
    if any(not isinstance(ref, InputRef) for _, ref in value):
        raise TypeError("bindings require typed InputRef values")
    return value


@dataclass(frozen=True)
class MetricStep:
    key: str
    metric_id: str
    inputs: tuple[tuple[str, InputRef], ...]
    version: str = "1"

    def __post_init__(self):
        if not self.key or self.key != self.key.strip():
            raise ValueError("metric step key is required")
        object.__setattr__(self, "inputs", _bindings(self.inputs))
        if {role for role, _ in self.inputs} != set(
            FINANCIAL_V1.get(self.metric_id, self.version).inputs
        ):
            raise ValueError("metric bindings must match its registered operands")


@dataclass(frozen=True)
class ReconciliationStep:
    rule_id: str
    inputs: tuple[tuple[str, InputRef], ...]
    policy: TolerancePolicy
    version: str = "1"

    def __post_init__(self):
        object.__setattr__(self, "inputs", _bindings(self.inputs))
        rule = FINANCIAL_RULES_V1.get(self.rule_id, self.version)
        roles = {role for role, _ in self.inputs}
        if (
            not {o.role for o in rule.operands if o.required}
            <= roles
            <= {o.role for o in rule.operands}
        ):
            raise ValueError("reconciliation bindings must match registered operands")
        if not isinstance(self.policy, TolerancePolicy):
            raise TypeError("an explicit tolerance policy is required")


@dataclass(frozen=True)
class FinancialRequest:
    workspace_id: UUID
    user_id: UUID
    idempotency_key: str
    language: LanguageContext
    sources: tuple[SourceBatch, ...]
    metrics: tuple[MetricStep, ...]
    reconciliations: tuple[ReconciliationStep, ...]
    requested_capabilities: tuple[Capability, ...] = (
        Capability.SUMMARIZE,
        Capability.CALCULATE,
    )
    profile: str = "financial_v1"
    analyst_metric_keys: tuple[str, ...] | None = None

    def snapshot(self):
        """Revalidate nested transport objects; frozen is not a trust boundary."""
        value = deepcopy(self)
        sources = tuple(
            SourceBatch(
                replace(b.document),
                replace(b.metadata),
                tuple(replace(row) for row in b.rows),
            )
            for b in value.sources
        )
        metrics = tuple(
            replace(
                step, inputs=tuple((role, replace(ref)) for role, ref in step.inputs)
            )
            for step in value.metrics
        )
        reconciliations = tuple(
            replace(
                step,
                policy=replace(step.policy),
                inputs=tuple((role, replace(ref)) for role, ref in step.inputs),
            )
            for step in value.reconciliations
        )
        return replace(
            value,
            sources=sources,
            metrics=metrics,
            reconciliations=reconciliations,
            language=replace(value.language),
        )

    def __post_init__(self):
        rule_version_for(self.profile)
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.user_id, UUID)
            or not self.idempotency_key.strip()
        ):
            raise ValueError("workspace, user and idempotency key are required")
        object.__setattr__(
            self,
            "sources",
            tuple(sorted(self.sources, key=lambda b: str(b.document.source_id))),
        )
        object.__setattr__(
            self, "metrics", tuple(sorted(self.metrics, key=lambda s: s.key))
        )
        object.__setattr__(
            self,
            "reconciliations",
            tuple(sorted(self.reconciliations, key=lambda s: (s.rule_id, s.version))),
        )
        object.__setattr__(
            self,
            "requested_capabilities",
            tuple(sorted(set(self.requested_capabilities), key=lambda c: c.value)),
        )
        if not self.sources or not self.requested_capabilities:
            raise ValueError("sources and requested capabilities cannot be empty")
        source_ids = {b.document.source_id for b in self.sources}
        if len(source_ids) != len(self.sources) or any(
            b.document.workspace_id != self.workspace_id for b in self.sources
        ):
            raise ValueError("source IDs must be unique and belong to the workspace")
        if not {b.metadata.source_language for b in self.sources} <= set(
            self.language.source_languages
        ):
            raise ValueError("run language context must cover every source language")
        if len({s.key for s in self.metrics}) != len(self.metrics):
            raise ValueError("metric step keys must be unique")
        if len({(s.rule_id, s.version) for s in self.reconciliations}) != len(
            self.reconciliations
        ):
            raise ValueError("one reconciliation per rule/version is supported per run")
        for step in (*self.metrics, *self.reconciliations):
            for _, ref in step.inputs:
                if ref.source_id is not None and ref.source_id not in source_ids:
                    raise ValueError("binding references a source outside the request")
        self.ordered_metrics()
        keys = {s.key for s in self.metrics}
        if any(
            ref.source_id is None and ref.key not in keys
            for s in self.reconciliations
            for _, ref in s.inputs
        ):
            raise ValueError("unknown metric dependency")
        if self.analyst_metric_keys is not None:
            if not isinstance(self.analyst_metric_keys, tuple) or any(
                not isinstance(key, str) or key not in keys
                for key in self.analyst_metric_keys
            ):
                raise ValueError(
                    "analyst metric selection must contain known step keys"
                )
            object.__setattr__(
                self,
                "analyst_metric_keys",
                tuple(sorted(set(self.analyst_metric_keys))),
            )
        _ = self.fingerprint  # Reject unsupported semantic types before any I/O.

    def ordered_metrics(self):
        pending = {s.key: s for s in self.metrics}
        known = set(pending)
        if any(
            ref.source_id is None and ref.key not in known
            for s in pending.values()
            for _, ref in s.inputs
        ):
            raise ValueError("unknown metric dependency")
        ordered, done = [], set()
        while pending:
            ready = sorted(
                k
                for k, s in pending.items()
                if all(
                    ref.source_id is not None or ref.key in done for _, ref in s.inputs
                )
            )
            if not ready:
                raise ValueError("cyclic metric dependency")
            for key in ready:
                ordered.append(pending.pop(key))
                done.add(key)
        return tuple(ordered)

    def semantic_payload(self):
        return {
            "version": PIPELINE_VERSION,
            "sources": [b.payload() for b in self.sources],
            "metrics": self.metrics,
            "reconciliations": self.reconciliations,
            "capabilities": self.requested_capabilities,
            "profile": self.profile,
            "language": self.language.semantic_dict(),
            **(
                {"analyst_metric_keys": self.analyst_metric_keys}
                if self.analyst_metric_keys is not None
                else {}
            ),
        }

    @property
    def fingerprint(self):
        return semantic_hash(self.semantic_payload())
