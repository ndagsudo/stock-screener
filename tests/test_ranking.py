import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import database, ranking


def _candidate(code, total_score, **overrides):
    c = {
        "code": code,
        "name": f"Company {code}",
        "price": 1000.0,
        "total_score": total_score,
        "growth_score": total_score * 0.3,
        "valuation_score": total_score * 0.25,
        "profitability_score": total_score * 0.15,
        "health_score": total_score * 0.1,
        "momentum_score": total_score * 0.1,
        "price_position_score": total_score * 0.1,
        "roe": 0.1,
        "operating_margin": 0.1,
        "net_profit_growth_yoy": 0.05,
        "forecast_net_profit_growth": 0.05,
        "drawdown_from_52w_high": 0.1,
        "per": 15.0,
        "peg": 1.0,
        "forecast_multiples": {"base": 1.5},
    }
    c.update(overrides)
    return c


def test_build_categories_weekly_top10_is_sorted_by_score():
    candidates = [_candidate(str(1000 + i), score) for i, score in enumerate([50, 90, 70, 60, 80])]
    categories = ranking.build_categories(candidates)
    scores = [c["total_score"] for c in categories["weekly_top10"]]
    assert scores == sorted(scores, reverse=True)


def test_double_and_triple_5y_filters_by_multiple():
    candidates = [
        _candidate("1001", 80, forecast_multiples={"base": 2.5}),
        _candidate("1002", 75, forecast_multiples={"base": 1.2}),
        _candidate("1003", 70, forecast_multiples={"base": 3.5}),
    ]
    categories = ranking.build_categories(candidates)
    double_codes = {c["code"] for c in categories["double_5y"]}
    triple_codes = {c["code"] for c in categories["triple_5y"]}
    assert double_codes == {"1001", "1003"}
    assert triple_codes == {"1003"}


def test_turnaround_requires_growth_low_per_and_drawdown():
    good = _candidate(
        "2001", 60, net_profit_growth_yoy=0.20, per=12.0, drawdown_from_52w_high=0.30
    )
    bad_no_drawdown = _candidate(
        "2002", 60, net_profit_growth_yoy=0.20, per=12.0, drawdown_from_52w_high=0.05
    )
    categories = ranking.build_categories([good, bad_no_drawdown])
    turnaround_codes = {c["code"] for c in categories["turnaround"]}
    assert "2001" in turnaround_codes
    assert "2002" not in turnaround_codes


def test_save_ranking_history_marks_new_and_tracks_rank_change(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)

    week1 = [_candidate("1001", 90), _candidate("1002", 80), _candidate("1003", 70)]
    week2 = [_candidate("1002", 95), _candidate("1001", 85), _candidate("1004", 75)]

    with database.connect(db_path) as conn:
        categories_w1 = {"weekly_top10": sorted(week1, key=lambda c: c["total_score"], reverse=True)}
        ranking.save_ranking_history(conn, "2026-08-08", categories_w1)

    with database.connect(db_path) as conn:
        categories_w2 = {"weekly_top10": sorted(week2, key=lambda c: c["total_score"], reverse=True)}
        ranking.save_ranking_history(conn, "2026-08-15", categories_w2)

    with database.connect(db_path) as conn:
        rows = {
            r["code"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM ranking_history WHERE run_id = ? AND category = 'weekly_top10'",
                ("2026-08-15",),
            ).fetchall()
        }

    # 1002: prev rank2 -> new rank1 = up
    assert rows["1002"]["rank_change"] == "up"
    assert rows["1002"]["prev_rank"] == 2
    # 1001: prev rank1 -> new rank2 = down
    assert rows["1001"]["rank_change"] == "down"
    # 1004: not present last week = new
    assert rows["1004"]["rank_change"] == "new"
    # 1003 dropped out entirely this week
    assert "1003" not in rows


def test_ranking_history_never_overwritten_across_weeks(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        ranking.save_ranking_history(
            conn, "2026-08-08", {"weekly_top10": [_candidate("1001", 90)]}
        )
        ranking.save_ranking_history(
            conn, "2026-08-15", {"weekly_top10": [_candidate("1001", 95)]}
        )
    with database.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM ranking_history WHERE category = 'weekly_top10'"
        ).fetchone()["n"]
    assert count == 2  # 両週の記録が両方残っている
