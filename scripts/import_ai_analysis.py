"""
手動で行ったAI定性分析の結果をDBに取り込むCLIツール。

想定フロー:
  1. `python -m src.main` 実行後、data/ai_review/{run_id}/{code}.txt に
     レビュー依頼テキストが書き出される（Anthropic API呼び出しなし）。
  2. そのテキストを Claude Code のチャット等に貼り付けて分析してもらう
     （Claude Pro の範囲。追加のAPIキー・APIクレジットは不要）。
  3. 返ってきたJSON（why_notable/bull_points/... のスキーマ）をファイルに保存し、
     このスクリプトでDBに取り込む。
  4. `python -m src.main --skip-fetch` でサイトを再生成すると、
     stocks/{code}.html にAI分析セクションが反映される。

使い方:
    python scripts/import_ai_analysis.py --code 1234 --run-id 2026-08-15 --file result.json
    # --run-id を省略すると最新のrun_idを使う
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import database, ai_analysis


def _latest_run_id(conn) -> str | None:
    row = conn.execute("SELECT run_id FROM updates ORDER BY run_id DESC LIMIT 1").fetchone()
    return row["run_id"] if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description="手動AI分析結果のインポート")
    parser.add_argument("--code", required=True, help="証券コード")
    parser.add_argument("--run-id", default=None, help="対象のrun_id（省略時は最新）")
    parser.add_argument("--file", required=True, help="分析結果JSONファイルのパス")
    parser.add_argument("--reviewer", default="Claude Code（手動レビュー）", help="分析者/モデル名の記録用ラベル")
    args = parser.parse_args()

    result_path = Path(args.file)
    if not result_path.exists():
        print(f"エラー: ファイルが見つかりません: {result_path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"エラー: JSONとして読み込めませんでした: {exc}", file=sys.stderr)
        sys.exit(1)

    database.init_db()
    with database.connect() as conn:
        run_id = args.run_id or _latest_run_id(conn)
        if run_id is None:
            print("エラー: run_idが指定されておらず、DBにも実行履歴がありません。", file=sys.stderr)
            sys.exit(1)

        rank_row = conn.execute(
            "SELECT rank FROM ranking_history WHERE code = ? AND run_id = ? AND category = 'overall'",
            (args.code, run_id),
        ).fetchone()
        rank_at_analysis = rank_row["rank"] if rank_row else None

        try:
            analysis_id = ai_analysis.save_manual_analysis(
                conn,
                run_id,
                args.code,
                result,
                rank_at_analysis=rank_at_analysis,
                reviewer=args.reviewer,
            )
        except ValueError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"保存しました: code={args.code} run_id={run_id} ai_analyses.id={analysis_id}")
    print("サイトに反映するには次を実行してください: python -m src.main --skip-fetch")


if __name__ == "__main__":
    main()
