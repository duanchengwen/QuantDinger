-- Activation is a separate human decision. Older approvals stay inert on upgrade.
CREATE TABLE IF NOT EXISTS qd_ashare_strategy_activations (
    tenant_id text NOT NULL,
    account_id text NOT NULL,
    version_id text NOT NULL,
    config_sha256 text NOT NULL,
    actor_user_id bigint NOT NULL CHECK (actor_user_id > 0),
    activated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, account_id, version_id),
    FOREIGN KEY (tenant_id, account_id, version_id, config_sha256)
        REFERENCES qd_ashare_strategy_versions (tenant_id, account_id, id, config_sha256)
);

DROP TRIGGER IF EXISTS strategy_activations_append_only ON qd_ashare_strategy_activations;
CREATE TRIGGER strategy_activations_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
    ON qd_ashare_strategy_activations FOR EACH STATEMENT EXECUTE FUNCTION qd_ashare_strategy_append_only();
