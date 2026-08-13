"""
J-Quants API V2 クライアント。

現行仕様（2026年時点、https://jpx-jquants.com/ja/spec/ 配下の公式ドキュメント）:
  - ベースURL: https://api.jquants.com/v2
  - 認証: リクエストヘッダー `x-api-key` にダッシュボードで発行したAPIキーを設定する
    （旧V1のリフレッシュトークン/IDトークン方式は廃止された）
  - レスポンスは {"data": [...], "pagination_key": "..."} の形式
  - 契約プランによって取得可能なエンドポイント・項目・レート制限が異なる

このクライアントは「契約プランで取得できないデータがあってもパイプライン全体を
止めない」という要件を満たすため、HTTPエラーやフィールド欠損時は例外を握りつぶし
空リスト/Noneを返し、呼び出し元がエラーログに記録できるようにする。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings


class JQuantsAPIError(Exception):
    pass


@dataclass
class JQuantsClient:
    api_key: str = ""
    base_url: str = settings.JQUANTS_BASE_URL
    requests_per_minute: int = settings.JQUANTS_REQUESTS_PER_MINUTE
    timeout: int = 30
    max_retries: int = 3

    def __post_init__(self) -> None:
        self.api_key = self.api_key or settings.JQUANTS_API_KEY
        self._min_interval = 60.0 / max(self.requests_per_minute, 1)
        self._last_request_at = 0.0
        self.session = requests.Session()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        if not self.is_configured:
            return None
        url = f"{self.base_url}{path}"
        headers = {"x-api-key": self.api_key}
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
                self._last_request_at = time.monotonic()
                if resp.status_code == 429:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:  # noqa: PERF203
                last_exc = exc
                time.sleep(min(2 ** attempt, 10))
        raise JQuantsAPIError(f"GET {path} failed after {self.max_retries} attempts: {last_exc}")

    def _get_all_pages(self, path: str, params: Optional[dict] = None) -> list[dict]:
        """pagination_key を辿って全ページを取得する。"""
        params = dict(params or {})
        results: list[dict] = []
        while True:
            payload = self._get(path, params)
            if not payload:
                break
            results.extend(payload.get("data", []))
            pagination_key = payload.get("pagination_key")
            if not pagination_key:
                break
            params["pagination_key"] = pagination_key
        return results

    # ------------------------------------------------------------------
    # 上場銘柄一覧 GET /equities/master
    # ------------------------------------------------------------------
    def get_listed_info(self, code: Optional[str] = None, date: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        return self._get_all_pages("/equities/master", params)

    # ------------------------------------------------------------------
    # 株価四本値 GET /equities/bars/daily
    # ------------------------------------------------------------------
    def get_daily_quotes(
        self,
        code: Optional[str] = None,
        date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return self._get_all_pages("/equities/bars/daily", params)

    # ------------------------------------------------------------------
    # 財務情報サマリ GET /fins/summary
    # ------------------------------------------------------------------
    def get_financial_summary(self, code: Optional[str] = None, date: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        return self._get_all_pages("/fins/summary", params)

    # ------------------------------------------------------------------
    # 配当情報 GET /fins/dividend
    # ------------------------------------------------------------------
    def get_dividends(
        self,
        code: Optional[str] = None,
        date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if code:
            params["code"] = code
        if date:
            params["date"] = date
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return self._get_all_pages("/fins/dividend", params)

    # ------------------------------------------------------------------
    # 決算発表予定日 GET /equities/earnings-calendar
    # ------------------------------------------------------------------
    def get_earnings_calendar(self) -> list[dict]:
        return self._get_all_pages("/equities/earnings-calendar")
