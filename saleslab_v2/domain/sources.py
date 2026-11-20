from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .enums import LanguageCode, SourceFormat, TrustLevel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SourceDocument:
    workspace_id: UUID
    filename: str
    source_format: SourceFormat
    content_hash: str
    parser_version: str
    trust_level: TrustLevel
    languages: tuple[LanguageCode, ...] = ()
    source_id: UUID = field(default_factory=uuid4)
    uploaded_at: datetime = field(default_factory=utcnow)
    created_at: datetime = field(default_factory=utcnow)
    supersedes_source_id: UUID | None = None

    def __post_init__(self):
        if len(self.content_hash) != 64 or any(
            c not in "0123456789abcdef" for c in self.content_hash
        ):
            raise ValueError("content_hash must be lowercase SHA-256 hex")
