"""
カテゴリー別ランキングの生成と、前週との順位変動の記録。

ranking_history テーブルは過去の行を一切削除しない。毎週 run_id (ランキング
作成日) ごとに新しい行を追加していくだけなので、このテーブル自体が
「モデルの予測がどう変化してきたか」の完全な記録になる。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database

CATEGORY_LABELS = {
    "weekly_top10": "🔥 今週の注目株",
    "double_5y": "5年2倍候補",
    "triple_5y": "5年3倍候補",
    "turnaround": "業績回復型",
    "high_growth": "高成長型",
    "value_growth": "割安成長型",
    "high_roe": "高ROE型",
    "high_margin": "高利益率型",
    "revision_up": "決算上方修正型",
    "price_pullback": "株価調整中・業績堅調",
}


def _is_turnaround(snap: dict, scored: dict) -> bool:
    rules = settings.TURNAROUND_RULES
    net_growth = snap.get("net_profit_growth_yoy")
    per = snap.get("per")
    drawdown = snap.get("drawdown_from_52w_high")
    forecast_growth = snap.get("forecast_net_profit_growth")

    growth_ok = (net_growth is not None and net_growth >= rules["min_profit_growth_latest"]) or (
        forecast_growth is not None and forecast_growth >= rules["min_profit_growth_latest"]
    )
    per_ok = per is None or per <= rules["max_per_for_turnaround"]
    drawdown_ok = drawdown is not None and drawdown >= rules["min_drawdown_from_high"]
    return bool(growth_ok and per_ok and drawdown_ok)


def build_categories(candidates: list[dict]) -> dict[str, list[dict]]:
    """candidates: [{**snapshot, **scored}, ...]。カテゴリーごとの銘柄リストを返す。"""
    by_total = sorted(candidates, key=lambda c: c["total_score"], reverse=True)

    def has_multiple(c: dict, scenario: str, min_multiple: float) -> bool:
        m = c.get("forecast_multiples", {}).get(scenario)
        return m is not None and m >= min_multiple

    categories: dict[str, list[dict]] = {}
    categories["weekly_top10"] = by_total[: settings.WEEKLY_HIGHLIGHT_COUNT]
    categories["double_5y"] = [c for c in by_total if has_multiple(c, "base", 2.0)][:20]
    categories["triple_5y"] = [c for c in by_total if has_multiple(c, "base", 3.0)][:20]
    categories["turnaround"] = [c for c in by_total if _is_turnaround(c, c)][:20]
    categories["high_growth"] = sorted(by_total, key=lambda c: c["growth_score"], reverse=True)[:20]
    categories["value_growth"] = sorted(
        by_total, key=lambda c: (c["growth_score"] + c["valuation_score"]), reverse=True
    )[:20]
    categories["high_roe"] = sorted(
        [c for c in by_total if c.get("roe") is not None], key=lambda c: c["roe"], reverse=True
    )[:20]
    categories["high_margin"] = sorted(
        [c for c in by_total if c.get("operating_margin") is not None],
        key=lambda c: c["operating_margin"],
        reverse=True,
    )[:20]
    categories["revision_up"] = sorted(
        [c for c in by_total if (c.get("forecast_net_profit_growth") or -999) > 0],
        key=lambda c: c.get("forecast_net_profit_growth") or 0,
        reverse=True,
    )[:20]
    categories["price_pullback"] = sorted(
        [c for c in by_total if (c.get("drawdown_from_52w_high") or 0) > 0.15 and c["growth_score"] >= 15],
        key=lambda c: c["price_position_score"],
        reverse=True,
    )[:20]
    categories["overall"] = by_total  # 全候補（内部保持用、詳細ページ生成などに使う）
    return categories


def _reason_for(c: dict) -> str:
    """順位変動の理由を、数値データから機械的に組み立てる。"""
    parts = []
    if c.get("net_profit_growth_yoy") is not None and c["net_profit_growth_yoy"] > 0.10:
        parts.append("決算増益")
    if c.get("forecast_net_profit_growth") is not None and c["forecast_net_profit_growth"] > 0.10:
        parts.append("通期増益予想")
    if c.get("drawdown_from_52w_high") is not None and c["drawdown_from_52w_high"] > 0.20:
        parts.append("株価調整による割安度上昇")
    if c.get("peg") is not None and c["peg"] < 1.0:
        parts.append("PEG1倍未満")
    if not parts:
        sub_scores = {
            "成長性": c.get("growth_score", 0),
            "割安性": c.get("valuation_score", 0),
            "収益性": c.get("profitability_score", 0),
        }
        top_key = max(sub_scores, key=sub_scores.get)
        parts.append(f"{top_key}スコアが優位")
    return "＋".join(parts[:3])


def get_previous_run_id(conn: sqlite3.Connection, category: str, before_run_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT run_id FROM ranking_history WHERE category = ? AND run_id < ? ORDER BY run_id DESC LIMIT 1",
        (category, before_run_id),
    ).fetchone()
    return row["run_id"] if row else None


def save_ranking_history(conn: sqlite3.Connection, run_id: str, categories: dict[str, list[dict]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for category, items in categories.items():
        prev_run_id = get_previous_run_id(conn, category, run_id)
        prev_ranks: dict[str, int] = {}
        if prev_run_id:
            rows = conn.execute(
                "SELECT code, rank FROM ranking_history WHERE run_id = ? AND category = ?",
                (prev_run_id, category),
            ).fetchall()
            prev_ranks = {r["code"]: r["rank"] for r in rows}

        # NEW判定用: 過去N週以内に一度でも登場していたか
        recent_codes: set[str] = set()
        if not prev_run_id:
            pass
        else:
            cutoff_rows = conn.execute(
                "SELECT DISTINCT run_id FROM ranking_history WHERE category = ? AND run_id <= ? ORDER BY run_id DESC LIMIT ?",
                (category, run_id, settings.RANK_CHANGE_NEW_THRESHOLD_WEEKS),
            ).fetchall()
            run_ids = [r["run_id"] for r in cutoff_rows if r["run_id"] != run_id]
            if run_ids:
                placeholders = ",".join(["?"] * len(run_ids))
                recent_rows = conn.execute(
                    f"SELECT DISTINCT code FROM ranking_history WHERE category = ? AND run_id IN ({placeholders})",
                    (category, *run_ids),
                ).fetchall()
                recent_codes = {r["code"] for r in recent_rows}

        rows_to_save = []
        for idx, c in enumerate(items, start=1):
            code = c["code"]
            prev_rank = prev_ranks.get(code)
            if prev_rank is not None:
                if idx < prev_rank:
                    rank_change = "up"
                elif idx > prev_rank:
                    rank_change = "down"
                else:
                    rank_change = "same"
            else:
                rank_change = "new" if code not in recent_codes else "re-entry"

            rows_to_save.append(
                {
                    "run_id": run_id,
                    "category": category,
                    "code": code,
                    "rank": idx,
                    "score": c.get("total_score"),
                    "price": c.get("price"),
                    "prev_rank": prev_rank,
                    "rank_change": rank_change,
                    "reason": _reason_for(c),
                    "created_at": now,
                }
            )
        database.upsert(conn, "ranking_history", rows_to_save, ["run_id", "category", "code"])
