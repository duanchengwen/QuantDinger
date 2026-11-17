-- Current observations only. No backdated historical theme membership is inferred.
CREATE TABLE IF NOT EXISTS qd_ashare_theme_receipts (
    id text PRIMARY KEY,
    board_code text NOT NULL,
    board_name text NOT NULL,
    received_at timestamptz NOT NULL,
    published_at timestamptz CHECK (published_at IS NULL),
    source text NOT NULL CHECK (source = 'eastmoney'),
    source_hash text NOT NULL CHECK (length(source_hash) = 64),
    evidence_path text NOT NULL,
    member_count integer NOT NULL CHECK (member_count >= 0 AND member_count <= 10000),
    provider_total integer NOT NULL CHECK (provider_total > 0 AND provider_total <= 10000),
    excluded_count integer NOT NULL CHECK (excluded_count >= 0),
    metadata jsonb NOT NULL,
    CHECK (member_count + excluded_count = provider_total)
);
CREATE INDEX IF NOT EXISTS qd_ashare_theme_receipts_known
    ON qd_ashare_theme_receipts(board_code, received_at DESC);
CREATE TABLE IF NOT EXISTS qd_ashare_theme_members (
    receipt_id text NOT NULL REFERENCES qd_ashare_theme_receipts(id),
    symbol text NOT NULL,
    details jsonb NOT NULL,
    PRIMARY KEY(receipt_id, symbol)
);
