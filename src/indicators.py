"""
財務・株価指標の計算。

このモジュールの純粋関数群 (calc_*) は DB に一切依存せず、数値だけを受け取り
数値だけを返す。欠損値は必ず None として扱い、0 と混同しない
（例: 前期利益が None なら成長率は None であって 0% ではない）。

build_indicator_snapshot() だけが DB にアクセスし、コードごとに必要な生データを
集めて上記の純粋関数へ渡す「配線」を行う。look-ahead bias を避けるため、
as_of_date 以前に開示された情報のみを使用する。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

Number = Optional[float]


def _safe_div(numerator: Number, denominator: Number) -> Number:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


# ---------------------------------------------------------------------------
# 基本指標
# ---------------------------------------------------------------------------

def calc_per(price: Number, eps: Number) -> Number:
    """株価収益率 PER = 株価 / EPS。EPSが0以下なら意味を持たないためNone。"""
    if price is None or eps is None or eps <= 0:
        return None
    return price / eps


def calc_pbr(price: Number, bps: Number) -> Number:
    if price is None or bps is None or bps <= 0:
        return None
    return price / bps


def calc_roe(net_profit: Number, equity: Number) -> Number:
    return _safe_div(net_profit, equity)


def calc_roa(net_profit: Number, total_assets: Number) -> Number:
    return _safe_div(net_profit, total_assets)


def calc_operating_margin(operating_profit: Number, sales: Number) -> Number:
    return _safe_div(operating_profit, sales)


def calc_equity_ratio(equity: Number, total_assets: Number) -> Number:
    return _safe_div(equity, total_assets)


def calc_dividend_yield(annual_dividend_per_share: Number, price: Number) -> Number:
    if annual_dividend_per_share is None or price is None or price <= 0:
        return None
    return annual_dividend_per_share / price


def calc_market_cap(price: Number, shares_outstanding: Number) -> Number:
    return None if price is None or shares_outstanding is None else price * shares_outstanding


# ---------------------------------------------------------------------------
# 成長率・CAGR
# ---------------------------------------------------------------------------

def calc_growth_rate(current: Number, previous: Number) -> Number:
    """単純な期間成長率 (current/previous - 1)。previousが0以下なら計算不能。"""
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous) - 1.0


def calc_cagr(start_value: Number, end_value: Number, years: Number) -> Number:
    """年平均成長率。start_valueが0以下、yearsが0以下なら計算不能。"""
    if start_value is None or end_value is None or years is None:
        return None
    if start_value <= 0 or years <= 0:
        return None
    if end_value <= 0:
        return -1.0
    return (end_value / start_value) ** (1.0 / years) - 1.0


def calc_peg(per: Number, eps_growth_rate: Number) -> Number:
    """PEG = PER / (EPS成長率 x 100)。成長率が0以下ならPEGは意味を持たないためNone。"""
    if per is None or eps_growth_rate is None or eps_growth_rate <= 0:
        return None
    growth_pct = eps_growth_rate * 100.0
    return per / growth_pct


# ---------------------------------------------------------------------------
# 株価位置
# ---------------------------------------------------------------------------

def calc_drawdown_from_high(price: Number, high_52w: Number) -> Number:
    """52週高値からの下落率（正の値＝高値からどれだけ下がっているか）。"""
    if price is None or high_52w is None or high_52w <= 0:
        return None
    return 1.0 - (price / high_52w)


def calc_ma_deviation(price: Number, moving_average: Number) -> Number:
    """移動平均からの乖離率。正なら上、負なら下。"""
    if price is None or moving_average is None or moving_average == 0:
        return None
    return (price / moving_average) - 1.0


def calc_simple_moving_average(closes: list[float], window: int) -> Number:
    if len(closes) < window:
        return None
    recent = closes[-window:]
    return sum(recent) / window


def calc_return(current_price: Number, past_price: Number) -> Number:
    return calc_growth_rate(current_price, past_price)


# ---------------------------------------------------------------------------
# 5年後EPS・株価試算（forecast.pyから呼び出される基礎関数）
# ---------------------------------------------------------------------------

def calc_future_eps(current_eps: Number, annual_growth_rate: Number, years: int) -> Number:
    if current_eps is None or annual_growth_rate is None or current_eps <= 0:
        return None
    return current_eps * ((1.0 + annual_growth_rate) ** years)


def calc_future_price(future_eps: Number, exit_per: Number) -> Number:
    if future_eps is None or exit_per is None or future_eps <= 0:
        return None
    return future_eps * exit_per


def calc_price_multiple(future_price: Number, current_price: Number) -> Number:
    return _safe_div(future_price, current_price)


# ---------------------------------------------------------------------------
# DB からの指標スナップショット組み立て（look-ahead bias 防止）
# ---------------------------------------------------------------------------

def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10].replace("/", "-"), fmt)
        except ValueError:
            continue
    return None


def build_indicator_snapshot(conn: sqlite3.Connection, code: str, as_of_date: Optional[str] = None) -> dict:
    """
    銘柄コードについて、as_of_date（省略時は最新）時点で入手可能だったデータのみを
    使って指標一式を計算する。欠損データは None のまま返し、0で埋めない。
    """
    as_of = as_of_date or datetime.utcnow().strftime("%Y-%m-%d")

    company = conn.execute("SELECT * FROM companies WHERE code = ?", (code,)).fetchone()

    price_rows = conn.execute(
        "SELECT * FROM prices WHERE code = ? AND date <= ? ORDER BY date ASC", (code, as_of)
    ).fetchall()
    fin_rows = conn.execute(
        "SELECT * FROM financials WHERE code = ? AND disclosure_date <= ? ORDER BY disclosure_date ASC",
        (code, as_of),
    ).fetchall()

    snapshot: dict = {
        "code": code,
        "name": company["name"] if company else None,
        "as_of_date": as_of,
        "data_available": {
            "price": bool(price_rows),
            "financials": bool(fin_rows),
        },
    }

    # --- 価格系 ---
    latest_price_row = price_rows[-1] if price_rows else None
    price = latest_price_row["adj_close"] if latest_price_row else None
    if price is None and latest_price_row is not None:
        price = latest_price_row["close"]
    snapshot["price"] = price
    snapshot["price_date"] = latest_price_row["date"] if latest_price_row else None
    snapshot["market_cap"] = latest_price_row["market_cap"] if latest_price_row else None

    closes = [r["adj_close"] or r["close"] for r in price_rows if (r["adj_close"] or r["close"]) is not None]
    dates = [r["date"] for r in price_rows]

    snapshot["ma200"] = calc_simple_moving_average(closes, 200)
    snapshot["ma200_deviation"] = calc_ma_deviation(price, snapshot["ma200"])

    one_year_ago = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    window_prices = [
        (d, c) for d, c in zip(dates, closes) if d >= one_year_ago
    ]
    high_52w = max((c for _, c in window_prices), default=None)
    snapshot["high_52w"] = high_52w
    snapshot["drawdown_from_52w_high"] = calc_drawdown_from_high(price, high_52w)

    price_1y_ago = window_prices[0][1] if window_prices else None
    snapshot["return_1y"] = calc_return(price, price_1y_ago)

    # --- 決算系（通期 FY のみを growth/CAGR の対象とする） ---
    fy_rows = [r for r in fin_rows if (r["period_type"] or "").upper() in ("FY", "4Q")]
    latest_fy = fy_rows[-1] if fy_rows else None
    prev_fy = fy_rows[-2] if len(fy_rows) >= 2 else None
    fy_5y_ago = fy_rows[-6] if len(fy_rows) >= 6 else (fy_rows[0] if fy_rows and len(fy_rows) < 6 else None)

    latest_any = fin_rows[-1] if fin_rows else None

    def _f(row, col):
        return row[col] if row is not None else None

    snapshot["latest_disclosure_date"] = _f(latest_any, "disclosure_date")
    snapshot["eps"] = _f(latest_fy, "eps") if latest_fy else _f(latest_any, "eps")
    snapshot["bps"] = _f(latest_fy, "bps") if latest_fy else _f(latest_any, "bps")
    snapshot["sales"] = _f(latest_fy, "sales")
    snapshot["operating_profit"] = _f(latest_fy, "operating_profit")
    snapshot["ordinary_profit"] = _f(latest_fy, "ordinary_profit")
    snapshot["net_profit"] = _f(latest_fy, "net_profit")
    snapshot["total_assets"] = _f(latest_fy, "total_assets")
    snapshot["equity"] = _f(latest_fy, "equity")
    snapshot["shares_outstanding"] = _f(latest_fy, "shares_outstanding") or _f(latest_any, "shares_outstanding")

    snapshot["forecast_sales"] = _f(latest_any, "forecast_sales")
    snapshot["forecast_operating_profit"] = _f(latest_any, "forecast_operating_profit")
    snapshot["forecast_net_profit"] = _f(latest_any, "forecast_net_profit")
    snapshot["forecast_eps"] = _f(latest_any, "forecast_eps")

    snapshot["per"] = calc_per(price, snapshot["eps"])
    snapshot["pbr"] = calc_pbr(price, snapshot["bps"])
    snapshot["roe"] = _f(latest_fy, "roe") or calc_roe(snapshot["net_profit"], snapshot["equity"])
    snapshot["roa"] = calc_roa(snapshot["net_profit"], snapshot["total_assets"])
    snapshot["operating_margin"] = calc_operating_margin(snapshot["operating_profit"], snapshot["sales"])
    snapshot["equity_ratio"] = _f(latest_fy, "equity_ratio") or calc_equity_ratio(
        snapshot["equity"], snapshot["total_assets"]
    )

    if not snapshot["market_cap"]:
        snapshot["market_cap"] = calc_market_cap(price, snapshot["shares_outstanding"])

    # YoY 成長率
    snapshot["sales_growth_yoy"] = calc_growth_rate(snapshot["sales"], _f(prev_fy, "sales"))
    snapshot["operating_profit_growth_yoy"] = calc_growth_rate(
        snapshot["operating_profit"], _f(prev_fy, "operating_profit")
    )
    snapshot["ordinary_profit_growth_yoy"] = calc_growth_rate(
        snapshot["ordinary_profit"], _f(prev_fy, "ordinary_profit")
    )
    snapshot["net_profit_growth_yoy"] = calc_growth_rate(snapshot["net_profit"], _f(prev_fy, "net_profit"))
    snapshot["eps_growth_yoy"] = calc_growth_rate(snapshot["eps"], _f(prev_fy, "eps"))

    # 通期進捗率・会社予想に対する伸び
    snapshot["forecast_net_profit_growth"] = calc_growth_rate(
        snapshot["forecast_net_profit"], snapshot["net_profit"]
    )
    snapshot["forecast_eps_growth"] = calc_growth_rate(snapshot["forecast_eps"], snapshot["eps"])

    # CAGR（最大5期分）
    years_span = len(fy_rows) - 1 if fy_5y_ago and latest_fy else None
    snapshot["sales_cagr"] = calc_cagr(_f(fy_5y_ago, "sales"), snapshot["sales"], years_span)
    snapshot["operating_profit_cagr"] = calc_cagr(
        _f(fy_5y_ago, "operating_profit"), snapshot["operating_profit"], years_span
    )
    snapshot["eps_cagr"] = calc_cagr(_f(fy_5y_ago, "eps"), snapshot["eps"], years_span)

    # PEG: 実績EPS成長率がなければ会社予想EPS成長率で代用
    growth_for_peg = snapshot["eps_growth_yoy"] if snapshot["eps_growth_yoy"] is not None else snapshot[
        "forecast_eps_growth"
    ]
    snapshot["peg"] = calc_peg(snapshot["per"], growth_for_peg)
    snapshot["eps_growth_for_peg"] = growth_for_peg

    # 配当利回り（直近1年の決定済み配当合計）
    div_rows = conn.execute(
        "SELECT * FROM dividends WHERE code = ? AND pub_date <= ? ORDER BY pub_date DESC LIMIT 4",
        (code, as_of),
    ).fetchall()
    total_div = 0.0
    has_div = False
    for r in div_rows:
        try:
            v = float(r["div_rate"])
            total_div += v
            has_div = True
        except (TypeError, ValueError):
            continue
    snapshot["dividend_yield"] = calc_dividend_yield(total_div if has_div else None, price)

    return snapshot
