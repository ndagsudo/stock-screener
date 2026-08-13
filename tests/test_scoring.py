import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import scoring


def _base_snapshot(**overrides):
    snap = {
        "code": "1234",
        "eps_growth_for_peg": 0.20,
        "operating_profit_growth_yoy": 0.15,
        "sales_growth_yoy": 0.10,
        "eps_cagr": 0.15,
        "peg": 1.0,
        "per": 15.0,
        "pbr": 2.0,
        "roe": 0.12,
        "operating_margin": 0.10,
        "roa": 0.05,
        "equity_ratio": 0.5,
        "forecast_net_profit_growth": 0.10,
        "drawdown_from_52w_high": 0.20,
        "ma200_deviation": -0.05,
    }
    snap.update(overrides)
    return snap


def test_score_components_within_bounds():
    snap = _base_snapshot()
    result = scoring.compute_total_score(snap)
    assert 0 <= result["growth_score"] <= 30
    assert 0 <= result["valuation_score"] <= 25
    assert 0 <= result["profitability_score"] <= 15
    assert 0 <= result["health_score"] <= 10
    assert 0 <= result["momentum_score"] <= 10
    assert 0 <= result["price_position_score"] <= 10
    assert 0 <= result["total_score"] <= 100


def test_total_score_is_sum_of_components():
    snap = _base_snapshot()
    result = scoring.compute_total_score(snap)
    total = (
        result["growth_score"]
        + result["valuation_score"]
        + result["profitability_score"]
        + result["health_score"]
        + result["momentum_score"]
        + result["price_position_score"]
    )
    assert round(total, 2) == result["total_score"]


def test_missing_data_does_not_crash_and_scores_zero_when_all_missing():
    empty_snap = {"code": "0000"}
    result = scoring.compute_total_score(empty_snap)
    assert result["total_score"] == 0.0
    assert all(v is False for v in result["data_coverage"].values())


def test_partial_missing_data_still_scores_from_available_metrics():
    snap = {"code": "5555", "peg": 0.8}  # 割安性の一部データのみ
    result = scoring.compute_total_score(snap)
    assert result["valuation_score"] > 0
    assert result["growth_score"] == 0.0
    assert result["data_coverage"]["valuation"] is True
    assert result["data_coverage"]["growth"] is False


def test_higher_growth_scores_higher():
    low = scoring.score_growth(_base_snapshot(eps_growth_for_peg=0.02, operating_profit_growth_yoy=0.02))
    high = scoring.score_growth(_base_snapshot(eps_growth_for_peg=0.30, operating_profit_growth_yoy=0.30))
    assert high[0] > low[0]


def test_low_peg_scores_higher_valuation_than_high_peg():
    cheap = scoring.score_valuation(_base_snapshot(peg=0.5))
    expensive = scoring.score_valuation(_base_snapshot(peg=2.8))
    assert cheap[0] > expensive[0]
