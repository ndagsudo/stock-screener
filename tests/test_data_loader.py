import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import _parse_subscription_end_date, _quotes_to_rows, _to_float, load_financials


def test_parse_subscription_end_date_extracts_upper_bound():
    message = (
        'GET /equities/bars/daily returned 400: {"message": "Your subscription covers '
        'the following dates: 2024-05-21 ~ 2026-05-21. If you want more data, please '
        'check other plans:https://jpx-jquants.com/#dataset"}'
    )
    assert _parse_subscription_end_date(message) == "20260521"


def test_parse_subscription_end_date_returns_none_for_unrelated_message():
    assert _parse_subscription_end_date("some other error") is None
    assert _parse_subscription_end_date("") is None


def test_quotes_to_rows_converts_market_cap_from_millions_to_yen():
    # J-Quants MktCap is in millions of yen (e.g. 100,000 -> 1000億円).
    records = [{"Code": "13010", "Date": "2026-05-21", "C": 1000.0, "MktCap": 100000.0}]
    rows = _quotes_to_rows(records)
    assert rows[0]["market_cap"] == 100000.0 * 1_000_000


def test_quotes_to_rows_keeps_market_cap_none_when_missing():
    records = [{"Code": "13010", "Date": "2026-05-21", "C": 1000.0}]
    rows = _quotes_to_rows(records)
    assert rows[0]["market_cap"] is None


def test_to_float_handles_jquants_placeholder_strings():
    # J-Quantsは未確定・非開示の数値項目を "-" 等の文字列で返すことがある。
    assert _to_float("-") is None
    assert _to_float("") is None
    assert _to_float(None) is None
    assert _to_float("123.45") == 123.45
    assert _to_float(123.45) == 123.45
    assert _to_float("not a number") is None


class _FakeClient:
    """load_financials 用のネットワーク不要スタブ。"""

    def __init__(self, records):
        self._records = records

    def get_financial_summary(self, code=None, date=None):
        return self._records


def test_load_financials_coerces_string_placeholders_to_none(tmp_path):
    import sys as _sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src import database

    db_path = tmp_path / "test.db"
    database.init_db(db_path)

    # 実際のライブ実行で観測された形: FNP(予想純利益)が "-" で返ってきた
    records = [
        {
            "DiscDate": "2026-05-15",
            "Code": "13010",
            "CurPerType": "FY",
            "NP": 500.0,
            "FNP": "-",
            "ROE": "-",
        }
    ]
    client = _FakeClient(records)
    with database.connect(db_path) as conn:
        n = load_financials(client, conn, "2026-08-15", "13010")
        assert n == 1
        row = conn.execute("SELECT * FROM financials WHERE code = '13010'").fetchone()
    assert row["net_profit"] == 500.0
    assert row["forecast_net_profit"] is None
    assert row["roe"] is None
