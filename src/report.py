"""
静的HTML生成（GitHub Pages公開用）。

このプロジェクトにWebサーバー/バックエンドAPIは存在しない。ここで
templates/*.html を Jinja2 でレンダリングし site/ 配下に書き出すだけで
サイトが完成する。
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database, ai_analysis
from src.ranking import CATEGORY_LABELS

from jinja2 import Environment, FileSystemLoader, select_autoescape, Undefined


def _none_if_missing(v):
    """Jinja2のUndefined（辞書にキーが存在しない場合）もNone扱いにする。
    テンプレート側で毎回 .get() を書かなくても『データなし』に倒れるようにする。"""
    return None if isinstance(v, Undefined) else v


def _fmt_pct(v, digits=1):
    v = _none_if_missing(v)
    if v is None:
        return "データなし"
    return f"{v * 100:.{digits}f}%"


def _fmt_num(v, digits=1):
    v = _none_if_missing(v)
    if v is None:
        return "データなし"
    return f"{v:,.{digits}f}"


def _fmt_yen_oku(v):
    v = _none_if_missing(v)
    if v is None:
        return "データなし"
    return f"{v / 100_000_000:,.0f}億円"


def _fmt_price(v):
    v = _none_if_missing(v)
    if v is None:
        return "データなし"
    return f"{v:,.0f}円"


def _rank_arrow(rank_change: str) -> str:
    return {"up": "↑", "down": "↓", "same": "→", "new": "NEW", "re-entry": "↺"}.get(rank_change, "")


def get_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(settings.TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["pct"] = _fmt_pct
    env.filters["num"] = _fmt_num
    env.filters["yen_oku"] = _fmt_yen_oku
    env.filters["price"] = _fmt_price
    env.filters["arrow"] = _rank_arrow
    return env


def get_latest_run_id(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT run_id FROM updates ORDER BY run_id DESC LIMIT 1").fetchone()
    return row["run_id"] if row else None


def _category_rows(conn: sqlite3.Connection, run_id: str, category: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT rh.*, c.name AS company_name, ss.indicators_json,
               ss.growth_score, ss.valuation_score, ss.profitability_score,
               ss.health_score, ss.momentum_score, ss.price_position_score
        FROM ranking_history rh
        LEFT JOIN companies c ON c.code = rh.code
        LEFT JOIN screening_scores ss ON ss.run_id = rh.run_id AND ss.code = rh.code
        WHERE rh.run_id = ? AND rh.category = ?
        ORDER BY rh.rank ASC
        """,
        (run_id, category),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        indicators = database.from_json(d.pop("indicators_json"), {})
        d["indicators"] = indicators
        d["rank_change_display"] = _rank_arrow(d.get("rank_change"))
        result.append(d)
    return result


def load_run_context(conn: sqlite3.Connection, run_id: str) -> dict:
    categories = {cat: _category_rows(conn, run_id, cat) for cat in CATEGORY_LABELS}
    update_row = conn.execute("SELECT * FROM updates WHERE run_id = ?", (run_id,)).fetchone()
    return {
        "run_id": run_id,
        "categories": categories,
        "category_labels": CATEGORY_LABELS,
        "run_update": dict(update_row) if update_row else None,
    }


def load_history_context(conn: sqlite3.Connection, category: str = "weekly_top10", limit_runs: int = 52) -> list[dict]:
    run_ids = [
        r["run_id"]
        for r in conn.execute(
            "SELECT DISTINCT run_id FROM ranking_history WHERE category = ? ORDER BY run_id DESC LIMIT ?",
            (category, limit_runs),
        ).fetchall()
    ]
    history = []
    for run_id in run_ids:
        history.append({"run_id": run_id, "entries": _category_rows(conn, run_id, category)})
    return history


def load_stock_context(conn: sqlite3.Connection, code: str, run_id: Optional[str] = None) -> Optional[dict]:
    company = conn.execute("SELECT * FROM companies WHERE code = ?", (code,)).fetchone()
    if not company:
        return None
    run_id = run_id or get_latest_run_id(conn)

    scored_row = conn.execute(
        "SELECT * FROM screening_scores WHERE code = ? AND run_id = ?", (code, run_id)
    ).fetchone()
    indicators = database.from_json(scored_row["indicators_json"], {}) if scored_row else {}

    forecasts = conn.execute(
        "SELECT * FROM forecasts WHERE code = ? AND run_id = ?", (code, run_id)
    ).fetchall()
    forecast_by_scenario = {f["scenario"]: dict(f) for f in forecasts}

    fin_history = conn.execute(
        "SELECT * FROM financials WHERE code = ? ORDER BY disclosure_date ASC", (code,)
    ).fetchall()
    price_history = conn.execute(
        "SELECT date, close, adj_close FROM prices WHERE code = ? ORDER BY date ASC", (code,)
    ).fetchall()

    ai = ai_analysis.get_latest_analysis(conn, code)

    scored_dict = dict(scored_row) if scored_row else None
    manual_review_prompt = None
    if indicators and scored_dict:
        manual_review_prompt = ai_analysis.build_manual_review_prompt(indicators, scored_dict)

    rank_rows = conn.execute(
        """
        SELECT category, rank, rank_change, prev_rank FROM ranking_history
        WHERE code = ? AND run_id = ?
        """,
        (code, run_id),
    ).fetchall()

    return {
        "company": dict(company),
        "run_id": run_id,
        "indicators": indicators,
        "scored": scored_dict,
        "forecasts": forecast_by_scenario,
        "financial_history": [dict(r) for r in fin_history],
        "price_history": [dict(r) for r in price_history],
        "ai": ai,
        "manual_review_prompt": manual_review_prompt,
        "rankings": [dict(r) for r in rank_rows],
    }


def load_performance_context(conn: sqlite3.Connection) -> dict:
    from src import performance

    return performance.compute_recent_performance(conn)


def render_site(conn: sqlite3.Connection, run_id: Optional[str] = None) -> None:
    run_id = run_id or get_latest_run_id(conn)
    env = get_jinja_env()
    settings.SITE_DIR.mkdir(parents=True, exist_ok=True)
    (settings.SITE_DIR / "stocks").mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    if run_id is None:
        # データがまだ無い状態でも落ちずに空サイトを出す
        run_context = {"run_id": None, "categories": {c: [] for c in CATEGORY_LABELS}, "category_labels": CATEGORY_LABELS, "run_update": None}
        empty_stats = {"count": 0, "avg_return": None, "median_return": None, "positive_ratio": None}
        perf = {
            "top1": dict(empty_stats),
            "top3": dict(empty_stats),
            "top10": dict(empty_stats),
            "benchmark": None,
            "window_months": settings.PERFORMANCE_WINDOW_MONTHS,
        }
    else:
        run_context = load_run_context(conn, run_id)
        perf = load_performance_context(conn)

    common = {
        "generated_at": now_iso,
        "site_title": "日本中小型株スクリーナー（投資版 miyagi-kids）",
        "settings": settings,
    }

    index_tmpl = env.get_template("index.html")
    (settings.SITE_DIR / "index.html").write_text(
        index_tmpl.render(**common, run=run_context, performance=perf), encoding="utf-8"
    )

    rankings_tmpl = env.get_template("rankings.html")
    (settings.SITE_DIR / "rankings.html").write_text(
        rankings_tmpl.render(**common, run=run_context, category_labels=CATEGORY_LABELS), encoding="utf-8"
    )

    history_tmpl = env.get_template("history.html")
    history_data = load_history_context(conn) if run_id else []
    (settings.SITE_DIR / "history.html").write_text(
        history_tmpl.render(**common, history=history_data, category_labels=CATEGORY_LABELS), encoding="utf-8"
    )

    methodology_tmpl = env.get_template("methodology.html")
    (settings.SITE_DIR / "methodology.html").write_text(
        methodology_tmpl.render(**common), encoding="utf-8"
    )

    if run_id:
        stock_tmpl = env.get_template("stock.html")
        codes = {row["code"] for cat_rows in run_context["categories"].values() for row in cat_rows}
        for code in codes:
            stock_ctx = load_stock_context(conn, code, run_id)
            if stock_ctx is None:
                continue
            (settings.SITE_DIR / "stocks" / f"{code}.html").write_text(
                stock_tmpl.render(**common, stock=stock_ctx), encoding="utf-8"
            )

    static_dest = settings.SITE_DIR / "static"
    if static_dest.exists():
        shutil.rmtree(static_dest)
    shutil.copytree(settings.STATIC_DIR, static_dest)
