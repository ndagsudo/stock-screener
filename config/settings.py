"""
中小型株スクリーナー 設定ファイル

すべての閾値・重み・シナリオ前提をここに集約する。
数値の意味を変えたいときはコードではなくこのファイルを編集する。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")  # ローカル開発用。存在しなければ何もしない（GitHub Actionsでは無視される）
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "stock.db"
SITE_DIR = BASE_DIR / "site"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# --------------------------------------------------------------------------
# J-Quants API (V2)
# --------------------------------------------------------------------------
JQUANTS_API_KEY = os.environ.get("JQUANTS_API_KEY", "")
JQUANTS_BASE_URL = "https://api.jquants.com/v2"
# Free plan = 5 req/min. Keep a conservative default; override per-plan.
JQUANTS_REQUESTS_PER_MINUTE = int(os.environ.get("JQUANTS_REQUESTS_PER_MINUTE", "5"))

# --------------------------------------------------------------------------
# AI定性分析（手動レビュー方式）
#
# このプロジェクトは Anthropic API を直接呼び出さない（APIキー・APIクレジット
# 不要）。パイプラインはスコア上位銘柄について「AIに渡すデータ＋調査観点」を
# data/ai_review/{run_id}/{code}.txt に書き出すだけで、実際の定性分析は
# 利用者が手動で（Claude Code 等で）行い、scripts/import_ai_analysis.py で
# 結果をDBに取り込む。
# --------------------------------------------------------------------------

# 既存の分析結果が十分新しければ再エクスポートをスキップする（レビューの
# 手間を無駄に増やさないための閾値）:
AI_REANALYSIS_MAX_AGE_DAYS = 90          # キャッシュが古いとみなす日数
AI_REANALYSIS_ON_RANK_JUMP = 5           # 順位がN位以上改善したら再エクスポート対象にする
AI_REANALYSIS_ON_NEW_FINANCIALS = True   # 新しい決算が出ていたら再エクスポート対象にする

# --------------------------------------------------------------------------
# 対象ユニバース（時価総額フィルタ、円）
# --------------------------------------------------------------------------
MIN_MARKET_CAP = 10_000_000_000      # 100億円
MAX_MARKET_CAP = 300_000_000_000     # 3,000億円

# 時価総額帯に該当する銘柄のうち、実際に価格履歴・決算を取得する銘柄数の上限。
# J-Quants Freeプランは5req/分しかなく、1銘柄あたり3コール（価格・決算・配当）
# 必要なため、無制限だと数百銘柄で数時間かかる。0 または None で無制限。
# Light/Standard/Premiumプラン（60/120/500 req/分）に上げた場合はこの値も
# 大きくしてよい。
MAX_CODES_PER_RUN = int(os.environ.get("MAX_CODES_PER_RUN", "120")) or None

# 除外する市場区分/商品区分キーワード（REIT・ETF・ETN・投信など）
EXCLUDE_NAME_KEYWORDS = [
    "投資法人", "ＲＥＩＴ", "REIT", "上場投信", "ETF", "ETN", "インフラファンド",
]
# J-Quants /equities/master の ProdCat / Mkt コードでの除外（REIT市場等）
EXCLUDE_PRODUCT_CATEGORIES = ["050", "060", "070"]  # REIT/ETF/ETN系（要現物確認、不明時は名称フィルタ優先）

# --------------------------------------------------------------------------
# スクリーニング（数値条件で絞り込む一次候補数の目安）
# --------------------------------------------------------------------------
SCREENING_MIN_CANDIDATES = 100
SCREENING_MAX_CANDIDATES = 300

# 最低限のクオリティライン（データが揃っている銘柄のみ判定、欠損は除外しない）
MIN_ROE = 0.0                # ROEが分かっていてマイナスは除外対象
MIN_OPERATING_MARGIN = 0.0
MAX_PER = 100.0               # 異常値除外用の上限（割安性評価そのものはスコアで行う）
MIN_EPS_GROWTH = None         # Noneなら絞り込みに使わない（欠損許容）

# --------------------------------------------------------------------------
# スコアリング後の絞り込み段数
# --------------------------------------------------------------------------
TOP_SCORE_CANDIDATES = 50     # スコアリング後のトップ
AI_ANALYSIS_CANDIDATES = 20   # AI定性分析の対象数
WEEKLY_HIGHLIGHT_COUNT = 10   # 「今週の注目株」表示数

# --------------------------------------------------------------------------
# スコア配点（合計100点）
# --------------------------------------------------------------------------
SCORE_WEIGHTS = {
    "growth": 30,
    "valuation": 25,
    "profitability": 15,
    "health": 10,
    "momentum": 10,
    "price_position": 10,
}

# --------------------------------------------------------------------------
# 5年シミュレーション前提
# --------------------------------------------------------------------------
FORECAST_YEARS = 5
FORECAST_SCENARIOS = {
    # growth_multiplier: 実績EPS成長率にかける係数（弱気・標準・強気）
    "bear": {"growth_multiplier": 0.5, "exit_per": 15},
    "base": {"growth_multiplier": 1.0, "exit_per": 20},
    "bull": {"growth_multiplier": 1.3, "exit_per": 25},
}
# 成長率が欠損/異常な場合のフォールバック上限・下限（暴走防止）
MAX_ASSUMED_GROWTH_RATE = 0.60
MIN_ASSUMED_GROWTH_RATE = -0.30

# --------------------------------------------------------------------------
# 業績回復型の判定条件
# --------------------------------------------------------------------------
TURNAROUND_RULES = {
    "min_profit_growth_latest": 0.10,   # 直近decisiveな増益率
    "max_per_for_turnaround": 30.0,
    "min_drawdown_from_high": 0.20,     # 52週高値からの下落率がこれ以上
}

# --------------------------------------------------------------------------
# 順位変動の表示閾値
# --------------------------------------------------------------------------
RANK_CHANGE_NEW_THRESHOLD_WEEKS = 4  # 過去N週にランクインしていなければ NEW 扱い

# --------------------------------------------------------------------------
# 実績検証（バックテスト）
# --------------------------------------------------------------------------
PERFORMANCE_HORIZONS_DAYS = {
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 182,
    "1y": 365,
}
PERFORMANCE_WINDOW_MONTHS = 6  # トップページに出す「過去半年の成績」の対象期間
BENCHMARK_CODE = "TOPIX"

# --------------------------------------------------------------------------
# 実行タイミング（表示用。実スケジュールは GitHub Actions 側で管理）
# --------------------------------------------------------------------------
UPDATE_TIMEZONE = "Asia/Tokyo"
