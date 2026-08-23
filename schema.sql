CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    password_hashes JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS portfolio (
    id BIGSERIAL PRIMARY KEY,
    client_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    mtm DOUBLE PRECISION,
    ltp DOUBLE PRECISION,
    quantity DOUBLE PRECISION,
    buy_price DOUBLE PRECISION,
    realised_profit DOUBLE PRECISION,
    change_value DOUBLE PRECISION,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_client ON portfolio(client_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_client_symbol ON portfolio(client_id, symbol);

CREATE TABLE IF NOT EXISTS change_requests (
    id BIGSERIAL PRIMARY KEY,
    client_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    excel_row BIGINT,
    field TEXT NOT NULL,
    old_value DOUBLE PRECISION,
    requested_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    requested_quantity DOUBLE PRECISION,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    admin_remark TEXT
);

CREATE INDEX IF NOT EXISTS idx_change_requests_client ON change_requests(client_id);
CREATE INDEX IF NOT EXISTS idx_change_requests_status ON change_requests(status);
