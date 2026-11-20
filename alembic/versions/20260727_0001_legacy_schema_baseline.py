"""Mark the externally verified legacy schema as the Alembic baseline.

This revision is intentionally empty. It must never create, alter, rename, or
drop legacy objects. Existing databases adopt it with ``alembic stamp`` only
after the read-only schema report has been reviewed. Empty databases must first
be initialized by the existing application startup and then inspected.

Revision ID: 20260727_0001
Revises: None
"""

from typing import Sequence, Union


revision: str = "20260727_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
