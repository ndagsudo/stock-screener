import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import _parse_subscription_end_date


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
