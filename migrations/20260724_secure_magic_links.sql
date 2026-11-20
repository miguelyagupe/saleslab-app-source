-- Safe PostgreSQL migration for one-time magic links.
-- Apply before deploying the backend code that issues hashed magic link tokens.

SET lock_timeout = '5s';
SET statement_timeout = '5min';

CREATE TABLE IF NOT EXISTS magic_link_tokens (
  id uuid PRIMARY KEY,
  token_hash varchar(64) NOT NULL UNIQUE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  role varchar(20) NOT NULL,
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_magic_link_tokens_expires_at
  ON magic_link_tokens (expires_at);
