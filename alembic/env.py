"""Alembic environment for the legacy SalesLab PostgreSQL schema.

DATABASE_URL is the only source for connection details. Existing databases
must be inspected before they are stamped; the baseline revision intentionally
contains no legacy DDL.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from database import Base
import models  # noqa: F401  # Register the legacy models with Base.metadata.
import saleslab_v2.persistence.models  # noqa: F401  # Register isolated V2 models.

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """Return an async SQLAlchemy URL without logging or persisting secrets."""
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL must be set to run Alembic")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def include_object(object_, name, type_, reflected, compare_to):
    """Do not autogenerate drops for database objects absent from the models.

    Production schema drift must first be reviewed with the read-only inspector.
    Explicit, reviewed revisions can still execute deliberate DDL operations.
    """
    if reflected and compare_to is None:
        return False
    return True


def configure(connection: Connection | None = None, *, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        transactional_ddl=True,
    )


def run_migrations_offline() -> None:
    """Render SQL without opening a database connection."""
    configure(url=database_url())
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
