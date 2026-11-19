-- Immutable evidence produced by the automatic paper strategy evaluator.
CREATE TABLE IF NOT EXISTS qd_ashare_strategy_backtests (
    tenant_id text NOT NULL,
    account_id text NOT NULL,
    id text NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
    research_run_id text NOT NULL REFERENCES qd_ashare_research_runs (id),
    result_sha256 text NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
    report jsonb NOT NULL CHECK (jsonb_typeof(report) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, account_id, id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES qd_ashare_accounts (tenant_id, account_id)
);

DROP TRIGGER IF EXISTS strategy_backtests_append_only ON qd_ashare_strategy_backtests;
CREATE TRIGGER strategy_backtests_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
    ON qd_ashare_strategy_backtests FOR EACH STATEMENT EXECUTE FUNCTION qd_ashare_strategy_append_only();

-- The initial inline constraint was generated under this name.
ALTER TABLE qd_ashare_strategy_versions DROP CONSTRAINT IF EXISTS qd_ashare_strategy_versions_config_check;
ALTER TABLE qd_ashare_strategy_versions DROP CONSTRAINT IF EXISTS paper_strategy_config_supported;
ALTER TABLE qd_ashare_strategy_versions ADD CONSTRAINT paper_strategy_config_supported CHECK (
    jsonb_typeof(config) = 'object' AND (
        config @> '{"mode":"paper","kind":"buy_once_below"}'
        OR (config @> '{"mode":"paper","kind":"sell_once_above"}'
            AND config ? 'research_reference' AND config ? 'backtest_reference')
    )
);
