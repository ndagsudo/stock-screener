import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import indicators as ind


def test_calc_per_basic():
    assert ind.calc_per(1000, 100) == 10.0


def test_calc_per_missing_or_invalid():
    assert ind.calc_per(None, 100) is None
    assert ind.calc_per(1000, None) is None
    assert ind.calc_per(1000, 0) is None
    assert ind.calc_per(1000, -5) is None


def test_calc_pbr_basic():
    assert ind.calc_pbr(500, 250) == 2.0


def test_calc_roe():
    assert ind.calc_roe(100, 1000) == 0.1
    assert ind.calc_roe(100, None) is None
    assert ind.calc_roe(100, 0) is None


def test_calc_operating_margin():
    assert ind.calc_operating_margin(200, 1000) == 0.2
    assert ind.calc_operating_margin(None, 1000) is None


def test_calc_growth_rate():
    assert round(ind.calc_growth_rate(120, 100), 4) == 0.2
    assert ind.calc_growth_rate(None, 100) is None
    assert ind.calc_growth_rate(120, None) is None
    assert ind.calc_growth_rate(120, 0) is None
    assert ind.calc_growth_rate(120, -10) is None


def test_calc_cagr():
    # 100 -> 200 over 5 years
    cagr = ind.calc_cagr(100, 200, 5)
    assert round(cagr, 4) == round(2 ** (1 / 5) - 1, 4)


def test_calc_cagr_missing():
    assert ind.calc_cagr(None, 200, 5) is None
    assert ind.calc_cagr(100, 200, 0) is None
    assert ind.calc_cagr(0, 200, 5) is None


def test_calc_cagr_negative_end_value():
    assert ind.calc_cagr(100, -10, 5) == -1.0


def test_calc_peg():
    # PER 20, growth 25% -> PEG 20/25 = 0.8
    peg = ind.calc_peg(20, 0.25)
    assert round(peg, 4) == 0.8


def test_calc_peg_zero_or_negative_growth():
    assert ind.calc_peg(20, 0.0) is None
    assert ind.calc_peg(20, -0.1) is None
    assert ind.calc_peg(None, 0.1) is None


def test_calc_drawdown_from_high():
    dd = ind.calc_drawdown_from_high(800, 1000)
    assert round(dd, 4) == 0.2


def test_calc_ma_deviation():
    dev = ind.calc_ma_deviation(1100, 1000)
    assert round(dev, 4) == 0.1


def test_calc_future_eps_and_price():
    future_eps = ind.calc_future_eps(100, 0.15, 5)
    assert round(future_eps, 2) == round(100 * (1.15 ** 5), 2)
    future_price = ind.calc_future_price(future_eps, 20)
    assert round(future_price, 2) == round(future_eps * 20, 2)


def test_calc_future_eps_missing():
    assert ind.calc_future_eps(None, 0.1, 5) is None
    assert ind.calc_future_eps(100, None, 5) is None
    assert ind.calc_future_eps(0, 0.1, 5) is None


def test_calc_price_multiple():
    assert ind.calc_price_multiple(2000, 1000) == 2.0
    assert ind.calc_price_multiple(2000, None) is None


def test_calc_dividend_yield_and_market_cap():
    assert ind.calc_dividend_yield(50, 1000) == 0.05
    assert ind.calc_dividend_yield(None, 1000) is None
    assert ind.calc_market_cap(1000, 1_000_000) == 1_000_000_000


def test_calc_simple_moving_average():
    closes = [float(i) for i in range(1, 21)]
    ma = ind.calc_simple_moving_average(closes, 10)
    assert ma == sum(closes[-10:]) / 10
    assert ind.calc_simple_moving_average(closes, 50) is None
