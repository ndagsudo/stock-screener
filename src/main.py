"""
週次パイプラインのオーケストレーター。

実行順序:
  1. J-Quantsデータ取得        (data_loader)
  2. SQLite更新                (data_loader / database)
  3. 財務指標計算               (indicators, screener内で実施)
  4. 候補抽出                   (screener)
  5. スコア計算                 (scoring)
  6. 5年シミュレーション         (forecast)
  7. ランキング生成・順位変動    (ranking)
  8. AIレビュー用データのエクスポート (ai_analysis)
  9. 過去ランキングの実績評価    (performance)
  10. HTML生成                  (report)

途中で1銘柄のデータが欠けていても全体を止めない。致命的な例外だけ
updates テーブルに status='failed' として記録して再送出する。

【重要】このパイプラインは Anthropic API を一切呼び出さない。ステップ8は
スコア上位銘柄について「AIに渡すデータ＋調査してほしい観点」をテキスト
ファイルに書き出すだけで、APIキーもAPIクレジットも不要。実際の定性分析は
利用者が手動で（Claude Code 等で）行い、scripts/import_ai_analysis.py で
結果をDBに取り込む。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database, data_loader, screener, scoring, forecast, ranking, ai_analysis, performance, report


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_run_id() -> str:
    """ランキング作成日。GitHub Actionsのcronスケジュールは
    UTCで指定しているが、run_idは『毎週土曜日』という運用上の意味を
    持つため、日本時間(Asia/Tokyo)の日付を使う。"""
    return datetime.now(ZoneInfo(settings.UPDATE_TIMEZONE)).strftime("%Y-%m-%d")


def run_pipeline(
    run_id: str | None = None,
    max_codes: int | None = None,
    skip_fetch: bool = False,
) -> dict:
    run_id = run_id or default_run_id()
    max_codes = max_codes if max_codes is not None else settings.MAX_CODES_PER_RUN
    database.init_db()

    with database.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO updates (run_id, started_at, status) VALUES (?, ?, 'running')",
            (run_id, _now_iso()),
        )

    summary: dict = {"run_id": run_id}
    try:
        # 1-2. データ取得
        if not skip_fetch:
            fetch_summary = data_loader.load_all(run_id, max_codes=max_codes)
            summary["fetch"] = fetch_summary
            if not fetch_summary.get("configured"):
                with database.connect() as conn:
                    database.log_error(
                        conn, run_id, "main", "JQUANTS_API_KEY未設定のためデータ取得をスキップしました"
                    )

        with database.connect() as conn:
            # 3-4. 指標計算・候補抽出（as_of_date=run_id とすることで、
            # 過去のrun_idを指定して再生成した場合でもlook-ahead biasが起きない）
            candidates = screener.run_screening(conn, run_id, as_of_date=run_id)
            summary["candidates_screened"] = len(candidates)

            # 5. スコアリング
            scored_candidates = []
            score_rows = []
            now = _now_iso()
            for snap in candidates:
                scored = scoring.compute_total_score(snap)
                merged = {**snap, **scored}
                scored_candidates.append(merged)
                score_rows.append(
                    {
                        "run_id": run_id,
                        "code": snap["code"],
                        "growth_score": scored["growth_score"],
                        "valuation_score": scored["valuation_score"],
                        "profitability_score": scored["profitability_score"],
                        "health_score": scored["health_score"],
                        "momentum_score": scored["momentum_score"],
                        "price_position_score": scored["price_position_score"],
                        "total_score": scored["total_score"],
                        "indicators_json": database.to_json(snap),
                        "created_at": now,
                    }
                )
            database.upsert(conn, "screening_scores", score_rows, ["run_id", "code"])

            top_scored = sorted(scored_candidates, key=lambda c: c["total_score"], reverse=True)[
                : settings.TOP_SCORE_CANDIDATES
            ]
            summary["candidates_scored"] = len(top_scored)

            # 6. 5年シミュレーション
            forecast_rows = []
            for c in top_scored:
                scenarios = forecast.run_forecast(c)
                c["forecast_multiples"] = {s["scenario"]: s["multiple"] for s in scenarios}
                for s in scenarios:
                    forecast_rows.append({"run_id": run_id, **s})
            database.upsert(conn, "forecasts", forecast_rows, ["run_id", "code", "scenario"])

            # 7. ランキング生成・順位変動
            categories = ranking.build_categories(top_scored)
            ranking.save_ranking_history(conn, run_id, categories)

            overall_rows = conn.execute(
                "SELECT code, rank, prev_rank FROM ranking_history WHERE run_id = ? AND category = 'overall'",
                (run_id,),
            ).fetchall()
            rank_by_code = {r["code"]: r["rank"] for r in overall_rows}
            rank_jumps = {
                r["code"]: (r["prev_rank"] - r["rank"])
                for r in overall_rows
                if r["prev_rank"] is not None and (r["prev_rank"] - r["rank"]) > 0
            }

            # 8. AIレビュー用データのエクスポート（Anthropic API呼び出しなし）
            # スコア上位銘柄について「渡すデータ＋調査観点」をテキストファイルに
            # 書き出すだけ。実際の分析は利用者が手動で行う
            # （data/ai_review/{run_id}/{code}.txt を参照）。
            try:
                ai_targets = ai_analysis.select_ai_targets(top_scored, rank_jumpers=list(rank_jumps.keys()))
                for t in ai_targets:
                    t["rank"] = rank_by_code.get(t["code"])
                export_results = ai_analysis.export_targets_for_manual_review(
                    conn, run_id, ai_targets, rank_jumps=rank_jumps
                )
                summary["ai_export_count"] = sum(1 for r in export_results if r["status"] == "exported")
                summary["ai_analyzed"] = summary["ai_export_count"]  # updatesテーブルの列名は維持
            except Exception as exc:  # noqa: BLE001
                database.log_error(conn, run_id, "main.ai_analysis", str(exc))
                summary["ai_export_count"] = 0
                summary["ai_analyzed"] = 0

            # 9. 実績検証: 今週のweekly_top10を将来の検証対象として登録し、
            #    到来済みホライズンの実績を更新する
            weekly_rows = [dict(r) for r in conn.execute(
                "SELECT code, rank, score, price FROM ranking_history WHERE run_id = ? AND category = 'weekly_top10'",
                (run_id,),
            ).fetchall()]
            performance.register_forecast_targets(conn, run_id, run_id, weekly_rows)
            summary["realized_updated"] = performance.update_realized_returns(conn)

            conn.execute(
                """
                UPDATE updates SET finished_at = ?, status = 'success',
                    candidates_screened = ?, candidates_scored = ?, ai_analyzed = ?
                WHERE run_id = ?
                """,
                (
                    _now_iso(),
                    summary.get("candidates_screened", 0),
                    summary.get("candidates_scored", 0),
                    summary.get("ai_analyzed", 0),
                    run_id,
                ),
            )

        # 10. HTML生成
        with database.connect() as conn:
            report.render_site(conn, run_id=run_id)
        summary["status"] = "success"

    except Exception as exc:  # noqa: BLE001
        with database.connect() as conn:
            database.log_error(conn, run_id, "main", f"{exc}\n{traceback.format_exc()}")
            conn.execute(
                "UPDATE updates SET finished_at = ?, status = 'failed', notes = ? WHERE run_id = ?",
                (_now_iso(), str(exc)[:500], run_id),
            )
        summary["status"] = "failed"
        summary["error"] = str(exc)
        raise
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="中小型株スクリーナー 週次パイプライン")
    parser.add_argument("--run-id", default=None, help="ランキング作成日 (YYYY-MM-DD)。省略時は本日。")
    parser.add_argument("--max-codes", type=int, default=None, help="取得対象銘柄数の上限（開発・検証用）")
    parser.add_argument("--skip-fetch", action="store_true", help="J-Quants取得をスキップしDB内の既存データのみ使用")
    args = parser.parse_args()

    result = run_pipeline(run_id=args.run_id, max_codes=args.max_codes, skip_fetch=args.skip_fetch)
    print(database.to_json(result))


if __name__ == "__main__":
    main()
