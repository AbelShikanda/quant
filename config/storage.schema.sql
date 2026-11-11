-- Gold Quantamental Bot — SQLite schema (v1)
-- Single writer, WAL mode. Do not run multiple writer processes.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scope_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id TEXT NOT NULL,
    scope_version INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL,
    applied_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE(scope_id, scope_version, manifest_hash)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    scope_id TEXT NOT NULL,
    scope_version INTEGER NOT NULL,
    captured_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    execution_bar TEXT NOT NULL,
    context_bar TEXT NOT NULL,
    market_json TEXT NOT NULL,
    fundamentals_json TEXT NOT NULL,
    technicals_json TEXT NOT NULL,
    fusion_json TEXT NOT NULL,
    overseer_json TEXT NOT NULL,
    source_versions_json TEXT NOT NULL,
    data_quality_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    scope_version INTEGER NOT NULL,
    decision_type TEXT NOT NULL,
    side TEXT NOT NULL,
    confidence REAL,
    entry_score REAL,
    fundamental_score REAL,
    technical_score REAL,
    overseer_verdict TEXT NOT NULL,
    veto_reasons_json TEXT NOT NULL,
    exit_plan_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL UNIQUE,
    decision_id TEXT NOT NULL,
    submitted_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    volume REAL NOT NULL,
    price REAL,
    stop_loss REAL,
    take_profit REAL,
    deviation_points INTEGER,
    magic INTEGER,
    comment TEXT,
    state TEXT NOT NULL,
    broker_order_ticket INTEGER,
    idempotency_key TEXT NOT NULL UNIQUE,
    raw_request_json TEXT NOT NULL,
    raw_response_json TEXT NOT NULL,
    FOREIGN KEY(decision_id) REFERENCES decisions(decision_id)
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL,
    filled_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    volume REAL NOT NULL,
    price REAL NOT NULL,
    commission REAL NOT NULL DEFAULT 0,
    swap REAL NOT NULL DEFAULT 0,
    slippage_points REAL,
    broker_deal_ticket INTEGER,
    raw_deal_json TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    volume REAL NOT NULL,
    open_price REAL NOT NULL,
    open_time_utc TEXT NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    close_price REAL,
    close_time_utc TEXT,
    close_reason TEXT,
    realized_pnl REAL,
    mae REAL,
    mfe REAL,
    broker_position_ticket INTEGER,
    state TEXT NOT NULL,
    raw_position_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at_utc TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    event_type TEXT NOT NULL,
    correlation_id TEXT,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL UNIQUE,
    opened_at_utc TEXT NOT NULL,
    closed_at_utc TEXT,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    resolved_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_captured_at ON snapshots(captured_at_utc);
CREATE INDEX IF NOT EXISTS idx_decisions_decided_at ON decisions(decided_at_utc);
CREATE INDEX IF NOT EXISTS idx_orders_submitted_at ON orders(submitted_at_utc);
CREATE INDEX IF NOT EXISTS idx_fills_filled_at ON fills(filled_at_utc);
CREATE INDEX IF NOT EXISTS idx_positions_open_time ON positions(open_time_utc);
CREATE INDEX IF NOT EXISTS idx_logs_logged_at ON logs(logged_at_utc);
CREATE INDEX IF NOT EXISTS idx_incidents_opened_at ON incidents(opened_at_utc);
