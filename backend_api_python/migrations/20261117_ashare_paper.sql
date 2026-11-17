-- Opt-in A-share cash paper ledger; never shares balances with live accounts.
CREATE TABLE IF NOT EXISTS qd_ashare_accounts (
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 initial_cash NUMERIC(24,2) NOT NULL CHECK (initial_cash > 0),
 cash NUMERIC(24,2) NOT NULL CHECK (cash >= 0),
 reserved_cash NUMERIC(24,2) NOT NULL DEFAULT 0 CHECK (reserved_cash >= 0 AND reserved_cash <= cash),
 fee_policy JSONB NOT NULL,
 PRIMARY KEY (tenant_id, account_id)
);
CREATE TABLE IF NOT EXISTS qd_ashare_orders (
 id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 client_order_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('buy','sell')),
 quantity BIGINT NOT NULL CHECK (quantity > 0), filled BIGINT NOT NULL DEFAULT 0 CHECK (filled >= 0 AND filled <= quantity),
 limit_price NUMERIC(24,2) NOT NULL CHECK (limit_price > 0),
 submitted_at TIMESTAMPTZ NOT NULL, trading_day DATE NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('open','partial','filled','cancelled','expired')),
 reserved_cash NUMERIC(24,2) NOT NULL DEFAULT 0 CHECK (reserved_cash >= 0),
 notional NUMERIC(24,2) NOT NULL DEFAULT 0,
 commission NUMERIC(24,2) NOT NULL DEFAULT 0,
 transfer_fee NUMERIC(24,2) NOT NULL DEFAULT 0,
 sell_tax NUMERIC(24,2) NOT NULL DEFAULT 0,
 UNIQUE (tenant_id, account_id, client_order_id),
 FOREIGN KEY (tenant_id, account_id) REFERENCES qd_ashare_accounts(tenant_id, account_id)
);
CREATE TABLE IF NOT EXISTS qd_ashare_lots (
 id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 symbol TEXT NOT NULL, acquired_day DATE NOT NULL, quantity BIGINT NOT NULL CHECK (quantity >= 0),
 FOREIGN KEY (tenant_id, account_id) REFERENCES qd_ashare_accounts(tenant_id, account_id)
);
CREATE TABLE IF NOT EXISTS qd_ashare_quotes (
 tenant_id TEXT NOT NULL, account_id TEXT NOT NULL, symbol TEXT NOT NULL,
 exchange_time TIMESTAMPTZ NOT NULL, quote_id TEXT NOT NULL,
 PRIMARY KEY (tenant_id, account_id, symbol, exchange_time),
 UNIQUE (tenant_id, account_id, quote_id),
 FOREIGN KEY (tenant_id, account_id) REFERENCES qd_ashare_accounts(tenant_id, account_id)
);
CREATE TABLE IF NOT EXISTS qd_ashare_fills (
 id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 order_id BIGINT NOT NULL REFERENCES qd_ashare_orders(id), symbol TEXT NOT NULL,
 side TEXT NOT NULL, quantity BIGINT NOT NULL, price NUMERIC(24,2) NOT NULL,
 fee NUMERIC(24,2) NOT NULL, currency TEXT NOT NULL DEFAULT 'CNY' CHECK (currency='CNY'),
 exchange_time TIMESTAMPTZ NOT NULL, quote_id TEXT NOT NULL, evidence_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qd_ashare_audit (
 id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 event TEXT NOT NULL, details JSONB NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS qd_ashare_open_orders ON qd_ashare_orders(tenant_id, account_id, symbol, status);
CREATE INDEX IF NOT EXISTS qd_ashare_lot_account ON qd_ashare_lots(tenant_id, account_id, symbol, acquired_day);
