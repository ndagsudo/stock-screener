"""
100点満点スコアリング（成長性30・割安性25・収益性15・財務健全性10・
業績モメンタム10・株価位置10）。

すべての計算はここで完結し、AIには一切スコア計算をさせない。
各カテゴリーは複数の指標の加重平均で構成され、指標が欠損している場合は
その指標を分母・分子ともに除外して残りの指標だけで再計算する
（欠損を0点として扱わない）。
"""
from __future__ import annotations

from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings

Number = Optional[float]


def _normalize(value: Number, low: float, high: float, ascending: bool = True) -> Optional[float]:
    """value を [low, high] の範囲で 0.0〜1.0 に正規化する。範囲外はクリップ。"""
    if value is None:
        return None
    if low == high:
        return None
    frac = (value - low) / (high - low)
    frac = max(0.0, min(1.0, frac))
    return frac if ascending else 1.0 - frac


def _weighted_score(components: list[tuple[Optional[float], float]], max_points: float) -> tuple[float, bool]:
    """[(normalized_0_1_or_None, weight), ...] から加重平均し max_points 満点に換算する。
    戻り値: (得点, データが十分だったか)"""
    available = [(v, w) for v, w in components if v is not None]
    if not available:
        return 0.0, False
    total_weight = sum(w for _, w in available)
    if total_weight == 0:
        return 0.0, False
    weighted = sum(v * w for v, w in available) / total_weight
    return weighted * max_points, True


def score_growth(snap: dict) -> tuple[float, bool]:
    components = [
        (_normalize(snap.get("eps_growth_for_peg"), 0.0, 0.30), 0.40),
        (_normalize(snap.get("operating_profit_growth_yoy"), 0.0, 0.30), 0.30),
        (_normalize(snap.get("sales_growth_yoy"), 0.0, 0.25), 0.15),
        (_normalize(snap.get("eps_cagr"), 0.0, 0.25), 0.15),
    ]
    return _weighted_score(components, settings.SCORE_WEIGHTS["growth"])


def score_valuation(snap: dict) -> tuple[float, bool]:
    # PEGを最重視: 単純な低PERランキングにしないための核。
    components = [
        (_normalize(snap.get("peg"), 0.5, 3.0, ascending=False), 0.50),
        (_normalize(snap.get("per"), 8.0, 40.0, ascending=False), 0.30),
        (_normalize(snap.get("pbr"), 0.5, 5.0, ascending=False), 0.20),
    ]
    return _weighted_score(components, settings.SCORE_WEIGHTS["valuation"])


def score_profitability(snap: dict) -> tuple[float, bool]:
    components = [
        (_normalize(snap.get("roe"), 0.0, 0.20), 0.45),
        (_normalize(snap.get("operating_margin"), 0.0, 0.20), 0.35),
        (_normalize(snap.get("roa"), 0.0, 0.10), 0.20),
    ]
    return _weighted_score(components, settings.SCORE_WEIGHTS["profitability"])


def score_health(snap: dict) -> tuple[float, bool]:
    components = [
        (_normalize(snap.get("equity_ratio"), 0.20, 0.70), 1.0),
    ]
    return _weighted_score(components, settings.SCORE_WEIGHTS["health"])


def score_momentum(snap: dict) -> tuple[float, bool]:
    components = [
        (_normalize(snap.get("forecast_net_profit_growth"), -0.10, 0.30), 0.6),
        (_normalize(snap.get("operating_profit_growth_yoy"), -0.10, 0.30), 0.4),
    ]
    return _weighted_score(components, settings.SCORE_WEIGHTS["momentum"])


def score_price_position(snap: dict) -> tuple[float, bool]:
    """業績が良いのに株価が調整している銘柄を拾うためのスコア。
    52週高値からの下落幅が大きいほど、また200日線からの下方乖離が大きいほど加点する。"""
    components = [
        (_normalize(snap.get("drawdown_from_52w_high"), 0.0, 0.5), 0.6),
        (_normalize(snap.get("ma200_deviation"), -0.3, 0.3, ascending=False), 0.4),
    ]
    return _weighted_score(components, settings.SCORE_WEIGHTS["price_position"])


def compute_total_score(snap: dict) -> dict:
    growth, growth_ok = score_growth(snap)
    valuation, valuation_ok = score_valuation(snap)
    profitability, profitability_ok = score_profitability(snap)
    health, health_ok = score_health(snap)
    momentum, momentum_ok = score_momentum(snap)
    price_position, price_position_ok = score_price_position(snap)

    total = growth + valuation + profitability + health + momentum + price_position

    return {
        "code": snap.get("code"),
        "growth_score": round(growth, 2),
        "valuation_score": round(valuation, 2),
        "profitability_score": round(profitability, 2),
        "health_score": round(health, 2),
        "momentum_score": round(momentum, 2),
        "price_position_score": round(price_position, 2),
        "total_score": round(total, 2),
        "data_coverage": {
            "growth": growth_ok,
            "valuation": valuation_ok,
            "profitability": profitability_ok,
            "health": health_ok,
            "momentum": momentum_ok,
            "price_position": price_position_ok,
        },
    }
