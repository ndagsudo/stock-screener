"""
AIによる定性分析。

【最重要】AIはランキングを決める主体ではない。数値スクリーニング・スコアリング・
5年シミュレーションはすべて Python (screener.py / scoring.py / forecast.py) が
完了した後、その上位候補についてだけ「なぜこの会社が面白い可能性があるのか」を
定性的に整理させる。AIには数値計算をさせず、「買い/売り/絶対上がる」等の断定も
禁止する。情報源が不明な場合は「情報を確認できませんでした」と出力させ、
受注・市場シェア・顧客・契約・将来計画・設備投資などを根拠なく記載しない。

ANTHROPIC_API_KEY が無い環境でもパイプライン全体（数値ランキングまで）は
正常に動作する。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings
from src import database

SYSTEM_PROMPT = """あなたは日本株の企業分析を手伝うリサーチアシスタントです。
渡されるのは、Pythonによる客観的な数値スクリーニング・スコアリングを既に通過した
1銘柄分の構造化データです。あなたの役割はランキングを決めることでも、
「買い」「売り」「絶対に上がる」といった投資判断を下すことでもありません。
役割は次の1点だけです:

この会社について、なぜ興味深い可能性があるのかを、
・強気材料（成長性を支持する材料）
・弱気材料（懸念点・リスク）
・次回決算などで確認すべきポイント
・事業の成長ドライバー
・競争優位性
に整理して説明することです。

厳守事項:
1. 数値計算は一切行わない（PER・成長率・スコア等は既にPythonで計算済み、渡された値をそのまま参照するに留める）。
2. 「買い」「売り」「絶対上がる」「〜倍になる」等の断定的な投資助言・株価予測をしない。
3. 受注・市場シェア・特定の顧客名・契約内容・将来の設備投資計画など、
   渡されたデータや広く確認できる公知の事実から確認できない具体的事項は書かない。
   確認できない場合は該当項目に「情報を確認できませんでした」と明記する。
4. 架空の情報を作らない。憶測は「一般的に推測される」等、事実と明確に区別する。
5. 出力は必ず record_analysis ツールを使い、指定されたフィールドに日本語で記入する。
"""

ANALYSIS_TOOL = {
    "name": "record_analysis",
    "description": "1銘柄分の定性分析結果を構造化して記録する。",
    "input_schema": {
        "type": "object",
        "properties": {
            "why_notable": {"type": "string", "description": "なぜこの会社が注目されているかの要約（2〜4文）"},
            "bull_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "強気材料（最大5件）",
            },
            "bear_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "弱気材料（最大4件）",
            },
            "checkpoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "次回決算などで確認すべきポイント（最大3件）",
            },
            "growth_drivers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "事業の成長ドライバー",
            },
            "competitive_advantages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "競争優位性",
            },
            "overall_comment": {"type": "string", "description": "AIによる総合コメント（断定を避けた要約）"},
            "confidence_note": {
                "type": "string",
                "description": "情報を確認できなかった主要な項目があれば明記。なければ空文字。",
            },
        },
        "required": [
            "why_notable",
            "bull_points",
            "bear_points",
            "checkpoints",
            "growth_drivers",
            "competitive_advantages",
            "overall_comment",
        ],
    },
}


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
        "note_to_ai": (
            "上記は全てPythonで計算済みの客観的な数値データです。"
            "この数値そのものの再計算は不要です。この会社が数値上なぜ興味深いのかを踏まえつつ、"
            "事業内容・強み・リスクについて、確認できる範囲の情報だけを使って整理してください。"
        ),
    }


def compute_input_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def select_ai_targets(overall_candidates: list[dict], rank_jumpers: Optional[list[str]] = None) -> list[dict]:
    """AI分析対象を選定する: スコア上位AI_ANALYSIS_CANDIDATES件 + 大幅順位上昇/決算大幅改善銘柄。"""
    by_score = sorted(overall_candidates, key=lambda c: c["total_score"], reverse=True)
    top = by_score[: settings.AI_ANALYSIS_CANDIDATES]
    selected = {c["code"]: c for c in top}

    rank_jumpers = rank_jumpers or []
    by_code = {c["code"]: c for c in overall_candidates}
    for code in rank_jumpers:
        if code in by_code and code not in selected:
            selected[code] = by_code[code]

    return list(selected.values())


def _needs_reanalysis(conn: sqlite3.Connection, code: str, snap: dict, input_hash: str, rank_jump: int = 0) -> bool:
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


def _call_anthropic(
    payload: dict, conn: Optional[sqlite3.Connection] = None, run_id: Optional[str] = None
) -> Optional[dict]:
    """Anthropic APIを呼び出す。ここで発生するあらゆる例外
    （クレジット不足・レート制限・ネットワーク障害・SDK未インストール等）は
    握りつぶして None を返す。AI分析はあくまで数値ランキング確定後の付加機能で
    あり、その失敗でパイプライン全体（数値スクリーニング・スコアリング・
    ランキング・サイト生成）を巻き込んで落としてはならない。"""
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        user_message = (
            "以下は数値スクリーニングを通過した銘柄の構造化データです。"
            "record_analysis ツールを使って分析結果を記録してください。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=[ANALYSIS_TOOL],
            tool_choice={"type": "tool", "name": "record_analysis"},
            messages=[{"role": "user", "content": user_message}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "record_analysis":
                return block.input
        return None
    except Exception as exc:  # noqa: BLE001
        if conn is not None and run_id is not None:
            database.log_error(conn, run_id, "ai_analysis._call_anthropic", str(exc), code=payload.get("code"))
        return None


def analyze_and_cache(
    conn: sqlite3.Connection,
    run_id: str,
    snap: dict,
    scored: dict,
    rank_at_analysis: Optional[int] = None,
    rank_jump: int = 0,
) -> Optional[int]:
    """必要なら再分析してキャッシュに保存する。分析をスキップ/再利用した場合は None。
    戻り値は新規保存した ai_analyses.id、または None。"""
    code = snap["code"]
    payload = build_prompt_payload(snap, scored)
    input_hash = compute_input_hash(payload)

    if not _needs_reanalysis(conn, code, snap, input_hash, rank_jump):
        return None

    result = _call_anthropic(payload, conn=conn, run_id=run_id)
    now = datetime.now(timezone.utc).isoformat()

    if result is None:
        # APIキー未設定 or 呼び出し失敗: 「情報を確認できませんでした」として保存し、
        # サイト全体は数値ランキングだけで正常に動作させる。
        result = {
            "why_notable": "情報を確認できませんでした（AI分析が未実行です）。",
            "bull_points": [],
            "bear_points": [],
            "checkpoints": [],
            "growth_drivers": [],
            "competitive_advantages": [],
            "overall_comment": "情報を確認できませんでした。",
            "confidence_note": "ANTHROPIC_API_KEY が未設定、またはAI呼び出しに失敗しました。",
        }

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
            settings.ANTHROPIC_MODEL if settings.ANTHROPIC_API_KEY else "none",
            input_hash,
            snap.get("latest_disclosure_date"),
            rank_at_analysis,
            now,
        ),
    )
    analysis_id = cur.lastrowid

    # 情報源: J-Quants由来の開示データは常に一次情報として記録する。
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


def run_ai_analysis(conn: sqlite3.Connection, run_id: str, targets: list[dict], rank_jumps: Optional[dict] = None) -> int:
    """targets: [{**snapshot, **scored, 'rank': int}, ...]。分析件数を返す。"""
    rank_jumps = rank_jumps or {}
    analyzed = 0
    for t in targets:
        code = t["code"]
        aid = analyze_and_cache(
            conn,
            run_id,
            t,
            t,
            rank_at_analysis=t.get("rank"),
            rank_jump=rank_jumps.get(code, 0),
        )
        if aid is not None:
            analyzed += 1
    return analyzed
