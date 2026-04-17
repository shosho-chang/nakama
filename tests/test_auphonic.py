"""shared/auphonic.py 單元測試。

所有 HTTP 呼叫和 subprocess 都用 mock，不需要真實 API key 或 ffmpeg。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.auphonic import (
    _find_available_account,
    _get_audio_duration,
    _load_accounts,
    _load_env_defaults,
    _trim_jingle,
    normalize,
)


@pytest.fixture(autouse=True)
def clear_auphonic_env(monkeypatch):
    """避免開發機 .env 的 AUPHONIC_ACCOUNT_* 污染 mock 測試。"""
    for i in range(1, 6):
        monkeypatch.delenv(f"AUPHONIC_ACCOUNT_{i}", raising=False)


# ── 帳號載入 ──


def test_load_accounts_success(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "a@test.com,key1")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "b@test.com,key2")
    accounts = _load_accounts()
    assert len(accounts) == 2
    assert accounts[0].email == "a@test.com"
    assert accounts[0].api_key == "key1"
    assert accounts[1].email == "b@test.com"


def test_load_accounts_with_spaces(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", " a@test.com , key1 ")
    accounts = _load_accounts()
    assert accounts[0].email == "a@test.com"
    assert accounts[0].api_key == "key1"


def test_load_accounts_skip_empty(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "a@test.com,key1")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_3", "c@test.com,key3")
    accounts = _load_accounts()
    assert len(accounts) == 2


def test_load_accounts_skip_malformed(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "no_comma_here")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "ok@test.com,key2")
    accounts = _load_accounts()
    assert len(accounts) == 1
    assert accounts[0].email == "ok@test.com"


def test_load_accounts_none_set(monkeypatch):
    for i in range(1, 6):
        monkeypatch.delenv(f"AUPHONIC_ACCOUNT_{i}", raising=False)
    with pytest.raises(ValueError, match="未設定"):
        _load_accounts()


# ── .env 參數讀取 ──


def test_load_env_defaults_uses_defaults(monkeypatch):
    """未設定任何環境變數時，使用程式碼預設值。"""
    for key in [
        "AUPHONIC_LOUDNESS",
        "AUPHONIC_DENOISE",
        "AUPHONIC_DENOISE_METHOD",
        "AUPHONIC_TRIM_JINGLE",
    ]:
        monkeypatch.delenv(key, raising=False)

    defaults = _load_env_defaults()
    assert defaults["loudness_target"] == -16.0
    assert defaults["denoise"] is True
    assert defaults["denoise_method"] == "dynamic"
    assert defaults["trim_jingle"] is True


def test_load_env_defaults_reads_env(monkeypatch):
    monkeypatch.setenv("AUPHONIC_LOUDNESS", "-14")
    monkeypatch.setenv("AUPHONIC_DENOISE", "false")
    monkeypatch.setenv("AUPHONIC_DENOISE_METHOD", "speech_isolation")
    monkeypatch.setenv("AUPHONIC_TRIM_JINGLE", "false")

    defaults = _load_env_defaults()
    assert defaults["loudness_target"] == -14.0
    assert defaults["denoise"] is False
    assert defaults["denoise_method"] == "speech_isolation"
    assert defaults["trim_jingle"] is False


# ── Audio Duration ──


def test_get_audio_duration():
    ffprobe_output = json.dumps({"format": {"duration": "123.456"}})
    mock_result = MagicMock(stdout=ffprobe_output)

    with patch("shared.auphonic.subprocess.run", return_value=mock_result) as mock_run:
        duration = _get_audio_duration(Path("/fake/audio.mp3"))

    assert duration == pytest.approx(123.456)
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][0] == "ffprobe"


# ── Find Available Account ──


def test_find_available_account_first_has_credits(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "a@test.com,key1")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "b@test.com,key2")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"credits": 2.0}}
    mock_resp.raise_for_status = MagicMock()

    with patch("shared.auphonic.httpx.get", return_value=mock_resp):
        account = _find_available_account(3600)

    assert account.email == "a@test.com"
    assert account.api_key == "key1"


def test_find_available_account_fallback_to_second(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "a@test.com,key1")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "b@test.com,key2")

    responses = [
        {"data": {"credits": 0.01}},  # key1: 不夠
        {"data": {"credits": 2.0}},  # key2: 夠
    ]
    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        resp = MagicMock()
        resp.json.return_value = responses[call_count]
        resp.raise_for_status = MagicMock()
        call_count += 1
        return resp

    with patch("shared.auphonic.httpx.get", side_effect=mock_get):
        account = _find_available_account(3600)

    assert account.email == "b@test.com"


def test_find_available_account_all_empty(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "a@test.com,key1")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "b@test.com,key2")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"credits": 0.0}}
    mock_resp.raise_for_status = MagicMock()

    with patch("shared.auphonic.httpx.get", return_value=mock_resp):
        with pytest.raises(ValueError, match="餘額不足"):
            _find_available_account(3600)


def test_find_available_account_api_error_skips(monkeypatch):
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "bad@test.com,bad_key")
    monkeypatch.setenv("AUPHONIC_ACCOUNT_2", "good@test.com,good_key")

    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("API error")
        resp = MagicMock()
        resp.json.return_value = {"data": {"credits": 2.0}}
        resp.raise_for_status = MagicMock()
        return resp

    with patch("shared.auphonic.httpx.get", side_effect=mock_get):
        account = _find_available_account(60)

    assert account.email == "good@test.com"


# ── Trim Jingle ──


def test_trim_jingle():
    audio_path = Path("/fake/audio.wav")

    with (
        patch("shared.auphonic._get_audio_duration", return_value=120.0),
        patch("shared.auphonic.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = _trim_jingle(audio_path, 6.0)

    assert result.stem == "audio_trimmed"
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd[0]
    ss_idx = cmd.index("-ss")
    assert cmd[ss_idx + 1] == "6.0"
    to_idx = cmd.index("-to")
    assert cmd[to_idx + 1] == "114.0"


def test_trim_jingle_audio_too_short():
    audio_path = Path("/fake/short.wav")

    with patch("shared.auphonic._get_audio_duration", return_value=10.0):
        result = _trim_jingle(audio_path, 6.0)

    assert result == audio_path


# ── normalize 整合測試（全 mock）──


def test_normalize_full_flow(tmp_path, monkeypatch):
    """測試 normalize 的完整流程（所有外部呼叫都 mock）。"""
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "test@test.com,test_key")

    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    from shared.auphonic import AuphonicAccount

    mock_account = AuphonicAccount(email="test@test.com", api_key="test_key")

    with (
        patch("shared.auphonic._get_audio_duration", return_value=300.0),
        patch("shared.auphonic._find_available_account", return_value=mock_account),
        patch("shared.auphonic._create_production", return_value="uuid-123"),
        patch("shared.auphonic._upload_file"),
        patch(
            "shared.auphonic._start_and_wait",
            return_value={
                "uuid": "uuid-123",
                "status": 3,
                "output_files": [{"format": "wav", "ending": ".wav"}],
                "output_basename": "test",
            },
        ),
        patch("shared.auphonic._download_result") as mock_download,
        patch("shared.auphonic._trim_jingle") as mock_trim,
    ):
        normalized = tmp_path / "test_normalized.wav"
        mock_download.return_value = normalized
        normalized.write_bytes(b"normalized audio")

        trimmed = tmp_path / "test_normalized_trimmed.wav"
        mock_trim.return_value = trimmed
        trimmed.write_bytes(b"trimmed audio")

        result = normalize(str(audio), output_dir=str(tmp_path))

    assert result == trimmed
    mock_trim.assert_called_once()


def test_normalize_skip_jingle_trim(tmp_path, monkeypatch):
    """trim_jingle=False 覆寫時不裁切。"""
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "test@test.com,test_key")

    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    from shared.auphonic import AuphonicAccount

    mock_account = AuphonicAccount(email="test@test.com", api_key="test_key")

    with (
        patch("shared.auphonic._get_audio_duration", return_value=300.0),
        patch("shared.auphonic._find_available_account", return_value=mock_account),
        patch("shared.auphonic._create_production", return_value="uuid-123"),
        patch("shared.auphonic._upload_file"),
        patch(
            "shared.auphonic._start_and_wait",
            return_value={
                "uuid": "uuid-123",
                "status": 3,
                "output_files": [{"format": "wav", "ending": ".wav"}],
                "output_basename": "test",
            },
        ),
        patch("shared.auphonic._download_result") as mock_download,
        patch("shared.auphonic._trim_jingle") as mock_trim,
    ):
        normalized = tmp_path / "test_normalized.wav"
        mock_download.return_value = normalized
        normalized.write_bytes(b"normalized audio")

        result = normalize(str(audio), output_dir=str(tmp_path), trim_jingle=False)

    mock_trim.assert_not_called()
    assert result == normalized


def test_normalize_override_params(tmp_path, monkeypatch):
    """keyword args 可覆寫 .env 預設值。"""
    monkeypatch.setenv("AUPHONIC_ACCOUNT_1", "test@test.com,test_key")
    monkeypatch.setenv("AUPHONIC_LOUDNESS", "-16")

    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    from shared.auphonic import AuphonicAccount

    mock_account = AuphonicAccount(email="test@test.com", api_key="test_key")

    with (
        patch("shared.auphonic._get_audio_duration", return_value=300.0),
        patch("shared.auphonic._find_available_account", return_value=mock_account),
        patch("shared.auphonic._create_production", return_value="uuid-123") as mock_create,
        patch("shared.auphonic._upload_file"),
        patch(
            "shared.auphonic._start_and_wait",
            return_value={
                "uuid": "uuid-123",
                "status": 3,
                "output_files": [{"format": "wav", "ending": ".wav"}],
                "output_basename": "test",
            },
        ),
        patch("shared.auphonic._download_result") as mock_download,
        patch("shared.auphonic._trim_jingle") as mock_trim,
    ):
        normalized = tmp_path / "test_normalized.wav"
        mock_download.return_value = normalized
        normalized.write_bytes(b"normalized audio")
        mock_trim.return_value = normalized

        normalize(str(audio), output_dir=str(tmp_path), loudness_target=-14)

    # 確認傳給 _create_production 的 params 用了覆寫值
    call_kwargs = mock_create.call_args
    assert call_kwargs.kwargs["params"]["loudness_target"] == -14


def test_normalize_file_not_found():
    with pytest.raises(FileNotFoundError, match="音檔不存在"):
        normalize("/nonexistent/audio.mp3")
