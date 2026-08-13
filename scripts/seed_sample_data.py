"""
開発・動作確認用のサンプルデータ生成スクリプト。

JQUANTS_API_KEY が無い環境でもパイプライン全体（スクリーニング→スコアリング→
5年シミュレーション→ランキング→AI分析のフォールバック→HTML生成）を
一通り動作確認できるように、乱数ベースだが再現可能な合成データを
data/stock.db に投入する。**実在の会社データではない。**

使い方:
    python scripts/seed_sample_data.py
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database

random.seed(42)

SECTORS = ["情報・通信業", "機械", "電気機器", "サービス業", "小売業", "化学"]

# (type, count, sales_growth_range, op_margin_range, per_bias, drawdown_bias)
COMPANY_TYPES = [
    ("high_growth", 8, (0.18, 0.32), (0.10, 0.20)),
    ("turnaround", 8, (0.05, 0.15), (0.03, 0.10)),
    ("value_stagnant", 8, (0.00, 0.05), (0.04, 0.08)),
    ("expensive_growth", 6, (0.15, 0.25), (0.08, 0.15)),
    ("high_quality", 6, (0.08, 0.14), (0.15, 0.25)),
]


def business_days(end: datetime, days: int) -> list[datetime]:
    out = []
    d = end
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def build_company(code: str, ctype: str, idx: int) -> dict:
    return {
        "code": code,
        "name": f"サンプル{ctype}{idx}株式会社",
        "name_en": f"Sample {ctype} {idx} Inc.",
        "sector17": "16",
        "sector17_name": "情報・サービス",
        "sector33": "5250",
        "sector33_name": random.choice(SECTORS),
        "market": "0111",
        "market_name": "プライム",
        "scale_category": "TOPIX Small",
        "product_category": "011",
        "is_active": 1,
        "first_seen_date": "2020-01-01",
        "last_updated_date": datetime.utcnow().strftime("%Y-%m-%d"),
    }


def build_financials(code: str, ctype: str, shares: float) -> list[dict]:
    rows = []
    years = 6
    # 単位は百万円。市場想定（時価総額100〜3000億円）に対してPERが
    # 現実的な範囲(概ね5〜40倍)に収まるよう、株価水準・発行株式数と
    # 整合するスケールに調整している。
    if ctype == "turnaround":
        # 序盤は低迷、直近2期で急回復
        profits = [750, 300, -150, 450, 2700, 3900]
        sales = [45000, 43500, 39000, 42000, 51000, 58500]
    elif ctype == "high_growth":
        base_sales, base_profit = 30000, 2250
        sales = [round(base_sales * (1.25 ** i)) for i in range(years)]
        profits = [round(base_profit * (1.28 ** i)) for i in range(years)]
    elif ctype == "expensive_growth":
        base_sales, base_profit = 37500, 3000
        sales = [round(base_sales * (1.20 ** i)) for i in range(years)]
        profits = [round(base_profit * (1.22 ** i)) for i in range(years)]
    elif ctype == "high_quality":
        base_sales, base_profit = 75000, 9000
        sales = [round(base_sales * (1.10 ** i)) for i in range(years)]
        profits = [round(base_profit * (1.11 ** i)) for i in range(years)]
    else:  # value_stagnant
        base_sales, base_profit = 60000, 3750
        sales = [round(base_sales * (1.02 ** i)) for i in range(years)]
        profits = [round(base_profit * (1.015 ** i)) for i in range(years)]

    equity_base = profits[0] * 8
    for i in range(years):
        year = 2021 + i
        disc_date = f"{year}-05-15"
        op = round(profits[i] * 1.3)
        net = profits[i]
        equity = equity_base + sum(profits[: i + 1]) * 0.6
        total_assets = equity / 0.45
        eps = round((net * 1_000_000) / shares, 2)
        bps = round((equity * 1_000_000) / shares, 2)
        rows.append(
            {
                "code": code,
                "disclosure_date": disc_date,
                "doc_type": "FY",
                "period_type": "FY",
                "period_start": f"{year-1}-04-01",
                "period_end": f"{year}-03-31",
                "sales": sales[i] * 1_000_000,
                "operating_profit": op * 1_000_000,
                "ordinary_profit": round(op * 0.97) * 1_000_000,
                "net_profit": net * 1_000_000,
                "eps": eps,
                "total_assets": total_assets * 1_000_000,
                "equity": equity * 1_000_000,
                "equity_ratio": round(equity / total_assets, 4),
                "bps": bps,
                "roe": round(net / equity, 4) if equity > 0 else None,
                "forecast_sales": round(sales[i] * 1.1) * 1_000_000,
                "forecast_operating_profit": round(op * 1.15) * 1_000_000,
                "forecast_ordinary_profit": round(op * 1.12) * 1_000_000,
                "forecast_net_profit": round(net * 1.15) * 1_000_000,
                "forecast_eps": round(eps * 1.15, 2),
                "shares_outstanding": shares,
                "raw_json": "{}",
            }
        )
    return rows


def build_prices(code: str, ctype: str, shares: float, base_price: float) -> list[dict]:
    end = datetime(2026, 8, 15)
    dates = business_days(end, 420)
    price = base_price
    drift_map = {
        "high_growth": 0.0009,
        "expensive_growth": 0.0005,
        "high_quality": 0.0004,
        "value_stagnant": 0.0001,
        "turnaround": -0.0003,  # 直近まで低迷し、終盤だけ切り返す設定はここでは単純化
    }
    drift = drift_map.get(ctype, 0.0003)
    rows = []
    for i, d in enumerate(dates):
        # ターンアラウンド銘柄は終盤にかけて反発させる
        local_drift = drift
        if ctype == "turnaround" and i > len(dates) * 0.8:
            local_drift = 0.0025
        shock = random.gauss(0, 0.015)
        price = max(price * (1 + local_drift + shock), 50)
        date_str = d.strftime("%Y-%m-%d")
        market_cap = price * shares
        rows.append(
            {
                "code": code,
                "date": date_str,
                "open": round(price, 1),
                "high": round(price * 1.01, 1),
                "low": round(price * 0.99, 1),
                "close": round(price, 1),
                "adj_open": round(price, 1),
                "adj_high": round(price * 1.01, 1),
                "adj_low": round(price * 0.99, 1),
                "adj_close": round(price, 1),
                "volume": random.randint(10000, 300000),
                "adj_volume": random.randint(10000, 300000),
                "turnover_value": round(price * random.randint(10000, 300000)),
                "market_cap": round(market_cap),
                "adj_factor": 1.0,
            }
        )
    return rows


def main() -> None:
    database.init_db()
    with database.connect() as conn:
        idx = 1000
        for ctype, count, _sg, _om in COMPANY_TYPES:
            for i in range(count):
                idx += 1
                code = str(idx)
                shares = random.uniform(15_000_000, 60_000_000)
                base_price = random.uniform(900, 3500)

                company_row = build_company(code, ctype, i + 1)
                database.upsert(conn, "companies", [company_row], ["code"])

                fin_rows = build_financials(code, ctype, shares)
                database.upsert(conn, "financials", fin_rows, ["code", "disclosure_date", "period_type"])

                price_rows = build_prices(code, ctype, shares, base_price)
                database.upsert(conn, "prices", price_rows, ["code", "date"])

        print(f"Seeded {idx - 1000} sample companies into {settings.DB_PATH}")


if __name__ == "__main__":
    main()
