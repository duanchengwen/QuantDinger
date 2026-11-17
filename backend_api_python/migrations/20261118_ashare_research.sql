-- Research is deliberately separate from orders, rules, and strategy approval.
CREATE TABLE IF NOT EXISTS qd_ashare_research_runs (
 id TEXT PRIMARY KEY,
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('queued','started','needs_model_configuration','no_evidence','failed','completed')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, lease_until TIMESTAMPTZ,
 claim_token TEXT,
 preferences JSONB NOT NULL,
 input_snapshot JSONB, input_hash TEXT,
 provider TEXT NOT NULL DEFAULT 'deepseek', model TEXT, response_model TEXT,
 raw_response TEXT, output_hash TEXT, model_output TEXT,
 usage JSONB, cost NUMERIC CHECK (cost IS NULL),
 summary TEXT NOT NULL DEFAULT '', candidates JSONB NOT NULL DEFAULT '[]',
 risks JSONB NOT NULL DEFAULT '[]', error_code TEXT,
 execution_allowed BOOLEAN NOT NULL DEFAULT false CHECK (execution_allowed = false),
 approval_status TEXT NOT NULL DEFAULT 'not_submitted' CHECK (approval_status = 'not_submitted'),
 UNIQUE (tenant_id,account_id,idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS qd_ashare_research_one_active
 ON qd_ashare_research_runs(tenant_id,account_id) WHERE status IN ('queued','started');
CREATE INDEX IF NOT EXISTS qd_ashare_research_queue
 ON qd_ashare_research_runs(status,created_at,id);
CREATE INDEX IF NOT EXISTS qd_ashare_research_account
 ON qd_ashare_research_runs(tenant_id,account_id,created_at DESC);
