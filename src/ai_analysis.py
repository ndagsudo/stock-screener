"""
AIによる定性分析（手動レビュー方式）。

【最重要】AIはランキングを決める主体ではない。数値スクリーニング・スコアリング・
5年シミュレーションはすべて Python (screener.py / scoring.py / forecast.py) が
完了した後、その上位候補についてだけ「なぜこの会社が面白い可能性があるのか」を
定性的に整理させる。AIには数値計算をさせず、「買い/売り/絶対上がる」等の断定も
禁止する。情報源が不明な場合は「情報を確認できませんでした」と出力させ、
受注・市場シェア・顧客・契約・将来計画・設備投資などを根拠なく記載しない。

【重要な設計変更】このモジュールは Anthropic API を直接呼び出さない。
週次パイプライン（GitHub Actions）から Anthropic の API キー・APIクレジットへの
依存を完全に排除するため、「AIに渡す構造化データ＋調査してほしい観点」を
テキストファイルとして書き出すところまでを自動化し、実際の分析は利用者が
手動で（Claude Code のチャット等、Claude Pro の範囲で）行う。分析結果は
save_manual_analysis() 経由で DB に保存する（scripts/import_ai_analysis.py が
そのCLIエントリポイント）。

これにより、パイプライン全体（数値ランキングまで）はAPIキーが一切無い環境でも
正常に動作する。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database

# 利用者から提示された「なぜこの会社が面白いのか」を調べる際の観点。
# 数値計算はさせず、あくまで定性的な調査・整理の指針として使う。
RESEARCH_QUESTIONS = [
    "この会社が市場から過小評価されている可能性はあるか",
    "今後3〜5年で利益が伸びる理由は何か",
    "その成長の源泉は何か（新製品・新市場・構造変化など）",
    "競合と比較して何が優れているか",
    "参入障壁はあるか",
    "ニッチトップ企業か",
    "海外投資家から評価される可能性はあるか",
    "株価が再評価されるきっかけ（カタリスト）は何か",
    "経営陣・資本政策に問題はないか",
    "成長ストーリーが崩れるリスクは何か",
    "現在の株価に成長期待がどの程度織り込まれているか",
    "3〜5年後に利益がどの程度伸びる可能性があるか",
]

ANALYSIS_SCHEMA_DESCRIPTION = """出力は以下のキーを持つJSONオブジェクト1つだけにしてください（前後に説明文を付けない）:

{
  "why_notable": "なぜこの会社が注目されているかの要約（2〜4文、string）",
  "bull_points": ["強気材料（最大5件、string配列）"],
  "bear_points": ["弱気材料（最大4件、string配列）"],
  "checkpoints": ["次回決算などで確認すべきポイント（最大3件、string配列）"],
  "growth_drivers": ["事業の成長ドライバー（string配列）"],
  "competitive_advantages": ["競争優位性（string配列）"],
  "overall_comment": "総合コメント（断定を避けた要約、string）",
  "confidence_note": "情報を確認できなかった主要項目があれば明記。無ければ空文字（string）"
}"""

REVIEW_INSTRUCTIONS = """あなたは日本株の企業分析を手伝うリサーチアシスタントです。
以下は、Pythonによる客観的な数値スクリーニング・スコアリングを既に通過した
1銘柄分の構造化データです。あなたの役割はランキングを決めることでも、
「買い」「売り」「絶対に上がる」といった投資判断を下すことでもありません。

役割は次の1点だけです: この会社について、なぜ興味深い可能性があるのかを、
下記の観点を参考にしながら調査し整理することです。

【調査してほしい観点】
{questions}

【厳守事項】
1. 数値計算は一切行わない（PER・成長率・スコア等は下記データで既にPython側で
   計算済み。渡された値をそのまま参照するに留める）。
2. 「買い」「売り」「絶対上がる」「〜倍になる」等の断定的な投資助言・株価予測をしない。
3. 受注・市場シェア・特定の顧客名・契約内容・将来の設備投資計画など、
   確認できない具体的事実は書かない。確認できない場合は該当項目に
   「情報を確認できませんでした」と明記する。
4. 架空の情報を作らない。憶測は「一般的に推測される」等、事実と明確に区別する。

【この銘柄の数値データ（Pythonで計算済み・そのまま参照）】
{payload_json}

{schema}
"""


def _pct(v: Optional[float]) -> str:
    return "データなし" if v is None else f"{v:.1%}"


def _num(v: Optional[float], unit: str = "") -> str:
    return "データなし" if v is None else f"{v:,.1f}{unit}"


def build_prompt_payload(snap: dict, scored: dict) -> dict:
    """AIに渡す構造化データ。数値はPythonで計算済みのものをそのまま渡す。"""
    return {
        "company_name": snap.get("name"),
        "code": snap.get("code"),
        "as_of_date": snap.get("as_of_date"),
        "latest_disclosure_date": snap.get("latest_disclosure_date"),
        "price": snap.get("price"),
        "market_cap_yen": snap.get("market_cap"),
        "per": snap.get("per"),
        "pbr": snap.get("pbr"),
        "peg": snap.get("peg"),
        "roe": snap.get("roe"),
        "roa": snap.get("roa"),
        "operating_margin": snap.get("operating_margin"),
        "equity_ratio": snap.get("equity_ratio"),
        "sales_growth_yoy": snap.get("sales_growth_yoy"),
        "operating_profit_growth_yoy": snap.get("operating_profit_growth_yoy"),
        "net_profit_growth_yoy": snap.get("net_profit_growth_yoy"),
        "eps_growth_yoy": snap.get("eps_growth_yoy"),
        "eps_cagr_5y": snap.get("eps_cagr"),
        "sales_cagr_5y": snap.get("sales_cagr"),
        "forecast_net_profit_growth": snap.get("forecast_net_profit_growth"),
        "drawdown_from_52w_high": snap.get("drawdown_from_52w_high"),
        "ma200_deviation": snap.get("ma200_deviation"),
        "return_1y": snap.get("return_1y"),
        "dividend_yield": snap.get("dividend_yield"),
        "numeric_score_total_100": scored.get("total_score"),
        "numeric_score_breakdown": {
            "growth": scored.get("growth_score"),
            "valuation": scored.get("valuation_score"),
            "profitability": scored.get("profitability_score"),
            "health": scored.get("health_score"),
            "momentum": scored.get("momentum_score"),
            "price_position": scored.get("price_position_score"),
        },
        "sector33": snap.get("sector33_name"),
    }


def build_manual_review_prompt(snap: dict, scored: dict) -> str:
    """Claude Code 等に貼り付けてそのまま使える、1銘柄分のレビュー依頼テキストを組み立てる。
    Anthropic API は一切呼び出さない（純粋な文字列整形のみ、コスト・APIキー不要）。"""
    payload = build_prompt_payload(snap, scored)
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(RESEARCH_QUESTIONS, start=1))
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return REVIEW_INSTRUCTIONS.format(
        questions=questions, payload_json=payload_json, schema=ANALYSIS_SCHEMA_DESCRIPTION
    )


def compute_input_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def select_ai_targets(overall_candidates: list[dict], rank_jumpers: Optional[list[str]] = None) -> list[dict]:
    """AIレビュー対象を選定する: スコア上位AI_ANALYSIS_CANDIDATES件 + 大幅順位上昇銘柄。
    ここで選ばれた銘柄だけがエクスポート対象になる。AI自身が対象を選ぶことはない。"""
    by_score = sorted(overall_candidates, key=lambda c: c["total_score"], reverse=True)
    top = by_score[: settings.AI_ANALYSIS_CANDIDATES]
    selected = {c["code"]: c for c in top}

    rank_jumpers = rank_jumpers or []
    by_code = {c["code"]: c for c in overall_candidates}
    for code in rank_jumpers:
        if code in by_code and code not in selected:
            selected[code] = by_code[code]

    return list(selected.values())


def _needs_review(conn: sqlite3.Connection, code: str, snap: dict, input_hash: str, rank_jump: int = 0) -> bool:
    """既存のキャッシュ済み分析が十分新しければ再エクスポートをスキップする
    （手動レビューの手間を無駄に増やさないため）。"""
    row = conn.execute(
        "SELECT * FROM ai_analyses WHERE code = ? ORDER BY created_at DESC LIMIT 1", (code,)
    ).fetchone()
    if row is None:
        return True
    if row["input_hash"] != input_hash:
        return True
    if settings.AI_REANALYSIS_ON_NEW_FINANCIALS:
        cached_disc = row["financials_disclosure_date"]
        current_disc = snap.get("latest_disclosure_date")
        if current_disc and cached_disc and current_disc > cached_disc:
            return True
    try:
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created_at).days
        if age_days >= settings.AI_REANALYSIS_MAX_AGE_DAYS:
            return True
    except (TypeError, ValueError):
        return True
    if rank_jump >= settings.AI_REANALYSIS_ON_RANK_JUMP:
        return True
    return False


def export_targets_for_manual_review(
    conn: sqlite3.Connection,
    run_id: str,
    targets: list[dict],
    rank_jumps: Optional[dict] = None,
    output_dir: Optional[Path] = None,
) -> list[dict]:
    """targets: [{**snapshot, **scored, 'rank': int}, ...]。
    Anthropic APIを一切呼び出さず、レビュー依頼テキストをファイルに書き出すだけ。
    戻り値は各銘柄の {code, path, status} のリスト（status: 'exported' | 'skipped_fresh'）。"""
    rank_jumps = rank_jumps or {}
    output_dir = output_dir or (settings.DATA_DIR / "ai_review" / run_id)
    results = []
    exported_index = []

    for t in targets:
        code = t["code"]
        payload = build_prompt_payload(t, t)
        input_hash = compute_input_hash(payload)
        if not _needs_review(conn, code, t, input_hash, rank_jumps.get(code, 0)):
            results.append({"code": code, "path": None, "status": "skipped_fresh"})
            continue

        output_dir.mkdir(parents=True, exist_ok=True)
        prompt_text = build_manual_review_prompt(t, t)
        file_path = output_dir / f"{code}.txt"
        file_path.write_text(prompt_text, encoding="utf-8")
        results.append({"code": code, "path": str(file_path), "status": "exported"})
        exported_index.append(
            {"code": code, "name": t.get("name"), "score": t.get("total_score"), "file": f"{code}.txt"}
        )

    if exported_index:
        index_path = output_dir / "_index.json"
        index_path.write_text(database.to_json(exported_index), encoding="utf-8")

    return results


def save_manual_analysis(
    conn: sqlite3.Connection,
    run_id: str,
    code: str,
    result: dict,
    rank_at_analysis: Optional[int] = None,
    reviewer: str = "Claude Code（手動レビュー）",
) -> int:
    """手動で行ったAI定性分析の結果をDBに保存する。result は
    ANALYSIS_SCHEMA_DESCRIPTION に沿った辞書（why_notable/bull_points/...）。
    scripts/import_ai_analysis.py から呼ばれる。"""
    required_keys = [
        "why_notable",
        "bull_points",
        "bear_points",
        "checkpoints",
        "growth_drivers",
        "competitive_advantages",
        "overall_comment",
    ]
    missing = [k for k in required_keys if k not in result]
    if missing:
        raise ValueError(f"分析結果に必須フィールドが不足しています: {missing}")

    scored_row = conn.execute(
        "SELECT indicators_json FROM screening_scores WHERE code = ? AND run_id = ?", (code, run_id)
    ).fetchone()
    snap = database.from_json(scored_row["indicators_json"], {}) if scored_row else {}

    now = datetime.now(timezone.utc).isoformat()
    payload = build_prompt_payload(snap, snap)
    input_hash = compute_input_hash(payload)

    cur = conn.execute(
        """
        INSERT INTO ai_analyses (
            code, run_id, analysis_date, why_notable, bull_points_json, bear_points_json,
            checkpoints_json, growth_drivers_json, competitive_advantages_json,
            overall_comment, model, input_hash, financials_disclosure_date,
            rank_at_analysis, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            run_id,
            now[:10],
            result.get("why_notable", ""),
            database.to_json(result.get("bull_points", [])),
            database.to_json(result.get("bear_points", [])),
            database.to_json(result.get("checkpoints", [])),
            database.to_json(result.get("growth_drivers", [])),
            database.to_json(result.get("competitive_advantages", [])),
            result.get("overall_comment", ""),
            reviewer,
            input_hash,
            snap.get("latest_disclosure_date"),
            rank_at_analysis,
            now,
        ),
    )
    analysis_id = cur.lastrowid

    conn.execute(
        """
        INSERT INTO sources (analysis_id, code, source_type, title, url, retrieved_date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            analysis_id,
            code,
            "jquants",
            "J-Quants API 財務・株価データ",
            "https://jpx-jquants.com/",
            snap.get("as_of_date"),
        ),
    )
    for src in result.get("sources", []):
        conn.execute(
            """
            INSERT INTO sources (analysis_id, code, source_type, title, url, retrieved_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                code,
                src.get("source_type", "other"),
                src.get("title", ""),
                src.get("url", ""),
                src.get("retrieved_date", now[:10]),
            ),
        )
    return analysis_id


def get_latest_analysis(conn: sqlite3.Connection, code: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM ai_analyses WHERE code = ? ORDER BY created_at DESC LIMIT 1", (code,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["bull_points"] = database.from_json(d.pop("bull_points_json"), [])
    d["bear_points"] = database.from_json(d.pop("bear_points_json"), [])
    d["checkpoints"] = database.from_json(d.pop("checkpoints_json"), [])
    d["growth_drivers"] = database.from_json(d.pop("growth_drivers_json"), [])
    d["competitive_advantages"] = database.from_json(d.pop("competitive_advantages_json"), [])
    sources = conn.execute(
        "SELECT source_type, title, url, retrieved_date FROM sources WHERE analysis_id = ?", (row["id"],)
    ).fetchall()
    d["sources"] = [dict(s) for s in sources]
    return d
