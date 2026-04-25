"""Google Search Console API v1 thin wrapper（ADR-009 Slice A）。

**職責邊界**：只做 service-account auth + raw Search Analytics query。返回 GSC
原始 rows（list[dict]），schema 建構（`KeywordMetricV1` / `StrikingDistanceV1`）
由 skill 層負責（ADR-009 triangulation T6 契約）。

**為何不用 tenacity**：Codebase 既有 clients（`wordpress_client.py` /
`r2_client.py`）都自捲 retry loop 或依賴 SDK 內建 retry；加 tenacity 會引入新
dep 且與既有 pattern 不一致。本檔用 googleapiclient 內建的 `.execute(num_retries=...)`
處理 transient 5xx，與 SDK 緊密整合、錯誤分類由 google-auth 統一決定。

**Host ↔ app-name mapping 不在本檔**（ADR-009 T5）— 該邏輯在
`shared.schemas.site_mapping`，避免 I/O adapter 污染 pure lookup 的測試邊界。

Environment:
    GSC_SERVICE_ACCOUNT_JSON_PATH  絕對路徑到 GCP service account JSON；runbook
                                    見 `docs/runbooks/gsc-oauth-setup.md`。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build

from shared.log import get_logger

logger = get_logger("nakama.gsc_client")


_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
_SDK_NUM_RETRIES = 2  # googleapiclient 內建：對 500/503 retry（exp backoff）
_HTTP_TIMEOUT = 30  # 秒；防 GSC API 掛住造成 agent thread 無限等待


class GSCCredentialsError(FileNotFoundError):
    """Service account JSON 路徑缺失或檔案不存在。"""


class GSCClient:
    """Thin wrapper — **不包含** business logic（striking-distance 判定 / 去重 /
    cannibalization detection 等），那屬 skill 層。
    """

    def __init__(self, *, service_account_json_path: str | Path) -> None:
        path = Path(service_account_json_path)
        if not path.is_file():
            raise GSCCredentialsError(
                f"GSC service account JSON not found at {path!s}; "
                "see docs/runbooks/gsc-oauth-setup.md"
            )
        self._sa_path = path
        self._service: Any | None = None  # lazy, build 在首次 query

        logger.debug("GSCClient init sa_path=%s", self._sa_path)

    @classmethod
    def from_env(cls) -> "GSCClient":
        """從 `GSC_SERVICE_ACCOUNT_JSON_PATH` env var 建構。缺 env 或檔不存在 raise。"""
        try:
            path = os.environ["GSC_SERVICE_ACCOUNT_JSON_PATH"]
        except KeyError as e:
            raise GSCCredentialsError(
                "GSC_SERVICE_ACCOUNT_JSON_PATH env var not set; "
                "see docs/runbooks/gsc-oauth-setup.md"
            ) from e
        return cls(service_account_json_path=path)

    def _get_service(self) -> Any:
        """Lazy build — 第一次 query 才真正讀 JSON + auth handshake。

        Not thread-safe (check-then-set); OK for single-process agent.
        """
        if self._service is None:
            creds = service_account.Credentials.from_service_account_file(
                str(self._sa_path), scopes=_SCOPES
            )
            http = creds.authorize(httplib2.Http(timeout=_HTTP_TIMEOUT))
            self._service = build(
                "searchconsole", "v1", credentials=creds, http=http, cache_discovery=False
            )
        return self._service

    def query(
        self,
        *,
        site: str,
        start_date: str,
        end_date: str,
        dimensions: list[str],
        row_limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Search Analytics query — 返回 raw GSC rows。

        Args:
            site:        GSC property URL — domain property 用 `sc-domain:shosho.tw`，
                         URL-prefix property 用 `https://shosho.tw/`。runbook 說明格式。
            start_date:  ISO 8601 date "YYYY-MM-DD"（UTC，GSC 資料延遲 2-3 天）。
            end_date:    同上。
            dimensions:  GSC 合法維度 — 常用 `["query", "page"]`（keyword-by-URL）。
            row_limit:   單頁上限 1000；本 wrapper 不做 pagination，呼叫方需要時自行
                         多次呼叫 + `startRow` offset（skill 層責任）。

        Returns:
            list of dict，對應 GSC 回傳 `rows[]`；每筆含 `keys`（依 dimensions 順序）
            + `clicks` / `impressions` / `ctr` / `position`。無資料時回 `[]`。

        Raises:
            googleapiclient.errors.HttpError:
                4xx（auth / malformed query） — 不 retry。
                5xx（transient） — SDK 內建 retry 2 次後仍失敗才 propagate。
        """
        service = self._get_service()
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "rowLimit": row_limit,
        }
        logger.debug(
            "gsc_query site=%s start=%s end=%s dims=%s",
            site,
            start_date,
            end_date,
            dimensions,
        )
        resp = (
            service.searchanalytics()
            .query(siteUrl=site, body=body)
            .execute(num_retries=_SDK_NUM_RETRIES)
        )
        rows = resp.get("rows", [])
        logger.info(
            "gsc_query_ok site=%s row_count=%d",
            site,
            len(rows),
        )
        return rows
