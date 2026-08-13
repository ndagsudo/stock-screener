import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import database, report


def test_render_site_on_empty_database_does_not_crash(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    site_dir = tmp_path / "site"
    monkeypatch.setattr(report.settings, "SITE_DIR", site_dir)
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        report.render_site(conn, run_id=None)

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    assert "まだデータがありません" in index_html
    assert "データなし" in index_html


def test_render_site_with_ranking_data(tmp_path, monkeypatch):
    from src import ranking

    db_path = tmp_path / "test.db"
    site_dir = tmp_path / "site"
    monkeypatch.setattr(report.settings, "SITE_DIR", site_dir)
    database.init_db(db_path)

    with database.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO companies (code, name, is_active) VALUES ('1001', 'テスト株式会社', 1)"
        )
        conn.execute(
            "INSERT INTO updates (run_id, started_at, finished_at, status, candidates_screened, "
            "candidates_scored, ai_analyzed) VALUES ('2026-08-15', 'x', 'y', 'success', 1, 1, 0)"
        )
        candidate = {
            "code": "1001",
            "price": 1000.0,
            "total_score": 80.0,
            "growth_score": 24.0,
            "valuation_score": 20.0,
            "profitability_score": 12.0,
            "health_score": 8.0,
            "momentum_score": 8.0,
            "price_position_score": 8.0,
        }
        ranking.save_ranking_history(conn, "2026-08-15", {"weekly_top10": [candidate]})
        report.render_site(conn, run_id="2026-08-15")

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    assert "テスト株式会社" in index_html
    assert (site_dir / "stocks" / "1001.html").exists()
