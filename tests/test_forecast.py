import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import forecast


def _snapshot(**overrides):
    snap = {
        "code": "1234",
        "eps": 100.0,
        "price": 1500.0,
        "eps_cagr": 0.15,
        "eps_growth_yoy": 0.12,
        "forecast_eps_growth": 0.10,
    }
    snap.update(overrides)
    return snap


def test_run_forecast_returns_three_scenarios():
    results = forecast.run_forecast(_snapshot())
    scenarios = {r["scenario"] for r in results}
    assert scenarios == {"bear", "base", "bull"}


def test_bull_scenario_has_higher_price_than_bear():
    results = {r["scenario"]: r for r in forecast.run_forecast(_snapshot())}
    assert results["bull"]["future_price"] > results["base"]["future_price"]
    assert results["base"]["future_price"] > results["bear"]["future_price"]


def test_future_eps_formula():
    results = {r["scenario"]: r for r in forecast.run_forecast(_snapshot())}
    base = results["base"]
    expected_eps = 100.0 * ((1 + base["growth_rate_used"]) ** 5)
    assert round(base["future_eps"], 1) == round(expected_eps, 1)


def test_multiple_and_cagr_consistency():
    results = {r["scenario"]: r for r in forecast.run_forecast(_snapshot())}
    base = results["base"]
    assert round(base["future_price"] / base["current_price"], 2) == base["multiple"]


def test_missing_eps_returns_empty():
    assert forecast.run_forecast(_snapshot(eps=None)) == []


def test_missing_price_returns_empty():
    assert forecast.run_forecast(_snapshot(price=None)) == []


def test_no_growth_data_returns_empty():
    snap = _snapshot(eps_cagr=None, eps_growth_yoy=None, forecast_eps_growth=None)
    assert forecast.run_forecast(snap) == []


def test_growth_rate_is_clamped_to_configured_bounds():
    from config import settings

    snap = _snapshot(eps_cagr=5.0)  # 異常な高成長率
    results = {r["scenario"]: r for r in forecast.run_forecast(snap)}
    for r in results.values():
        assert r["growth_rate_used"] <= settings.MAX_ASSUMED_GROWTH_RATE + 1e-9
