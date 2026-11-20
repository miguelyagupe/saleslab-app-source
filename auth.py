"""
Auth — substitui a lógica actual de "token = 1 plano solto no ar" por
tokens ligados a um workspace real na base de dados.

Fluxos suportados:
  1. Registo com password (email + password)
  2. Login com Google (recebe um id_token do frontend, valida, cria/liga conta)
  3. Login com password
  4. Emissão de JWT curto (contém workspace_id + user_id + role), validado
     em cada pedido consultando a BD para saber o plano actual e a quota.

Diferença chave face à versão anterior: o JWT deixa de conter "quantas
análises restam" — isso agora é sempre calculado on-the-fly a partir da
tabela usage_counters, para não depender de o token nunca expirar.
"""
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt as pyjwt
from fastapi import HTTPException, Header
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import User, Workspace, WorkspaceMember, UsageCounter, MagicLinkToken

def require_secret_env(name: str, *, min_length: int = 32) -> str:
    value = os.environ.get(name)
    if not value or len(value) < min_length:
        raise RuntimeError(f"{name} must be configured with at least {min_length} characters")
    return value


JWT_SECRET = require_secret_env("JWT_SECRET")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
MAGIC_LINK_TTL_MINUTES = int(os.environ.get("MAGIC_LINK_TTL_MINUTES", "15"))
ALLOWED_SESSION_ROLES = {"owner", "manager"}

PLAN_LIMITS = {
    "free": {"analyses": 10, "chatbot": False, "max_members": 1},
    "pro":  {"analyses": 20, "chatbot": True,  "max_members": 1},
    "team": {"analyses": 9999, "chatbot": True, "max_members": 3},
}

# ── PASSWORDS ─────────────────────────────────────────────────────────────

def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    return bcrypt.checkpw(raw.encode(), hashed.encode())


# ── GOOGLE OAUTH ──────────────────────────────────────────────────────────

def verify_google_token(id_token_str: str) -> dict:
    """Valida o id_token enviado pelo frontend (via Google Sign-In JS).
    Devolve {email, google_id, email_verified}."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth não configurado.")
    try:
        info = google_id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Token Google inválido.")
    return {
        "email": info["email"],
        "google_id": info["sub"],
        "email_verified": info.get("email_verified", False),
    }


# ── JWT ───────────────────────────────────────────────────────────────────

def issue_session_token(user_id: uuid.UUID, workspace_id: uuid.UUID, role: str) -> str:
    """Token de sessão curto (7 dias) — apenas identifica quem é e onde.
    O plano e a quota são sempre lidos da BD, nunca confiados ao token."""
    if role not in ALLOWED_SESSION_ROLES:
        raise RuntimeError("Invalid session role")
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": str(user_id),
        "workspace_id": str(workspace_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=7)).timestamp()),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_session_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Sessão em falta. Faça login.")
    try:
        payload = pyjwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "user_id", "workspace_id", "role"]},
        )
        uuid.UUID(payload["user_id"])
        uuid.UUID(payload["workspace_id"])
        if payload["role"] not in ALLOWED_SESSION_ROLES:
            raise pyjwt.InvalidTokenError
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessão expirada. Faça login novamente.")
    except (ValueError, pyjwt.InvalidTokenError):
        raise HTTPException(status_code=401, detail="Sessão inválida.")


def hash_magic_link_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_magic_link_token(db: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID, role: str) -> str:
    if role not in ALLOWED_SESSION_ROLES:
        raise RuntimeError("Invalid magic link role")
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.add(MagicLinkToken(
        token_hash=hash_magic_link_token(raw_token),
        user_id=user_id,
        workspace_id=workspace_id,
        role=role,
        expires_at=now + timedelta(minutes=MAGIC_LINK_TTL_MINUTES),
    ))
    return raw_token


def magic_link_payload_from_row(row) -> dict:
    return {"user_id": row.user_id, "workspace_id": row.workspace_id, "role": row.role}


async def consume_magic_link_token(db: AsyncSession, raw_token: str) -> dict:
    if not raw_token:
        raise HTTPException(status_code=401, detail="Sessão inválida.")

    now = datetime.now(timezone.utc)
    stmt = (
        update(MagicLinkToken)
        .where(
            MagicLinkToken.token_hash == hash_magic_link_token(raw_token),
            MagicLinkToken.used_at.is_(None),
            MagicLinkToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(
            MagicLinkToken.user_id,
            MagicLinkToken.workspace_id,
            MagicLinkToken.role,
        )
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(status_code=401, detail="Sessão inválida.")

    return magic_link_payload_from_row(row)


async def get_consumed_magic_link_session(db: AsyncSession, raw_token: str) -> dict:
    if not raw_token:
        raise HTTPException(status_code=401, detail="Sessão inválida.")

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(MagicLinkToken.user_id, MagicLinkToken.workspace_id, MagicLinkToken.role).where(
            MagicLinkToken.token_hash == hash_magic_link_token(raw_token),
            MagicLinkToken.used_at.is_not(None),
            MagicLinkToken.expires_at > now,
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=401, detail="Sessão inválida.")

    return magic_link_payload_from_row(row)


async def get_current_session(
    db: AsyncSession,
    x_token: str | None = Header(default=None),
) -> dict:
    """Dependency reutilizável: valida o JWT e devolve {user, workspace, role}."""
    try:
        payload = decode_session_token(x_token or "")
    except HTTPException:
        payload = await get_consumed_magic_link_session(db, x_token or "")

    workspace_id = uuid.UUID(str(payload["workspace_id"]))
    user_id = uuid.UUID(str(payload["user_id"]))

    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=401, detail="Workspace não encontrado.")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Utilizador não encontrado.")

    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=401, detail="Sessão inválida.")

    return {"user": user, "workspace": workspace, "role": membership.role}


# ── QUOTA ─────────────────────────────────────────────────────────────────

def get_quota_period_key(workspace: Workspace) -> str:
    return "lifetime" if workspace.plan == "free" else datetime.now(timezone.utc).strftime("%Y-%m")


def get_quota_exceeded_detail(workspace: Workspace, limit: int) -> str:
    return (
        "As suas 10 análises gratuitas foram utilizadas. Faça upgrade para continuar."
        if workspace.plan == "free"
        else f"Limite de {limit} análises mensais atingido. Faça upgrade para continuar."
    )


async def ensure_quota_available(db: AsyncSession, workspace: Workspace) -> int:
    """Valida quota sem consumir. Usado antes da chamada Anthropic para evitar custo desnecessário."""
    limits = PLAN_LIMITS.get(workspace.plan, PLAN_LIMITS["free"])
    limit = limits["analyses"]

    if limit >= 9999:
        return 9999  # Team: ilimitado

    period_key = get_quota_period_key(workspace)
    result = await db.execute(
        select(UsageCounter.count).where(
            UsageCounter.workspace_id == workspace.id,
            UsageCounter.month == period_key,
        )
    )
    used = result.scalar_one_or_none() or 0

    if used >= limit:
        raise HTTPException(status_code=429, detail=get_quota_exceeded_detail(workspace, limit))

    return limit - used


async def consume_quota_after_success(db: AsyncSession, workspace: Workspace) -> int:
    """Consome quota de forma atómica depois de a análise estar pronta para gravar.

    Requer índice/constraint único em usage_counters(workspace_id, month). O INSERT
    .. ON CONFLICT .. DO UPDATE com WHERE count < limit impede contadores duplicados
    e evita ultrapassar quota em pedidos concorrentes. Não faz commit: o caller deve
    gravar a análise e o consumo na mesma transação.
    """
    limits = PLAN_LIMITS.get(workspace.plan, PLAN_LIMITS["free"])
    limit = limits["analyses"]

    if limit >= 9999:
        return 9999  # Team: ilimitado

    period_key = get_quota_period_key(workspace)
    stmt = (
        pg_insert(UsageCounter)
        .values(workspace_id=workspace.id, month=period_key, count=1)
        .on_conflict_do_update(
            index_elements=[UsageCounter.workspace_id, UsageCounter.month],
            set_={"count": UsageCounter.count + 1},
            where=UsageCounter.count < limit,
        )
        .returning(UsageCounter.count)
    )
    result = await db.execute(stmt)
    used = result.scalar_one_or_none()

    if used is None:
        raise HTTPException(status_code=429, detail=get_quota_exceeded_detail(workspace, limit))

    return limit - used


async def get_remaining_quota(db: AsyncSession, workspace: Workspace) -> int:
    """Só consulta, não consome — usado em /validate-session."""
    limits = PLAN_LIMITS.get(workspace.plan, PLAN_LIMITS["free"])
    limit = limits["analyses"]
    if limit >= 9999:
        return 9999

    period_key = "lifetime" if workspace.plan == "free" else datetime.now(timezone.utc).strftime("%Y-%m")
    result = await db.execute(
        select(UsageCounter).where(
            UsageCounter.workspace_id == workspace.id,
            UsageCounter.month == period_key,
        )
    )
    counter = result.scalar_one_or_none()
    used = counter.count if counter else 0
    return max(0, limit - used)


# ── WORKSPACE MEMBER LIMIT (para o Team: máx. 3 perfis) ───────────────────

async def count_workspace_members(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    result = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)
    )
    return len(result.scalars().all())
