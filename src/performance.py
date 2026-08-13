"""
予測検証（バックテスト）。

look-ahead bias を避けるための原則:
  - forecast_results に記録する price_at_ranking は、必ずランキング作成日
    (run_id) 時点で入手可能だった価格のみを使う。
  - 実績株価 (realized_price) は、対象ホライズンの target_date が到来した
    「後」でなければ埋めない（未来の情報を過去の行に混ぜない）。
  - 半年間の成績集計も、各時点で実際に発表されていたランキング
    (ranking_history) だけを使い、後から確定した情報で過去の順位を
    書き換えたりしない。
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database


def _add_days(date_str: str, days: int) -> str:
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def register_forecast_targets(conn: sqlite3.Connection, run_id: str, ranking_date: str, ranked_items: list[dict]) -> int:
    """weekly_top10 など主要ランキングに載った銘柄について、将来の実績検証用の
    スナップショット行を forecast_results に登録する。既存行は上書きしない
    （realized_* が既に埋まっている場合に消してしまわないため）。"""
    inserted = 0
    for item in ranked_items:
        code = item["code"]
        price = item.get("price")
        rank = item.get("rank")
        score = item.get("total_score")
        if price is None:
            continue
        for horizon, days in settings.PERFORMANCE_HORIZONS_DAYS.items():
            existing = conn.execute(
                "SELECT 1 FROM forecast_results WHERE run_id = ? AND code = ? AND horizon = ?",
                (run_id, code, horizon),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO forecast_results (
                    run_id, code, horizon, ranking_date, rank_at_ranking, score_at_ranking,
                    price_at_ranking, horizon_target_date, realized_price, realized_return, realized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (run_id, code, horizon, ranking_date, rank, score, price, _add_days(ranking_date, days)),
            )
            inserted += 1
    return inserted


def _price_on_or_before(conn: sqlite3.Connection, code: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT adj_close, close, date FROM prices WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    if row is None:
        return None
    return row["adj_close"] if row["adj_close"] is not None else row["close"]


def update_realized_returns(conn: sqlite3.Connection, as_of_date: Optional[str] = None) -> int:
    """target_date が到来済みで、まだ実績が埋まっていない forecast_results 行を更新する。"""
    as_of = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT rowid, * FROM forecast_results WHERE realized_price IS NULL AND horizon_target_date <= ?",
        (as_of,),
    ).fetchall()
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        price = _price_on_or_before(conn, row["code"], row["horizon_target_date"])
        if price is None or row["price_at_ranking"] in (None, 0):
            continue
        realized_return = (price / row["price_at_ranking"]) - 1.0
        conn.execute(
            """
            UPDATE forecast_results
            SET realized_price = ?, realized_return = ?, realized_at = ?
            WHERE run_id = ? AND code = ? AND horizon = ?
            """,
            (price, realized_return, now, row["run_id"], row["code"], row["horizon"]),
        )
        updated += 1
    return updated


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg_return": None, "median_return": None, "positive_ratio": None}
    return {
        "count": len(values),
        "avg_return": round(statistics.mean(values), 4),
        "median_return": round(statistics.median(values), 4),
        "positive_ratio": round(sum(1 for v in values if v > 0) / len(values), 4),
    }


def compute_recent_performance(
    conn: sqlite3.Connection, months: int = None, as_of_date: Optional[str] = None
) -> dict:
    """過去N ヶ月の weekly_top10 ランキングについて、TOP1/TOP3/TOP10の
    「ランキング時点の価格」から「現時点(as_of_date)の価格」までのリターンを集計する。
    as_of_date より後に確定した情報は一切使わない。"""
    months = months or settings.PERFORMANCE_WINDOW_MONTHS
    as_of = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    rows = conn.execute(
        """
        SELECT run_id, code, rank, price FROM ranking_history
        WHERE category = 'weekly_top10' AND run_id >= ? AND run_id <= ?
        ORDER BY run_id ASC
        """,
        (cutoff, as_of),
    ).fetchall()

    top1_returns, top3_returns, top10_returns = [], [], []
    details = []
    for r in rows:
        if r["price"] is None:
            continue
        current_price = _price_on_or_before(conn, r["code"], as_of)
        if current_price is None:
            continue
        ret = (current_price / r["price"]) - 1.0
        details.append(
            {
                "run_id": r["run_id"],
                "code": r["code"],
                "rank": r["rank"],
                "price_at_ranking": r["price"],
                "current_price": current_price,
                "return": round(ret, 4),
            }
        )
        if r["rank"] == 1:
            top1_returns.append(ret)
        if r["rank"] <= 3:
            top3_returns.append(ret)
        if r["rank"] <= 10:
            top10_returns.append(ret)

    return {
        "window_months": months,
        "as_of_date": as_of,
        "cutoff_date": cutoff,
        "top1": _stats(top1_returns),
        "top3": _stats(top3_returns),
        "top10": _stats(top10_returns),
        "details": details,
        "benchmark": compute_benchmark_return(conn, cutoff, as_of),
    }


def compute_benchmark_return(conn: sqlite3.Connection, from_date: str, to_date: str) -> Optional[dict]:
    """TOPIX等ベンチマークのリターン。指数データを取得していない場合は None
    （データなし、として report 側で表示する）。"""
    row_from = conn.execute(
        "SELECT close FROM prices WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (settings.BENCHMARK_CODE, from_date),
    ).fetchone()
    row_to = conn.execute(
        "SELECT close FROM prices WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (settings.BENCHMARK_CODE, to_date),
    ).fetchone()
    if not row_from or not row_to or row_from["close"] in (None, 0):
        return None
    ret = (row_to["close"] / row_from["close"]) - 1.0
    return {"code": settings.BENCHMARK_CODE, "return": round(ret, 4)}
