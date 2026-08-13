import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import database, performance


def _insert_price(conn, code, date, close):
    conn.execute(
        "INSERT OR REPLACE INTO prices (code, date, close, adj_close) VALUES (?, ?, ?, ?)",
        (code, date, close, close),
    )


def test_register_forecast_targets_creates_rows_for_each_horizon(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        inserted = performance.register_forecast_targets(
            conn, "2026-08-15", "2026-08-15", [{"code": "1001", "price": 1000.0, "rank": 1, "total_score": 90}]
        )
    assert inserted == 5  # 1w/1m/3m/6m/1y

    with database.connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM forecast_results WHERE code = '1001'").fetchall()
    assert len(rows) == 5
    assert all(r["realized_price"] is None for r in rows)


def test_register_forecast_targets_does_not_overwrite_existing_realized(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        performance.register_forecast_targets(
            conn, "2026-08-15", "2026-08-15", [{"code": "1001", "price": 1000.0, "rank": 1, "total_score": 90}]
        )
        conn.execute(
            "UPDATE forecast_results SET realized_price = 1200, realized_return = 0.2 "
            "WHERE run_id = '2026-08-15' AND code = '1001' AND horizon = '1w'"
        )
        # 同じ内容で再登録しても上書きされない
        performance.register_forecast_targets(
            conn, "2026-08-15", "2026-08-15", [{"code": "1001", "price": 1000.0, "rank": 1, "total_score": 90}]
        )
        row = conn.execute(
            "SELECT * FROM forecast_results WHERE run_id = '2026-08-15' AND code = '1001' AND horizon = '1w'"
        ).fetchone()
    assert row["realized_price"] == 1200


def test_update_realized_returns_computes_return_after_target_date(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        _insert_price(conn, "1001", "2026-08-15", 1000.0)
        _insert_price(conn, "1001", "2026-08-22", 1100.0)
        performance.register_forecast_targets(
            conn, "2026-08-15", "2026-08-15", [{"code": "1001", "price": 1000.0, "rank": 1, "total_score": 90}]
        )
        updated = performance.update_realized_returns(conn, as_of_date="2026-08-25")
    assert updated >= 1  # 少なくとも1w分は確定しているはず

    with database.connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM forecast_results WHERE run_id = '2026-08-15' AND code = '1001' AND horizon = '1w'"
        ).fetchone()
    assert row["realized_price"] == 1100.0
    assert round(row["realized_return"], 4) == round(0.1, 4)


def test_update_realized_returns_skips_future_targets(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        _insert_price(conn, "1001", "2026-08-15", 1000.0)
        performance.register_forecast_targets(
            conn, "2026-08-15", "2026-08-15", [{"code": "1001", "price": 1000.0, "rank": 1, "total_score": 90}]
        )
        # まだ1年後(1y)のホライズンは到来していない
        performance.update_realized_returns(conn, as_of_date="2026-08-16")
        row = conn.execute(
            "SELECT * FROM forecast_results WHERE run_id = '2026-08-15' AND code = '1001' AND horizon = '1y'"
        ).fetchone()
    assert row["realized_price"] is None


def test_compute_recent_performance_stats(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        _insert_price(conn, "1001", "2026-08-15", 1200.0)  # +20%
        _insert_price(conn, "1002", "2026-08-15", 900.0)  # -10%
        conn.execute(
            "INSERT INTO ranking_history (run_id, category, code, rank, score, price, created_at) "
            "VALUES ('2026-07-01', 'weekly_top10', '1001', 1, 90, 1000.0, '2026-07-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO ranking_history (run_id, category, code, rank, score, price, created_at) "
            "VALUES ('2026-07-01', 'weekly_top10', '1002', 2, 80, 1000.0, '2026-07-01T00:00:00')"
        )
        result = performance.compute_recent_performance(conn, months=6, as_of_date="2026-08-15")

    assert result["top1"]["count"] == 1
    assert round(result["top1"]["avg_return"], 2) == 0.2
    assert result["top3"]["count"] == 2
    assert round(result["top3"]["avg_return"], 2) == round((0.2 + (-0.1)) / 2, 2)
