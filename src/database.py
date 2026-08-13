"""
SQLite アクセス層。

このプロジェクトには常駐サーバーがないため、data/stock.db は Git リポジトリに
コミットされ、GitHub Actions の週次実行のたびに更新・コミットされる。
これにより ranking_history / forecast_results などの「過去の記録」が
Actions のジョブが終わるたびに消えることなく蓄積される。

ここでは「過去の行を上書きで消してよいテーブル」と
「絶対に削除してはいけない履歴テーブル」を明確に分ける。
削除禁止: ranking_history, forecast_results, ai_analyses, sources, updates, errors
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings

SCHEMA = """
-- 銘柄マスタ（最新状態を保持。上場廃止等は is_active=0 にするだけで行は消さない）
CREATE TABLE IF NOT EXISTS companies (
    code TEXT PRIMARY KEY,
    name TEXT,
    name_en TEXT,
    sector17 TEXT,
    sector17_name TEXT,
    sector33 TEXT,
    sector33_name TEXT,
    market TEXT,
    market_name TEXT,
    scale_category TEXT,
    product_category TEXT,
    is_active INTEGER DEFAULT 1,
    first_seen_date TEXT,
    last_updated_date TEXT
);

-- 日次株価（四本値・調整後・時価総額）
CREATE TABLE IF NOT EXISTS prices (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    adj_open REAL, adj_high REAL, adj_low REAL, adj_close REAL,
    volume REAL, adj_volume REAL,
    turnover_value REAL,
    market_cap REAL,
    adj_factor REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_code_date ON prices(code, date);

-- 決算情報（開示単位で1行）
CREATE TABLE IF NOT EXISTS financials (
    code TEXT NOT NULL,
    disclosure_date TEXT NOT NULL,
    doc_type TEXT,
    period_type TEXT,
    period_start TEXT,
    period_end TEXT,
    sales REAL,
    operating_profit REAL,
    ordinary_profit REAL,
    net_profit REAL,
    eps REAL,
    total_assets REAL,
    equity REAL,
    equity_ratio REAL,
    bps REAL,
    roe REAL,
    forecast_sales REAL,
    forecast_operating_profit REAL,
    forecast_ordinary_profit REAL,
    forecast_net_profit REAL,
    forecast_eps REAL,
    shares_outstanding REAL,
    raw_json TEXT,
    PRIMARY KEY (code, disclosure_date, period_type)
);
CREATE INDEX IF NOT EXISTS idx_financials_code ON financials(code, disclosure_date);

-- 配当情報
CREATE TABLE IF NOT EXISTS dividends (
    code TEXT NOT NULL,
    ref_no TEXT NOT NULL,
    pub_date TEXT,
    ex_date TEXT,
    record_date TEXT,
    pay_date TEXT,
    if_code TEXT,
    fr_code TEXT,
    if_term TEXT,
    div_rate TEXT,
    PRIMARY KEY (code, ref_no)
);

-- 決算発表予定
CREATE TABLE IF NOT EXISTS earnings_schedule (
    code TEXT NOT NULL,
    announce_date TEXT,
    fiscal_year_end TEXT,
    fiscal_quarter TEXT,
    PRIMARY KEY (code, announce_date, fiscal_quarter)
);

-- 数値スクリーニングの通過記録（run_idごと・履歴保持）
CREATE TABLE IF NOT EXISTS screening_results (
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    passed INTEGER NOT NULL,
    reasons_json TEXT,
    created_at TEXT,
    PRIMARY KEY (run_id, code)
);

-- スコアリング内訳（run_idごと・履歴保持）
CREATE TABLE IF NOT EXISTS screening_scores (
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    growth_score REAL,
    valuation_score REAL,
    profitability_score REAL,
    health_score REAL,
    momentum_score REAL,
    price_position_score REAL,
    total_score REAL,
    indicators_json TEXT,
    created_at TEXT,
    PRIMARY KEY (run_id, code)
);

-- 5年シナリオ試算（run_idごと・履歴保持）
CREATE TABLE IF NOT EXISTS forecasts (
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    scenario TEXT NOT NULL,
    current_eps REAL,
    growth_rate_used REAL,
    years INTEGER,
    future_eps REAL,
    exit_per REAL,
    future_price REAL,
    current_price REAL,
    multiple REAL,
    cagr REAL,
    PRIMARY KEY (run_id, code, scenario)
);

-- 予測検証用：ランキング時点のスナップショットと将来実績（絶対に削除しない）
CREATE TABLE IF NOT EXISTS forecast_results (
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    horizon TEXT NOT NULL,
    ranking_date TEXT,
    rank_at_ranking INTEGER,
    score_at_ranking REAL,
    price_at_ranking REAL,
    horizon_target_date TEXT,
    realized_price REAL,
    realized_return REAL,
    realized_at TEXT,
    PRIMARY KEY (run_id, code, horizon)
);

-- AI定性分析結果（キャッシュ。銘柄ごとに複数回分を履歴として保持）
CREATE TABLE IF NOT EXISTS ai_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    run_id TEXT,
    analysis_date TEXT,
    why_notable TEXT,
    bull_points_json TEXT,
    bear_points_json TEXT,
    checkpoints_json TEXT,
    growth_drivers_json TEXT,
    competitive_advantages_json TEXT,
    overall_comment TEXT,
    model TEXT,
    input_hash TEXT,
    financials_disclosure_date TEXT,
    rank_at_analysis INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_code ON ai_analyses(code, created_at);

-- AI分析の情報源
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    code TEXT,
    source_type TEXT,
    title TEXT,
    url TEXT,
    retrieved_date TEXT,
    FOREIGN KEY (analysis_id) REFERENCES ai_analyses(id)
);

-- 週次ランキング履歴（カテゴリー別。絶対に削除しない）
CREATE TABLE IF NOT EXISTS ranking_history (
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    code TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL,
    price REAL,
    prev_rank INTEGER,
    rank_change TEXT,
    reason TEXT,
    created_at TEXT,
    PRIMARY KEY (run_id, category, code)
);
CREATE INDEX IF NOT EXISTS idx_ranking_history_cat ON ranking_history(category, run_id);
CREATE INDEX IF NOT EXISTS idx_ranking_history_code ON ranking_history(code);

-- 実行ログ（絶対に削除しない）
CREATE TABLE IF NOT EXISTS updates (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    candidates_screened INTEGER,
    candidates_scored INTEGER,
    ai_analyzed INTEGER,
    notes TEXT
);

-- エラーログ（非致命的エラーを記録し、処理は止めない。絶対に削除しない）
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    stage TEXT,
    code TEXT,
    message TEXT,
    created_at TEXT
);
"""


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or settings.DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connect(db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict], key_columns: list[str]) -> int:
    """辞書のリストを指定テーブルに INSERT OR REPLACE する汎用ヘルパー。"""
    rows = list(rows)
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(columns))
    col_str = ",".join(columns)
    sql = f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES ({placeholders})"
    values = [tuple(row.get(c) for c in columns) for row in rows]
    conn.executemany(sql, values)
    return len(rows)


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def log_error(conn: sqlite3.Connection, run_id: str, stage: str, message: str, code: Optional[str] = None) -> None:
    from datetime import datetime, timezone

    conn.execute(
        "INSERT INTO errors (run_id, stage, code, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, stage, code, str(message)[:2000], datetime.now(timezone.utc).isoformat()),
    )
