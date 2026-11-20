-- Safe PostgreSQL migration for Stripe subscription lifecycle synchronization.
-- Apply before deploying the webhook code that reads/writes these columns.

SET lock_timeout = '5s';
SET statement_timeout = '5min';

ALTER TABLE IF EXISTS workspaces
  ADD COLUMN IF NOT EXISTS stripe_subscription_status varchar(50),
  ADD COLUMN IF NOT EXISTS stripe_current_period_end timestamptz,
  ADD COLUMN IF NOT EXISTS stripe_cancel_at_period_end boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS stripe_cancel_at timestamptz;

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
  id varchar(255) PRIMARY KEY,
  event_type varchar(100) NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_workspaces_stripe_customer_id
  ON workspaces (stripe_customer_id)
  WHERE stripe_customer_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_workspaces_stripe_subscription_id
  ON workspaces (stripe_subscription_id)
  WHERE stripe_subscription_id IS NOT NULL;
