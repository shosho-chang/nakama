"""Tests for shared/secondary_storage.py (B2 client)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def _b2_env(monkeypatch):
    monkeypatch.setenv("B2_BUCKET_NAME", "nakama-backup-mirror")
    monkeypatch.setenv("B2_KEY_ID", "k_id_12345678")
    monkeypatch.setenv("B2_APPLICATION_KEY", "app_key_secret")
    monkeypatch.setenv("B2_ENDPOINT_URL", "https://s3.us-west-002.backblazeb2.com")
    yield


@pytest.mark.parametrize(
    "missing_key",
    ["B2_BUCKET_NAME", "B2_KEY_ID", "B2_APPLICATION_KEY", "B2_ENDPOINT_URL"],
)
def test_from_env_raises_when_missing_one_of(_b2_env, monkeypatch, missing_key):
    from shared.secondary_storage import B2Client, B2Unavailable

    monkeypatch.delenv(missing_key, raising=False)
    with pytest.raises(B2Unavailable) as excinfo:
        B2Client.from_env()
    assert missing_key in str(excinfo.value)


def test_from_env_constructs_with_full_env(_b2_env):
    from shared.secondary_storage import B2Client

    with patch("shared.secondary_storage.boto3.client") as mock_boto:
        mock_boto.return_value = MagicMock()
        client = B2Client.from_env()

    assert client.bucket == "nakama-backup-mirror"
    assert client.endpoint_url == "https://s3.us-west-002.backblazeb2.com"
    call_kwargs = mock_boto.call_args.kwargs
    assert call_kwargs["endpoint_url"] == "https://s3.us-west-002.backblazeb2.com"
    assert call_kwargs["aws_access_key_id"] == "k_id_12345678"


def _client_with_mock_s3(s3_mock: MagicMock):
    from shared.secondary_storage import B2Client

    with patch("shared.secondary_storage.boto3.client", return_value=s3_mock):
        return B2Client(
            endpoint_url="https://example.b2.local",
            key_id="k",
            application_key="ak",
            bucket="mirror-bucket",
        )


def test_list_objects_maps_fields():
    s3 = MagicMock()
    s3.list_objects_v2.return_value = {
        "Contents": [
            {
                "Key": "state/2026/04/25/state.db.gz",
                "Size": 12345,
                "LastModified": datetime(2026, 4, 25, 4, 0, tzinfo=timezone.utc),
                "ETag": '"abc123"',
            }
        ]
    }
    client = _client_with_mock_s3(s3)
    objs = client.list_objects(prefix="state/", max_keys=50)

    assert len(objs) == 1
    assert objs[0].key == "state/2026/04/25/state.db.gz"
    assert objs[0].size == 12345
    assert objs[0].etag == "abc123"
    s3.list_objects_v2.assert_called_once_with(Bucket="mirror-bucket", Prefix="state/", MaxKeys=50)


def test_list_objects_wraps_client_error():
    from shared.secondary_storage import B2Unavailable

    s3 = MagicMock()
    s3.list_objects_v2.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListObjectsV2"
    )
    client = _client_with_mock_s3(s3)
    with pytest.raises(B2Unavailable) as exc:
        client.list_objects()
    assert "list_objects failed" in str(exc.value)


def test_head_object_wraps_client_error():
    from shared.secondary_storage import B2Unavailable

    s3 = MagicMock()
    s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    client = _client_with_mock_s3(s3)
    with pytest.raises(B2Unavailable):
        client.head_object("missing.gz")


def test_upload_file_calls_s3_upload(tmp_path):
    src = tmp_path / "snapshot.gz"
    src.write_bytes(b"gzip-payload")
    s3 = MagicMock()
    client = _client_with_mock_s3(s3)

    client.upload_file(src, "state/2026/04/25/state.db.gz", content_type="application/gzip")

    s3.upload_file.assert_called_once_with(
        Filename=str(src),
        Bucket="mirror-bucket",
        Key="state/2026/04/25/state.db.gz",
        ExtraArgs={"ContentType": "application/gzip"},
    )


def test_upload_file_wraps_client_error(tmp_path):
    from shared.secondary_storage import B2Unavailable

    src = tmp_path / "x.bin"
    src.write_bytes(b"x")
    s3 = MagicMock()
    s3.upload_file.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "PutObject"
    )
    client = _client_with_mock_s3(s3)
    with pytest.raises(B2Unavailable) as exc:
        client.upload_file(src, "k")
    assert "upload_file key=k" in str(exc.value)


def test_download_file_wraps_client_error(tmp_path):
    from shared.secondary_storage import B2Unavailable

    s3 = MagicMock()
    s3.download_file.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject"
    )
    client = _client_with_mock_s3(s3)
    with pytest.raises(B2Unavailable):
        client.download_file("missing.gz", tmp_path / "out.gz")
