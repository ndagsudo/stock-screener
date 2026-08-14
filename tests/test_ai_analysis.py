import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ai_analysis, database


def _sample_snap(**overrides):
    snap = {
        "code": "1234",
        "name": "テスト株式会社",
        "as_of_date": "2026-08-15",
        "latest_disclosure_date": "2026-05-15",
        "price": 1000.0,
        "market_cap": 50_000_000_000,
        "per": 15.0,
        "roe": 0.15,
    }
    snap.update(overrides)
    return snap


def _sample_scored(**overrides):
    scored = {
        "total_score": 80.0,
        "growth_score": 24.0,
        "valuation_score": 20.0,
        "profitability_score": 12.0,
        "health_score": 8.0,
        "momentum_score": 8.0,
        "price_position_score": 8.0,
    }
    scored.update(overrides)
    return scored


def test_module_does_not_import_anthropic_sdk():
    """このモジュールはAnthropic APIを直接呼び出さない設計であることの
    最低限の保証: anthropicパッケージのインポート・APIクライアント呼び出しが
    一切無いこと（コメント中で「呼び出さない」旨に言及するのは問題ないため、
    実際のimport/呼び出しパターンだけをチェックする）。"""
    import inspect

    source = inspect.getsource(ai_analysis)
    assert "import anthropic" not in source
    assert "anthropic.Anthropic(" not in source
    assert "anthropic" not in sys.modules or not hasattr(sys.modules.get("ai_analysis"), "anthropic")


def test_build_manual_review_prompt_includes_research_questions_and_data():
    snap = _sample_snap()
    scored = _sample_scored()
    prompt = ai_analysis.build_manual_review_prompt(snap, scored)

    assert "1234" in prompt
    assert "テスト株式会社" in prompt
    for q in ai_analysis.RESEARCH_QUESTIONS:
        assert q in prompt
    assert "why_notable" in prompt  # 出力スキーマの指示が含まれる
    assert "買い" not in prompt or "断定" in prompt  # 断定を避ける指示があること


def test_select_ai_targets_picks_top_n_by_score_and_rank_jumpers():
    candidates = [
        {"code": str(1000 + i), "total_score": score} for i, score in enumerate([90, 80, 70, 60, 50, 40])
    ]
    import config.settings as settings

    original = settings.AI_ANALYSIS_CANDIDATES
    settings.AI_ANALYSIS_CANDIDATES = 2
    try:
        targets = ai_analysis.select_ai_targets(candidates, rank_jumpers=["1005"])
    finally:
        settings.AI_ANALYSIS_CANDIDATES = original

    codes = {t["code"] for t in targets}
    assert codes == {"1000", "1001", "1005"}  # 上位2件 + 順位急上昇銘柄


def test_export_targets_writes_files_without_network(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    output_dir = tmp_path / "ai_review"

    target = {**_sample_snap(), **_sample_scored()}
    with database.connect(db_path) as conn:
        results = ai_analysis.export_targets_for_manual_review(
            conn, "2026-08-15", [target], output_dir=output_dir
        )

    assert len(results) == 1
    assert results[0]["status"] == "exported"
    exported_path = Path(results[0]["path"])
    assert exported_path.exists()
    content = exported_path.read_text(encoding="utf-8")
    assert "1234" in content
    assert (output_dir / "_index.json").exists()


def test_export_skips_fresh_cached_analysis(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    output_dir = tmp_path / "ai_review"
    target = {**_sample_snap(), **_sample_scored()}

    with database.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO screening_scores (run_id, code, indicators_json, created_at) VALUES (?, ?, ?, ?)",
            ("2026-08-15", "1234", database.to_json(target), "2026-08-15T00:00:00+00:00"),
        )
        # 1回目: エクスポートされる
        first = ai_analysis.export_targets_for_manual_review(
            conn, "2026-08-15", [target], output_dir=output_dir
        )
        assert first[0]["status"] == "exported"

        # 直後に手動分析結果を保存（＝キャッシュが新しくなる）
        result = {
            "why_notable": "テスト",
            "bull_points": ["a"],
            "bear_points": [],
            "checkpoints": [],
            "growth_drivers": [],
            "competitive_advantages": [],
            "overall_comment": "テスト",
        }
        ai_analysis.save_manual_analysis(conn, "2026-08-15", "1234", result)

        # 2回目: 同じ入力データ・新しいキャッシュなので再エクスポートされない
        second = ai_analysis.export_targets_for_manual_review(
            conn, "2026-08-15", [target], output_dir=output_dir
        )
    assert second[0]["status"] == "skipped_fresh"


def test_save_manual_analysis_requires_schema_fields(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    with database.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO screening_scores (run_id, code, indicators_json, created_at) VALUES (?, ?, ?, ?)",
            ("2026-08-15", "1234", database.to_json(_sample_snap()), "2026-08-15T00:00:00+00:00"),
        )
        try:
            ai_analysis.save_manual_analysis(conn, "2026-08-15", "1234", {"why_notable": "不完全"})
            assert False, "ValueErrorが発生するはず"
        except ValueError as exc:
            assert "bull_points" in str(exc)


def test_save_manual_analysis_persists_and_is_retrievable(tmp_path):
    db_path = tmp_path / "test.db"
    database.init_db(db_path)
    snap = _sample_snap()

    with database.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO screening_scores (run_id, code, indicators_json, created_at) VALUES (?, ?, ?, ?)",
            ("2026-08-15", "1234", database.to_json(snap), "2026-08-15T00:00:00+00:00"),
        )
        result = {
            "why_notable": "成長性が高い",
            "bull_points": ["利益成長率が高い"],
            "bear_points": ["競合が多い"],
            "checkpoints": ["次回決算の進捗率"],
            "growth_drivers": ["新製品"],
            "competitive_advantages": ["技術力"],
            "overall_comment": "引き続き注目",
            "sources": [
                {"source_type": "company_ir", "title": "IR資料", "url": "https://example.com", "retrieved_date": "2026-08-15"}
            ],
        }
        analysis_id = ai_analysis.save_manual_analysis(conn, "2026-08-15", "1234", result, rank_at_analysis=3)
        assert analysis_id is not None

        loaded = ai_analysis.get_latest_analysis(conn, "1234")

    assert loaded["why_notable"] == "成長性が高い"
    assert loaded["bull_points"] == ["利益成長率が高い"]
    assert loaded["rank_at_analysis"] == 3
    source_types = {s["source_type"] for s in loaded["sources"]}
    assert "jquants" in source_types
    assert "company_ir" in source_types
