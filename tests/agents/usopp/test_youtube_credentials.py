"""YouTube credential lifecycle tests with no live OAuth or platform calls."""

import os
import traceback

import pytest

from agents.usopp.youtube_credentials import YouTubeCredentialError, load_youtube_client


class _FakeCredentials:
    def __init__(
        self,
        *,
        valid: bool,
        expired: bool,
        refresh_token: str | None,
        serialized: str | None = None,
        refresh_error: Exception | None = None,
    ) -> None:
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.serialized = serialized
        self.refresh_error = refresh_error
        self.refresh_calls = 0
        self.last_request = None

    def refresh(self, request: object) -> None:
        self.refresh_calls += 1
        self.last_request = request
        if self.refresh_error is not None:
            raise self.refresh_error
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        if self.serialized is None:
            raise AssertionError("valid credentials must not be persisted")
        return self.serialized


def _assert_error_redacted(error: YouTubeCredentialError, *sentinels: str) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    for sentinel in sentinels:
        assert sentinel not in str(error)
        assert sentinel not in rendered


def test_valid_credentials_build_client_without_refresh_or_write(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    original = '{"fixture":"redacted"}'
    token_path.write_text(original, encoding="utf-8")
    credentials = _FakeCredentials(valid=True, expired=False, refresh_token="fixture-refresh")
    client = object()
    built = []

    def build_client(service, version, *, credentials):
        built.append((service, version, credentials))
        return client

    result = load_youtube_client(
        token_path,
        credentials_loader=lambda _path: credentials,
        request_factory=lambda: (_ for _ in ()).throw(
            AssertionError("valid credentials must not create a refresh request")
        ),
        client_builder=build_client,
    )

    assert result is client
    assert credentials.refresh_calls == 0
    assert built == [("youtube", "v3", credentials)]
    assert token_path.read_text(encoding="utf-8") == original


def test_expired_access_token_refreshes_once_persists_and_builds_client(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    token_path.write_text('{"fixture":"old"}', encoding="utf-8")
    refreshed = '{"fixture":"refreshed"}'
    credentials = _FakeCredentials(
        valid=False,
        expired=True,
        refresh_token="fixture-refresh",
        serialized=refreshed,
    )
    request = object()
    built = []

    def build_client(service, version, *, credentials):
        built.append((service, version, credentials))
        return "youtube-client"

    result = load_youtube_client(
        token_path,
        credentials_loader=lambda _path: credentials,
        request_factory=lambda: request,
        client_builder=build_client,
    )

    assert result == "youtube-client"
    assert credentials.refresh_calls == 1
    assert credentials.last_request is request
    assert token_path.read_text(encoding="utf-8") == refreshed
    assert built == [("youtube", "v3", credentials)]


def test_refresh_failure_is_single_attempt_secret_free_and_preserves_token(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    original = '{"fixture":"old-but-parseable"}'
    token_path.write_text(original, encoding="utf-8")
    sentinel = "invalid_grant fixture-refresh fixture-access client-secret"
    credentials = _FakeCredentials(
        valid=False,
        expired=True,
        refresh_token="fixture-refresh",
        refresh_error=RuntimeError(sentinel),
    )
    builder_calls = []

    with pytest.raises(YouTubeCredentialError) as caught:
        load_youtube_client(
            token_path,
            credentials_loader=lambda _path: credentials,
            request_factory=object,
            client_builder=lambda *_args, **_kwargs: builder_calls.append(True),
        )

    assert credentials.refresh_calls == 1
    assert builder_calls == []
    assert token_path.read_text(encoding="utf-8") == original
    _assert_error_redacted(caught.value, sentinel, "fixture-refresh")
    assert "scripts/youtube_auth.py" in str(caught.value)


def test_transient_refresh_failure_does_not_claim_reauthorization_is_required(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    original = '{"fixture":"old-but-parseable"}'
    token_path.write_text(original, encoding="utf-8")
    sentinel = "transport fixture-access fixture-refresh"
    credentials = _FakeCredentials(
        valid=False,
        expired=True,
        refresh_token="fixture-refresh",
        refresh_error=TimeoutError(sentinel),
    )

    with pytest.raises(YouTubeCredentialError, match="temporarily") as caught:
        load_youtube_client(
            token_path,
            credentials_loader=lambda _path: credentials,
            request_factory=object,
            client_builder=lambda *_args, **_kwargs: None,
        )

    assert credentials.refresh_calls == 1
    assert "scripts/youtube_auth.py" not in str(caught.value)
    _assert_error_redacted(caught.value, sentinel)
    assert token_path.read_text(encoding="utf-8") == original


def test_expired_access_without_refresh_credential_fails_closed(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    original = '{"fixture":"parseable-without-refresh"}'
    token_path.write_text(original, encoding="utf-8")
    credentials = _FakeCredentials(valid=False, expired=True, refresh_token=None)

    with pytest.raises(YouTubeCredentialError, match="refresh credential is missing"):
        load_youtube_client(
            token_path,
            credentials_loader=lambda _path: credentials,
            request_factory=lambda: (_ for _ in ()).throw(AssertionError("must not refresh")),
            client_builder=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("must not build")
            ),
        )

    assert credentials.refresh_calls == 0
    assert token_path.read_text(encoding="utf-8") == original


def test_malformed_token_fails_closed_without_secret_in_error(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    sentinel = "fixture-access fixture-refresh client-secret"
    token_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(YouTubeCredentialError, match="malformed") as caught:
        load_youtube_client(
            token_path,
            credentials_loader=lambda _path: (_ for _ in ()).throw(ValueError(sentinel)),
            client_builder=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("must not build")
            ),
        )

    _assert_error_redacted(caught.value, sentinel, "fixture-access")


def test_transient_token_load_failure_does_not_request_reauthorization(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    token_path.write_text('{"fixture":"parseable"}', encoding="utf-8")
    sentinel = "file busy fixture-access fixture-refresh"

    with pytest.raises(YouTubeCredentialError, match="temporarily") as caught:
        load_youtube_client(
            token_path,
            credentials_loader=lambda _path: (_ for _ in ()).throw(PermissionError(sentinel)),
            client_builder=lambda *_args, **_kwargs: None,
        )

    assert "scripts/youtube_auth.py" not in str(caught.value)
    _assert_error_redacted(caught.value, sentinel)


def test_client_build_failure_is_secret_free_and_retryable(tmp_path):
    token_path = tmp_path / "youtube_token.json"
    token_path.write_text('{"fixture":"parseable"}', encoding="utf-8")
    credentials = _FakeCredentials(valid=True, expired=False, refresh_token="fixture-refresh")
    sentinel = "builder fixture-access fixture-refresh client-secret"

    with pytest.raises(YouTubeCredentialError, match="temporarily") as caught:
        load_youtube_client(
            token_path,
            credentials_loader=lambda _path: credentials,
            client_builder=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
        )

    assert "scripts/youtube_auth.py" not in str(caught.value)
    _assert_error_redacted(caught.value, sentinel)


def test_atomic_persistence_failure_preserves_parseable_token_and_removes_temp(
    tmp_path, monkeypatch
):
    token_path = tmp_path / "youtube_token.json"
    original = '{"fixture":"last-known-parseable"}'
    token_path.write_text(original, encoding="utf-8")
    credentials = _FakeCredentials(
        valid=False,
        expired=True,
        refresh_token="fixture-refresh",
        serialized='{"fixture":"refreshed"}',
    )
    sentinel = "disk failure fixture-refresh client-secret"
    monkeypatch.setattr(
        os, "replace", lambda _source, _target: (_ for _ in ()).throw(OSError(sentinel))
    )

    with pytest.raises(YouTubeCredentialError, match="could not be saved") as caught:
        load_youtube_client(
            token_path,
            credentials_loader=lambda _path: credentials,
            request_factory=object,
            client_builder=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("must not build after persistence failure")
            ),
        )

    assert credentials.refresh_calls == 1
    assert token_path.read_text(encoding="utf-8") == original
    assert [path.name for path in tmp_path.iterdir()] == [token_path.name]
    _assert_error_redacted(caught.value, sentinel)
