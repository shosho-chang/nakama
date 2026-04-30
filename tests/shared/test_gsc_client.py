"""Google Search Console client tests — mock SDK boundary（不打真 API）。

覆蓋：
- 缺 service account JSON → 明確 exception
- query payload 組成正確
- 首次 query 才 build service（lazy init）
- from_env 讀 GCP_SERVICE_ACCOUNT_JSON，缺 env 報錯
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from shared.gsc_client import GSCClient, GSCCredentialsError


@pytest.fixture
def fake_sa_json(tmp_path):
    """假的 service account JSON 檔案 — 存在即可，內容由 from_service_account_file mock 讀。"""
    path = tmp_path / "fake-sa.json"
    path.write_text('{"type": "service_account", "client_email": "fake@x.iam.gserviceaccount.com"}')
    return path


def test_init_missing_json_raises(tmp_path):
    missing = tmp_path / "nonexistent.json"
    with pytest.raises(GSCCredentialsError, match="not found"):
        GSCClient(service_account_json_path=missing)


def test_init_points_to_directory_raises(tmp_path):
    """若 path 是 dir 不是 file 也要擋。"""
    with pytest.raises(GSCCredentialsError):
        GSCClient(service_account_json_path=tmp_path)  # dir


def test_from_env_missing_raises(monkeypatch):
    monkeypatch.delenv("GCP_SERVICE_ACCOUNT_JSON", raising=False)
    with pytest.raises(GSCCredentialsError, match="env var not set"):
        GSCClient.from_env()


def test_from_env_happy(monkeypatch, fake_sa_json):
    monkeypatch.setenv("GCP_SERVICE_ACCOUNT_JSON", str(fake_sa_json))
    client = GSCClient.from_env()
    assert client._sa_path == fake_sa_json


def test_service_lazy_init(fake_sa_json):
    """Constructor 不讀 JSON；首次 query 才 build service（避免 import-time auth）。"""
    with patch(
        "shared.gsc_client.service_account.Credentials.from_service_account_file"
    ) as m_creds:
        GSCClient(service_account_json_path=fake_sa_json)
        m_creds.assert_not_called()  # lazy


def test_query_builds_correct_payload(fake_sa_json):
    """query body 包含 GSC API 要的所有欄位；siteUrl 帶入 query kwarg。"""
    with (
        patch("shared.gsc_client.service_account.Credentials.from_service_account_file") as m_creds,
        patch("shared.gsc_client.build") as m_build,
    ):
        m_service = MagicMock()
        m_build.return_value = m_service
        # query().execute() 回 raw GSC shape
        m_service.searchanalytics.return_value.query.return_value.execute.return_value = {
            "rows": [
                {
                    "keys": ["晨間咖啡"],
                    "clicks": 12,
                    "impressions": 890,
                    "ctr": 0.013,
                    "position": 14.3,
                }
            ]
        }

        client = GSCClient(service_account_json_path=fake_sa_json)
        rows = client.query(
            site="sc-domain:shosho.tw",
            start_date="2026-04-01",
            end_date="2026-04-24",
            dimensions=["query"],
            row_limit=500,
        )

    m_creds.assert_called_once_with(
        str(fake_sa_json),
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    # build() 帶 http=AuthorizedHttp(creds, httplib2.Http(timeout=30))，
    # 不再 pass credentials=（AuthorizedHttp 已 attach；同傳會 ValueError）。
    import google_auth_httplib2

    assert m_build.call_count == 1
    build_kwargs = m_build.call_args
    assert build_kwargs[0] == ("searchconsole", "v1")
    assert "credentials" not in build_kwargs[1]
    assert build_kwargs[1]["cache_discovery"] is False
    assert isinstance(build_kwargs[1]["http"], google_auth_httplib2.AuthorizedHttp)
    m_service.searchanalytics.return_value.query.assert_called_once_with(
        siteUrl="sc-domain:shosho.tw",
        body={
            "startDate": "2026-04-01",
            "endDate": "2026-04-24",
            "dimensions": ["query"],
            "rowLimit": 500,
        },
    )
    # SDK 內建 retry
    m_service.searchanalytics.return_value.query.return_value.execute.assert_called_once_with(
        num_retries=2
    )
    assert rows == [
        {
            "keys": ["晨間咖啡"],
            "clicks": 12,
            "impressions": 890,
            "ctr": 0.013,
            "position": 14.3,
        }
    ]


def test_query_empty_rows(fake_sa_json):
    """GSC 無資料時 `rows` 欄可能整個缺 — wrapper 回 []，不 KeyError。"""
    with (
        patch("shared.gsc_client.service_account.Credentials.from_service_account_file"),
        patch("shared.gsc_client.build") as m_build,
    ):
        m_service = MagicMock()
        m_build.return_value = m_service
        m_service.searchanalytics.return_value.query.return_value.execute.return_value = {}

        client = GSCClient(service_account_json_path=fake_sa_json)
        rows = client.query(
            site="sc-domain:shosho.tw",
            start_date="2026-04-01",
            end_date="2026-04-24",
            dimensions=["query"],
        )
    assert rows == []


def test_query_dimension_filter_groups_in_body(fake_sa_json):
    """dimension_filter_groups kwarg is forwarded to the underlying API body."""
    filters = [{"filters": [{"dimension": "query", "operator": "equals", "expression": "test kw"}]}]
    with (
        patch("shared.gsc_client.service_account.Credentials.from_service_account_file"),
        patch("shared.gsc_client.build") as m_build,
    ):
        m_service = MagicMock()
        m_build.return_value = m_service
        execute = m_service.searchanalytics.return_value.query.return_value.execute
        execute.return_value = {"rows": []}

        client = GSCClient(service_account_json_path=fake_sa_json)
        client.query(
            site="sc-domain:shosho.tw",
            start_date="2026-04-01",
            end_date="2026-04-24",
            dimensions=["query"],
            dimension_filter_groups=filters,
        )

    m_service.searchanalytics.return_value.query.assert_called_once_with(
        siteUrl="sc-domain:shosho.tw",
        body={
            "startDate": "2026-04-01",
            "endDate": "2026-04-24",
            "dimensions": ["query"],
            "rowLimit": 1000,
            "dimensionFilterGroups": filters,
        },
    )


def test_query_service_built_once_across_calls(fake_sa_json):
    """同一 client 多次 query 只 build 一次 service（省 auth handshake）。"""
    with (
        patch("shared.gsc_client.service_account.Credentials.from_service_account_file"),
        patch("shared.gsc_client.build") as m_build,
    ):
        m_service = MagicMock()
        m_build.return_value = m_service
        m_service.searchanalytics.return_value.query.return_value.execute.return_value = {
            "rows": []
        }

        client = GSCClient(service_account_json_path=fake_sa_json)
        for _ in range(3):
            client.query(
                site="sc-domain:shosho.tw",
                start_date="2026-04-01",
                end_date="2026-04-24",
                dimensions=["query"],
            )
    assert m_build.call_count == 1
