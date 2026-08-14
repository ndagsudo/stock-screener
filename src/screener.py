"""
数値条件による一次スクリーニング。

「AIが銘柄を選ぶ」のではなく、まずここで純粋にPythonの数値条件だけで
対象を100〜300銘柄程度まで絞り込む。判定に使った理由(reasons)は
screening_results テーブルに保存し、後から検証できるようにする。
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
from src.indicators import build_indicator_snapshot


def _passes_hard_filters(snapshot: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ok = True

    market_cap = snapshot.get("market_cap")
    if market_cap is None:
        ok = False
        reasons.append("時価総額データなし")
    elif not (settings.MIN_MARKET_CAP <= market_cap <= settings.MAX_MARKET_CAP):
        ok = False
        reasons.append(f"時価総額が対象範囲外 ({market_cap:,.0f}円)")

    roe = snapshot.get("roe")
    if roe is not None and roe < settings.MIN_ROE:
        ok = False
        reasons.append(f"ROEが基準未満 ({roe:.1%})")

    op_margin = snapshot.get("operating_margin")
    if op_margin is not None and op_margin < settings.MIN_OPERATING_MARGIN:
        ok = False
        reasons.append(f"営業利益率が基準未満 ({op_margin:.1%})")

    per = snapshot.get("per")
    if per is not None and per > settings.MAX_PER:
        ok = False
        reasons.append(f"PERが上限超過 ({per:.1f}倍)")

    if settings.MIN_EPS_GROWTH is not None:
        eps_growth = snapshot.get("eps_growth_yoy")
        if eps_growth is not None and eps_growth < settings.MIN_EPS_GROWTH:
            ok = False
            reasons.append(f"EPS成長率が基準未満 ({eps_growth:.1%})")

    if not snapshot.get("data_available", {}).get("financials"):
        ok = False
        reasons.append("決算データなし")

    if ok:
        reasons.append("通過")
    return ok, reasons


def _provisional_rank_key(snapshot: dict) -> float:
    """候補数がSCREENING_MAX_CANDIDATESを超えた場合の粗い絞り込み用キー。
    正式なスコアリングは scoring.py が担当する。ここはあくまで足切り。"""
    roe = snapshot.get("roe") or 0.0
    op_margin = snapshot.get("operating_margin") or 0.0
    eps_growth = snapshot.get("eps_growth_for_peg") or 0.0
    return roe + op_margin + eps_growth


def run_screening(conn: sqlite3.Connection, run_id: str, as_of_date: Optional[str] = None) -> list[dict]:
    """全対象銘柄に指標スナップショットを作り、数値条件で絞り込む。
    戻り値は通過した銘柄のスナップショットのリスト。"""
    as_of = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    codes = [
        row["code"]
        for row in conn.execute("SELECT code FROM companies WHERE is_active = 1").fetchall()
    ]

    passed_rows = []
    all_result_rows = []
    for code in codes:
        # 1銘柄のデータ異常（想定外の型・欠損の組み合わせ等）で
        # スクリーニング全体を止めないよう、銘柄単位で例外を隔離する。
        try:
            snapshot = build_indicator_snapshot(conn, code, as_of)
            ok, reasons = _passes_hard_filters(snapshot)
        except Exception as exc:  # noqa: BLE001
            database.log_error(conn, run_id, "screener.run_screening", str(exc), code=code)
            ok, reasons = False, [f"指標計算中にエラー: {exc}"]
        all_result_rows.append(
            {
                "run_id": run_id,
                "code": code,
                "passed": 1 if ok else 0,
                "reasons_json": database.to_json(reasons),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if ok:
            passed_rows.append(snapshot)

    if len(passed_rows) > settings.SCREENING_MAX_CANDIDATES:
        passed_rows.sort(key=_provisional_rank_key, reverse=True)
        excluded = passed_rows[settings.SCREENING_MAX_CANDIDATES :]
        passed_rows = passed_rows[: settings.SCREENING_MAX_CANDIDATES]
        excluded_codes = {s["code"] for s in excluded}
        for row in all_result_rows:
            if row["code"] in excluded_codes:
                row["passed"] = 0
                reasons = database.from_json(row["reasons_json"], [])
                reasons.append("候補数上限により除外（粗スコア順）")
                row["reasons_json"] = database.to_json(reasons)

    database.upsert(conn, "screening_results", all_result_rows, ["run_id", "code"])
    return passed_rows
