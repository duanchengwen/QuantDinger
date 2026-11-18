-- An immutable paper strategy version and its explicit human decisions.
CREATE TABLE IF NOT EXISTS qd_ashare_strategy_versions (
    tenant_id text NOT NULL,
    account_id text NOT NULL,
    id text NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
    request_key text NOT NULL CHECK (request_key ~ '^[A-Za-z0-9_-]{8,100}$'),
    config jsonb NOT NULL CHECK (jsonb_typeof(config) = 'object'
        AND config @> '{"mode":"paper","kind":"buy_once_below"}'),
    config_sha256 text NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    created_by bigint NOT NULL CHECK (created_by > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, account_id, id),
    UNIQUE (tenant_id, account_id, request_key),
    UNIQUE (tenant_id, account_id, id, config_sha256),
    FOREIGN KEY (tenant_id, account_id) REFERENCES qd_ashare_accounts (tenant_id, account_id)
);

CREATE INDEX IF NOT EXISTS qd_ashare_strategy_versions_recent
    ON qd_ashare_strategy_versions (tenant_id, account_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS qd_ashare_strategy_decisions (
    tenant_id text NOT NULL,
    account_id text NOT NULL,
    version_id text NOT NULL,
    config_sha256 text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'revoked')),
    actor_user_id bigint NOT NULL CHECK (actor_user_id > 0),
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, account_id, version_id, decision),
    FOREIGN KEY (tenant_id, account_id, version_id, config_sha256)
        REFERENCES qd_ashare_strategy_versions (tenant_id, account_id, id, config_sha256)
);

CREATE OR REPLACE FUNCTION qd_ashare_strategy_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'strategy_records_are_append_only';
END;
$$;
DROP TRIGGER IF EXISTS strategy_versions_append_only ON qd_ashare_strategy_versions;
CREATE TRIGGER strategy_versions_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
    ON qd_ashare_strategy_versions FOR EACH STATEMENT EXECUTE FUNCTION qd_ashare_strategy_append_only();
DROP TRIGGER IF EXISTS strategy_decisions_append_only ON qd_ashare_strategy_decisions;
CREATE TRIGGER strategy_decisions_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
    ON qd_ashare_strategy_decisions FOR EACH STATEMENT EXECUTE FUNCTION qd_ashare_strategy_append_only();
