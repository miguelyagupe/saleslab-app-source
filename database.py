"""
Ligação à base de dados Postgres.

No Railway: adiciona um serviço Postgres ao projecto. Ele injecta
automaticamente a variável de ambiente DATABASE_URL nos outros serviços
do mesmo projecto (incluindo este backend), no formato:

    postgresql://user:pass@host:port/dbname

SQLAlchemy async precisa do driver asyncpg, por isso convertemos o prefixo.
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definida. Adiciona o serviço Postgres no Railway.")

# Railway/Heroku dão "postgresql://" ou "postgres://" — o driver async precisa de "postgresql+asyncpg://"
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://").replace("postgres://", "postgresql+asyncpg://")

engine = create_async_engine(ASYNC_DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Dependency do FastAPI — injecta uma sessão de BD por pedido."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Cria as tabelas se não existirem. Chamar uma vez no arranque (ou usar Alembic — ver README)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
