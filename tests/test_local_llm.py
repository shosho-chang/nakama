"""Tests for shared.local_llm — 本地 LLM OpenAI-compatible 客戶端。"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from shared.local_llm import ask_local, is_server_available

# ── is_server_available ──────────────────────────────────────────────────────


class TestIsServerAvailable:
    @patch("shared.local_llm.httpx.get")
    def test_available(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        assert is_server_available("http://localhost:8080/v1") is True

    @patch("shared.local_llm.httpx.get")
    def test_connection_refused(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("refused")
        assert is_server_available("http://localhost:8080/v1") is False

    @patch("shared.local_llm.httpx.get")
    def test_timeout(self, mock_get):
        mock_get.side_effect = httpx.TimeoutException("timeout")
        assert is_server_available("http://localhost:8080/v1") is False


# ── ask_local ────────────────────────────────────────────────────────────────


class TestAskLocal:
    @patch("shared.local_llm.httpx.post")
    def test_basic_request(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={"choices": [{"message": {"content": "Hello from local LLM"}}]}
            ),
        )

        result = ask_local(
            "你好",
            base_url="http://localhost:8080/v1",
            model="test-model",
        )

        assert result == "Hello from local LLM"
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["model"] == "test-model"
        assert payload["messages"] == [{"role": "user", "content": "你好"}]

    @patch("shared.local_llm.httpx.post")
    def test_with_system_prompt(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": [{"message": {"content": "OK"}}]}),
        )

        ask_local(
            "summarize this",
            system="You are a helpful assistant.",
            base_url="http://localhost:8080/v1",
        )

        payload = mock_post.call_args[1]["json"]
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "You are a helpful assistant."

    @patch("shared.local_llm.httpx.post")
    def test_connection_error_raises(self, mock_post):
        mock_post.side_effect = httpx.ConnectError("refused")

        with pytest.raises(ConnectionError, match="無法連線"):
            ask_local(
                "test",
                base_url="http://localhost:9999/v1",
            )

    @patch("shared.local_llm.httpx.post")
    def test_api_error_raises_runtime_error(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=500,
            text="Internal Server Error",
        )

        with pytest.raises(RuntimeError, match="API 錯誤 500"):
            ask_local("test", base_url="http://localhost:8080/v1")

    @patch("shared.local_llm.httpx.post")
    def test_empty_choices_raises(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": []}),
        )

        with pytest.raises(RuntimeError, match="空的 choices"):
            ask_local("test", base_url="http://localhost:8080/v1")

    @patch("shared.local_llm.httpx.post")
    def test_temperature_and_max_tokens_passed(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": [{"message": {"content": "ok"}}]}),
        )

        ask_local(
            "test",
            base_url="http://localhost:8080/v1",
            temperature=0.7,
            max_tokens=2048,
        )

        payload = mock_post.call_args[1]["json"]
        assert payload["temperature"] == 0.7
        assert payload["max_tokens"] == 2048
        assert payload["stream"] is False
