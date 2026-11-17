-- Durable human configuration and worker observations for paper accounts.
CREATE TABLE IF NOT EXISTS qd_ashare_web (
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 settings JSONB NOT NULL DEFAULT '{"symbols":["sz.000001"],"interval_seconds":5}',
 rules JSONB NOT NULL DEFAULT '{}', quotes JSONB NOT NULL DEFAULT '[]',
 worker JSONB NOT NULL DEFAULT '{"state":"not_started"}',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id, account_id)
);
ALTER TABLE qd_ashare_web ADD COLUMN IF NOT EXISTS notifications_encrypted TEXT;
