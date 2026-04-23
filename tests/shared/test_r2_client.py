"""Tests for shared/r2_client.py.

Coverage:
- from_env raises R2Unavailable when any of the 4 required envs missing
- from_env constructs with full env
- list_objects wraps boto3 list_objects_v2 and maps fields to R2Object
- list_objects propagates boto3 errors as R2Unavailable
- head_object wraps boto3 head_object and maps fields
- head_object wraps ClientError as R2Unavailable
- endpoint URL pattern derived from account id
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct12345678")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET_NAME", "backups")
    yield


# ---------------------------------------------------------------------------
# from_env env checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key",
    ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"],
)
def test_from_env_raises_when_missing_one_of(_env, monkeypatch, missing_key):
    from shared.r2_client import R2Client, R2Unavailable

    monkeypatch.delenv(missing_key, raising=False)
    with pytest.raises(R2Unavailable) as excinfo:
        R2Client.from_env()
    assert missing_key in str(excinfo.value)


def test_from_env_constructs_with_full_env(_env):
    from shared.r2_client import R2Client

    # boto3.client will be called — just patch it out to avoid real net/auth
    with patch("shared.r2_client.boto3.client") as mock_boto:
        mock_boto.return_value = MagicMock()
        client = R2Client.from_env()
    assert client.bucket == "backups"
    # boto3 client should have been invoked with R2 endpoint derived from account id
    call_kwargs = mock_boto.call_args.kwargs
    assert call_kwargs["endpoint_url"] == "https://acct12345678.r2.cloudflarestorage.com"
    assert call_kwargs["region_name"] == "auto"


# ---------------------------------------------------------------------------
# list_objects
# ---------------------------------------------------------------------------


def _raw_object(*, key: str = "k", size: int = 10, etag: str = '"abc"') -> dict:
    return {
        "Key": key,
        "Size": size,
        "LastModified": datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc),
        "ETag": etag,
    }


def _client_with_mock_s3(s3_mock: MagicMock):
    from shared.r2_client import R2Client

    with patch("shared.r2_client.boto3.client", return_value=s3_mock):
        return R2Client(
            account_id="acct",
            access_key_id="ak",
            secret_access_key="sk",
            bucket="backups",
        )


def test_list_objects_maps_fields():
    s3 = MagicMock()
    s3.list_objects_v2.return_value = {
        "Contents": [
            _raw_object(key="a", size=100, etag='"ea"'),
            _raw_object(key="b", size=200, etag='"eb"'),
        ]
    }
    client = _client_with_mock_s3(s3)
    objs = client.list_objects(prefix="daily/", max_keys=50)
    assert [o.key for o in objs] == ["a", "b"]
    assert [o.size for o in objs] == [100, 200]
    assert [o.etag for o in objs] == ["ea", "eb"]  # ETag quotes stripped
    s3.list_objects_v2.assert_called_once_with(Bucket="backups", Prefix="daily/", MaxKeys=50)


def test_list_objects_empty_when_contents_absent():
    s3 = MagicMock()
    s3.list_objects_v2.return_value = {}  # R2 returns no Contents key when empty
    client = _client_with_mock_s3(s3)
    assert client.list_objects() == []


def test_list_objects_wraps_client_error():
    from shared.r2_client import R2Unavailable

    s3 = MagicMock()
    s3.list_objects_v2.side_effect = ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "bucket gone"}}, "ListObjectsV2"
    )
    client = _client_with_mock_s3(s3)
    with pytest.raises(R2Unavailable) as exc:
        client.list_objects()
    assert "list_objects failed" in str(exc.value)


# ---------------------------------------------------------------------------
# head_object
# ---------------------------------------------------------------------------


def test_head_object_maps_fields():
    s3 = MagicMock()
    s3.head_object.return_value = {
        "ContentLength": 4096,
        "LastModified": datetime(2026, 4, 23, 8, 0, tzinfo=timezone.utc),
        "ETag": '"deadbeef"',
    }
    client = _client_with_mock_s3(s3)
    obj = client.head_object("daily/db.tar.zst")
    assert obj.key == "daily/db.tar.zst"
    assert obj.size == 4096
    assert obj.etag == "deadbeef"


def test_head_object_wraps_client_error():
    from shared.r2_client import R2Unavailable

    s3 = MagicMock()
    s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    client = _client_with_mock_s3(s3)
    with pytest.raises(R2Unavailable):
        client.head_object("missing.zst")
