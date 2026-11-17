-- Operational state belongs to the paper worker, never to a live broker.
CREATE TABLE IF NOT EXISTS qd_ashare_process_health (
 process_name TEXT PRIMARY KEY,
 instance_id TEXT NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL,
 details JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS qd_ashare_report_progress (
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 first_observed_day DATE NOT NULL,
 last_scanned_day DATE,
 PRIMARY KEY (tenant_id, account_id),
 FOREIGN KEY (tenant_id, account_id) REFERENCES qd_ashare_accounts(tenant_id, account_id)
);
CREATE TABLE IF NOT EXISTS qd_ashare_daily_reports (
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 trading_day DATE NOT NULL, generated_at TIMESTAMPTZ NOT NULL,
 report JSONB NOT NULL,
 PRIMARY KEY (tenant_id, account_id, trading_day),
 FOREIGN KEY (tenant_id, account_id) REFERENCES qd_ashare_accounts(tenant_id, account_id)
);
CREATE INDEX IF NOT EXISTS qd_ashare_audit_account_recent ON qd_ashare_audit(tenant_id,account_id,id DESC);
CREATE INDEX IF NOT EXISTS qd_ashare_fills_account_recent ON qd_ashare_fills(tenant_id,account_id,id DESC);
CREATE INDEX IF NOT EXISTS qd_ashare_orders_account_recent ON qd_ashare_orders(tenant_id,account_id,id DESC);
CREATE TABLE IF NOT EXISTS qd_ashare_day_observations (
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL, trading_day DATE NOT NULL,
 first_observed_at TIMESTAMPTZ NOT NULL, last_observed_at TIMESTAMPTZ NOT NULL,
 poll_count BIGINT NOT NULL DEFAULT 0,
 wall_clock_polls BIGINT NOT NULL DEFAULT 0, controlled_time_polls BIGINT NOT NULL DEFAULT 0,
 failed_polls BIGINT NOT NULL DEFAULT 0, ready_polls BIGINT NOT NULL DEFAULT 0,
 stale_quote_polls BIGINT NOT NULL DEFAULT 0,
 max_source_age_seconds DOUBLE PRECISION,
 last_details JSONB NOT NULL,
 PRIMARY KEY(tenant_id,account_id,trading_day),
 FOREIGN KEY (tenant_id,account_id) REFERENCES qd_ashare_accounts(tenant_id,account_id)
);
