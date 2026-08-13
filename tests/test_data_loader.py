import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import _parse_subscription_end_date, _quotes_to_rows


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
