import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from src import ai_analysis, database


class _FakeMessages:
    def create(self, **kwargs):
        raise RuntimeError(
            "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
            "'message': 'Your credit balance is too low to access the Anthropic API.'}}"
        )


class _FakeAnthropicClient:
    def __init__(self, api_key=None):
        self.messages = _FakeMessages()


def test_call_anthropic_does_not_raise_on_api_failure(tmp_path, monkeypatch):
    """実際のライブ実行で観測された障害の再発防止テスト:
    Anthropic APIがクレジット不足等でエラーを返しても、_call_anthropic は
    例外を外に伝播させず None を返し、errors テーブルに記録するだけに留める
    （数値ランキング・サイト生成を巻き込んで落とさないため）。"""
    fake_anthropic_module = types.SimpleNamespace(Anthropic=_FakeAnthropicClient)
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic_module)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-fake-key-for-test")

    db_path = tmp_path / "test.db"
    database.init_db(db_path)

    with database.connect(db_path) as conn:
        result = ai_analysis._call_anthropic({"code": "1234"}, conn=conn, run_id="2026-08-15")
        errors = conn.execute("SELECT * FROM errors WHERE stage = 'ai_analysis._call_anthropic'").fetchall()

    assert result is None
    assert len(errors) == 1
    assert "credit balance" in errors[0]["message"]


def test_analyze_and_cache_falls_back_gracefully_when_api_fails(tmp_path, monkeypatch):
    fake_anthropic_module = types.SimpleNamespace(Anthropic=_FakeAnthropicClient)
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic_module)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-fake-key-for-test")

    db_path = tmp_path / "test.db"
    database.init_db(db_path)

    snap = {"code": "1234", "name": "テスト株式会社", "as_of_date": "2026-08-15"}
    scored = {"total_score": 80.0}

    with database.connect(db_path) as conn:
        analysis_id = ai_analysis.analyze_and_cache(conn, "2026-08-15", snap, scored)
        assert analysis_id is not None
        saved = ai_analysis.get_latest_analysis(conn, "1234")

    assert saved is not None
    assert "情報を確認できませんでした" in saved["why_notable"]
