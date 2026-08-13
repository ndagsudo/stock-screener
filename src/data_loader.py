"""
J-Quants API から取得した生データを SQLite に取り込むオーケストレーション層。

方針:
  - 契約プランで取得できないエンドポイント/フィールドがあっても、そこで
    パイプライン全体を止めない。例外は errors テーブルに記録し処理を継続する。
  - 存在しないデータを推測して埋めない（0 とデータなしを混同しない）。
  - APIコール数はレート制限に直結するため、まず全銘柄の価格スナップショットで
    時価総額フィルタをかけ、対象を絞ってから銘柄別の価格履歴・決算履歴を取得する。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database
from src.jquants_client import JQuantsClient, JQuantsAPIError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _is_excluded_company(name: str, product_category: Optional[str]) -> bool:
    name = name or ""
    if any(kw in name for kw in settings.EXCLUDE_NAME_KEYWORDS):
        return True
    if product_category and product_category in settings.EXCLUDE_PRODUCT_CATEGORIES:
        return True
    return False


def load_universe(client: JQuantsClient, conn: sqlite3.Connection, run_id: str) -> int:
    """上場銘柄一覧を取得し companies テーブルへ反映する。"""
    try:
        records = client.get_listed_info()
    except JQuantsAPIError as exc:
        database.log_error(conn, run_id, "load_universe", str(exc))
        return 0

    rows = []
    for r in records:
        code = r.get("Code")
        if not code:
            continue
        name = r.get("CoName", "")
        product_category = r.get("ProdCat")
        rows.append(
            {
                "code": code,
                "name": name,
                "name_en": r.get("CoNameEn"),
                "sector17": r.get("S17"),
                "sector17_name": r.get("S17Nm"),
                "sector33": r.get("S33"),
                "sector33_name": r.get("S33Nm"),
                "market": r.get("Mkt"),
                "market_name": r.get("MktNm"),
                "scale_category": r.get("ScaleCat"),
                "product_category": product_category,
                "is_active": 0 if _is_excluded_company(name, product_category) else 1,
                "first_seen_date": _today_str(),
                "last_updated_date": _today_str(),
            }
        )
    database.upsert(conn, "companies", rows, ["code"])
    return len(rows)


def load_market_snapshot(
    client: JQuantsClient, conn: sqlite3.Connection, run_id: str, date: Optional[str] = None, max_lookback_days: int = 10
) -> int:
    """指定日（省略時は当日）を起点に、データが存在する直近営業日を探して
    全銘柄の株価・時価総額スナップショットを取得する。

    J-Quantsの日次株価は当日中には確定しておらず、公開プランによっては
    翌営業日にならないと取得できない場合がある。単純に「今日」だけを
    リクエストすると常に0件になりうるため、データが見つかるまで
    過去に遡ってリトライする。
    """
    base_date = datetime.strptime(date, "%Y%m%d") if date else datetime.now(timezone.utc)
    for offset in range(max_lookback_days):
        candidate_date = (base_date - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            records = client.get_daily_quotes(date=candidate_date)
        except JQuantsAPIError as exc:
            database.log_error(conn, run_id, "load_market_snapshot", str(exc))
            continue
        if records:
            rows = _quotes_to_rows(records)
            database.upsert(conn, "prices", rows, ["code", "date"])
            return len(rows)
    database.log_error(
        conn, run_id, "load_market_snapshot", f"過去{max_lookback_days}日以内に株価データが見つかりませんでした"
    )
    return 0


def _quotes_to_rows(records: list[dict]) -> list[dict]:
    rows = []
    for r in records:
        code = r.get("Code")
        date = r.get("Date")
        if not code or not date:
            continue
        rows.append(
            {
                "code": code,
                "date": date,
                "open": r.get("O"),
                "high": r.get("H"),
                "low": r.get("L"),
                "close": r.get("C"),
                "adj_open": r.get("AdjO"),
                "adj_high": r.get("AdjH"),
                "adj_low": r.get("AdjL"),
                "adj_close": r.get("AdjC"),
                "volume": r.get("Vo"),
                "adj_volume": r.get("AdjVo"),
                "turnover_value": r.get("Va"),
                "market_cap": r.get("MktCap"),
                "adj_factor": r.get("AdjFactor"),
            }
        )
    return rows


def load_price_history(client: JQuantsClient, conn: sqlite3.Connection, run_id: str, code: str, days: int = 400) -> int:
    """52週高値・200日移動平均・株価CAGR計算のため、銘柄別に価格履歴を取得する。"""
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
    to_date = _today_str().replace("-", "")
    try:
        records = client.get_daily_quotes(code=code, from_date=from_date, to_date=to_date)
    except JQuantsAPIError as exc:
        database.log_error(conn, run_id, "load_price_history", str(exc), code=code)
        return 0
    rows = _quotes_to_rows(records)
    database.upsert(conn, "prices", rows, ["code", "date"])
    return len(rows)


def load_financials(client: JQuantsClient, conn: sqlite3.Connection, run_id: str, code: str) -> int:
    """銘柄別の決算情報（複数期分）を取得する。"""
    try:
        records = client.get_financial_summary(code=code)
    except JQuantsAPIError as exc:
        database.log_error(conn, run_id, "load_financials", str(exc), code=code)
        return 0
    rows = []
    for r in records:
        disc_date = r.get("DiscDate")
        if not disc_date:
            continue
        rows.append(
            {
                "code": code,
                "disclosure_date": disc_date,
                "doc_type": r.get("DocType"),
                "period_type": r.get("CurPerType") or "NA",
                "period_start": r.get("CurPerSt"),
                "period_end": r.get("CurPerEn"),
                "sales": r.get("Sales"),
                "operating_profit": r.get("OP"),
                "ordinary_profit": r.get("OdP"),
                "net_profit": r.get("NP"),
                "eps": r.get("EPS"),
                "total_assets": r.get("TA"),
                "equity": r.get("Eq"),
                "equity_ratio": r.get("EqAR"),
                "bps": r.get("BPS"),
                "roe": r.get("ROE"),
                "forecast_sales": r.get("FSales"),
                "forecast_operating_profit": r.get("FOP"),
                "forecast_ordinary_profit": r.get("FOdP"),
                "forecast_net_profit": r.get("FNP"),
                "forecast_eps": r.get("FEPS"),
                "shares_outstanding": r.get("ShOutFY"),
                "raw_json": database.to_json(r),
            }
        )
    database.upsert(conn, "financials", rows, ["code", "disclosure_date", "period_type"])
    return len(rows)


def load_dividends(client: JQuantsClient, conn: sqlite3.Connection, run_id: str, code: str) -> int:
    try:
        records = client.get_dividends(code=code)
    except JQuantsAPIError as exc:
        database.log_error(conn, run_id, "load_dividends", str(exc), code=code)
        return 0
    rows = []
    for r in records:
        ref_no = r.get("RefNo")
        if not ref_no:
            continue
        rows.append(
            {
                "code": code,
                "ref_no": ref_no,
                "pub_date": r.get("PubDate"),
                "ex_date": r.get("ExDate"),
                "record_date": r.get("RecDate"),
                "pay_date": r.get("PayDate"),
                "if_code": r.get("IFCode"),
                "fr_code": r.get("FRCode"),
                "if_term": r.get("IFTerm"),
                "div_rate": r.get("DivRate"),
            }
        )
    database.upsert(conn, "dividends", rows, ["code", "ref_no"])
    return len(rows)


def load_earnings_calendar(client: JQuantsClient, conn: sqlite3.Connection, run_id: str) -> int:
    try:
        records = client.get_earnings_calendar()
    except JQuantsAPIError as exc:
        database.log_error(conn, run_id, "load_earnings_calendar", str(exc))
        return 0
    rows = []
    for r in records:
        code = r.get("Code")
        announce_date = r.get("Date")
        fq = r.get("FQ")
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "announce_date": announce_date or "",
                "fiscal_year_end": r.get("FY"),
                "fiscal_quarter": fq or "",
            }
        )
    database.upsert(conn, "earnings_schedule", rows, ["code", "announce_date", "fiscal_quarter"])
    return len(rows)


def prefilter_by_market_cap(conn: sqlite3.Connection) -> list[str]:
    """最新の時価総額スナップショットに基づき、対象時価総額帯の銘柄コードを返す。"""
    sql = """
        SELECT p.code FROM prices p
        JOIN (
            SELECT code, MAX(date) AS max_date FROM prices GROUP BY code
        ) latest ON p.code = latest.code AND p.date = latest.max_date
        JOIN companies c ON c.code = p.code
        WHERE c.is_active = 1
          AND p.market_cap IS NOT NULL
          AND p.market_cap >= ? AND p.market_cap <= ?
    """
    cur = conn.execute(sql, (settings.MIN_MARKET_CAP, settings.MAX_MARKET_CAP))
    return [row["code"] for row in cur.fetchall()]


def load_all(run_id: str, api_key: Optional[str] = None, max_codes: Optional[int] = None) -> dict:
    """データ取得パイプライン全体を実行する。戻り値は取得件数のサマリ。"""
    client = JQuantsClient(api_key=api_key or settings.JQUANTS_API_KEY)
    summary = {"configured": client.is_configured}
    if not client.is_configured:
        return summary

    with database.connect() as conn:
        summary["companies"] = load_universe(client, conn, run_id)
        summary["market_snapshot"] = load_market_snapshot(client, conn, run_id)
        codes = prefilter_by_market_cap(conn)
        if max_codes:
            codes = codes[:max_codes]
        summary["target_codes"] = len(codes)

        summary["price_history"] = 0
        summary["financials"] = 0
        summary["dividends"] = 0
        for code in codes:
            summary["price_history"] += load_price_history(client, conn, run_id, code)
            summary["financials"] += load_financials(client, conn, run_id, code)
            summary["dividends"] += load_dividends(client, conn, run_id, code)

        summary["earnings_calendar"] = load_earnings_calendar(client, conn, run_id)

    return summary
