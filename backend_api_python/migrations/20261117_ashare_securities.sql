-- Append-only, dated provider observations; these are not exchange-certified statuses.
CREATE TABLE IF NOT EXISTS qd_ashare_security_receipts (
    id text PRIMARY KEY,
    trading_day date NOT NULL,
    received_at timestamptz NOT NULL,
    source text NOT NULL CHECK (source = 'baostock'),
    source_hash text NOT NULL CHECK (length(source_hash) = 64),
    evidence_path text NOT NULL,
    universe_count integer NOT NULL CHECK (universe_count > 0),
    status_count integer NOT NULL CHECK (status_count >= 0),
    metadata jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS qd_ashare_security_receipts_day
    ON qd_ashare_security_receipts(trading_day, received_at DESC);
CREATE TABLE IF NOT EXISTS qd_ashare_universe_members (
    receipt_id text NOT NULL REFERENCES qd_ashare_security_receipts(id),
    symbol text NOT NULL,
    details jsonb NOT NULL,
    PRIMARY KEY(receipt_id, symbol)
);
CREATE TABLE IF NOT EXISTS qd_ashare_security_statuses (
    receipt_id text NOT NULL REFERENCES qd_ashare_security_receipts(id),
    symbol text NOT NULL,
    details jsonb NOT NULL,
    PRIMARY KEY(receipt_id, symbol)
);
CREATE INDEX IF NOT EXISTS qd_ashare_security_status_symbol
    ON qd_ashare_security_statuses(symbol, receipt_id);
