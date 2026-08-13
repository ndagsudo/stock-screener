"""
5年シミュレーション（弱気・標準・強気）。

すべてPythonによる機械的な計算。AIには一切計算させない。
画面表示側では必ず「システム試算」であり将来を保証しないことを明記する
（report.py 側で対応）。
"""
from __future__ import annotations

from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src.indicators import calc_future_eps, calc_future_price, calc_price_multiple, calc_cagr

Number = Optional[float]


def _base_growth_rate(snap: dict) -> Number:
    """5年試算の土台となる年成長率を選ぶ。優先順位: EPS CAGR実績 > 直近EPS成長率実績 > 会社予想EPS成長率。"""
    for key in ("eps_cagr", "eps_growth_yoy", "forecast_eps_growth"):
        v = snap.get(key)
        if v is not None:
            return max(settings.MIN_ASSUMED_GROWTH_RATE, min(settings.MAX_ASSUMED_GROWTH_RATE, v))
    return None


def run_forecast(snap: dict) -> list[dict]:
    """銘柄1件のスナップショットから bear/base/bull 3シナリオを計算する。
    EPSまたは株価が欠損している場合はシナリオ自体を返さない（空リスト）。"""
    code = snap.get("code")
    current_eps = snap.get("eps")
    current_price = snap.get("price")
    base_growth = _base_growth_rate(snap)

    if current_eps is None or current_eps <= 0 or current_price is None or base_growth is None:
        return []

    results = []
    for scenario, params in settings.FORECAST_SCENARIOS.items():
        growth_rate = base_growth * params["growth_multiplier"]
        growth_rate = max(settings.MIN_ASSUMED_GROWTH_RATE, min(settings.MAX_ASSUMED_GROWTH_RATE, growth_rate))
        exit_per = params["exit_per"]

        future_eps = calc_future_eps(current_eps, growth_rate, settings.FORECAST_YEARS)
        future_price = calc_future_price(future_eps, exit_per)
        multiple = calc_price_multiple(future_price, current_price)
        cagr = calc_cagr(current_price, future_price, settings.FORECAST_YEARS) if future_price else None

        results.append(
            {
                "code": code,
                "scenario": scenario,
                "current_eps": current_eps,
                "growth_rate_used": round(growth_rate, 4),
                "years": settings.FORECAST_YEARS,
                "future_eps": round(future_eps, 2) if future_eps else None,
                "exit_per": exit_per,
                "future_price": round(future_price, 1) if future_price else None,
                "current_price": current_price,
                "multiple": round(multiple, 2) if multiple else None,
                "cagr": round(cagr, 4) if cagr else None,
            }
        )
    return results
