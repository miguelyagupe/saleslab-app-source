-- Safe PostgreSQL migration for quota counters.
-- Run before deploying the application code that uses ON CONFLICT on (workspace_id, month).
-- This migration is idempotent and intentionally avoids wrapping the concurrent index build
-- in an explicit transaction, because PostgreSQL does not allow CREATE INDEX CONCURRENTLY
-- inside a transaction block.

SET lock_timeout = '5s';
SET statement_timeout = '5min';

ALTER TABLE IF EXISTS usage_counters
  ALTER COLUMN month TYPE varchar(16);

-- Consolidate any duplicate counters before enforcing uniqueness. If duplicates exist,
-- keep one row per (workspace_id, month) and sum the counts into that row.
WITH ranked AS (
  SELECT
    id,
    SUM(count) OVER (PARTITION BY workspace_id, month) AS total_count,
    ROW_NUMBER() OVER (PARTITION BY workspace_id, month ORDER BY id::text) AS rn
  FROM usage_counters
), keepers AS (
  SELECT id, total_count
  FROM ranked
  WHERE rn = 1
)
UPDATE usage_counters AS uc
SET count = keepers.total_count
FROM keepers
WHERE uc.id = keepers.id;

WITH ranked AS (
  SELECT
    id,
    ROW_NUMBER() OVER (PARTITION BY workspace_id, month ORDER BY id::text) AS rn
  FROM usage_counters
)
DELETE FROM usage_counters AS uc
USING ranked
WHERE uc.id = ranked.id
  AND ranked.rn > 1;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_usage_counters_workspace_month
  ON usage_counters (workspace_id, month);
