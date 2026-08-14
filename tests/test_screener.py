import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import database, screener


def test_run_screening_isolates_bad_data_for_single_code(tmp_path):
    """1銘柄の決算データに想定外の型（文字列プレースホルダ）が混ざっていても、
    スクリーニング全体がクラッシュせず、その銘柄だけ除外されることを確認する。
    実際のライブ実行でJ-QuantsのFNP項目が"-"のまま計算に渡り
    TypeError: unsupported operand type(s) for /: 'str' and 'float' で
    パイプライン全体が落ちた障害の再発防止テスト。"""
    db_path = tmp_path / "test.db"
    database.init_db(db_path)

    with database.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO companies (code, name, is_active) VALUES ('9999', 'テスト株式会社', 1)"
        )
        conn.execute(
            """
            INSERT INTO prices (code, date, close, adj_close, market_cap)
            VALUES ('9999', '2026-05-21', 1000.0, 1000.0, 50000000000)
            """
        )
        # わざと文字列プレースホルダを混入させる（load_financials の正規化を
        # バイパスして、DBに直接そのような値が入っていた場合を模擬）。
        conn.execute(
            """
            INSERT INTO financials (
                code, disclosure_date, period_type, net_profit, forecast_net_profit
            ) VALUES ('9999', '2026-05-15', 'FY', 500.0, '-')
            """
        )
        candidates = screener.run_screening(conn, "2026-08-15", as_of_date="2026-08-15")
        errors = conn.execute(
            "SELECT * FROM errors WHERE stage = 'screener.run_screening'"
        ).fetchall()

    assert isinstance(candidates, list)  # クラッシュせず戻り値が返る（例外が伝播しない）
    assert "9999" not in {c["code"] for c in candidates}  # 異常データの銘柄は除外される
    assert len(errors) == 1  # エラーとして記録され、次コードの処理は継続される
