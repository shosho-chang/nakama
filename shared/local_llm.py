"""本地 LLM 客戶端 — 透過 OpenAI-compatible API 呼叫 llama.cpp / Ollama。

llama.cpp server 和 Ollama 都提供 OpenAI-compatible `/v1/chat/completions` endpoint，
此模組用 httpx 直接呼叫，不需要額外的 openai SDK 依賴。

用法：
    from shared.local_llm import ask_local, is_server_available

    if is_server_available():
        answer = ask_local("請摘要以下內容...", system="你是知識庫管理員。")
"""

import httpx

from shared.log import get_logger
from shared.retry import with_retry

logger = get_logger("nakama.shared.local_llm")

# 預設值，可透過 config.yaml 或呼叫時覆寫
DEFAULT_BASE_URL = "http://localhost:8080/v1"
DEFAULT_MODEL = "gemma-4-26b-a4b"
DEFAULT_TIMEOUT = 300  # 本地推理可能較慢，5 分鐘 timeout


def _get_config() -> dict:
    """從 config.yaml 讀取 local_llm 設定（若有）。"""
    try:
        from shared.config import load_config

        cfg = load_config()
        return cfg.get("local_llm", {})
    except Exception:
        return {}


def is_server_available(base_url: str | None = None) -> bool:
    """檢查本地 LLM server 是否可連線。

    Args:
        base_url: Server URL，None 則用 config 或預設值

    Returns:
        True 如果 server 回應正常
    """
    cfg = _get_config()
    url = base_url or cfg.get("base_url", DEFAULT_BASE_URL)

    try:
        # llama.cpp: GET /v1/models 回傳可用模型列表
        # Ollama: GET /v1/models 同樣支援
        resp = httpx.get(f"{url}/models", timeout=5)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False


def ask_local(
    prompt: str,
    *,
    system: str = "",
    base_url: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout: int | None = None,
) -> str:
    """送出一次本地 LLM 請求，回傳純文字回應。

    使用 OpenAI-compatible chat/completions API。
    llama.cpp server 和 Ollama 都支援此格式。

    Args:
        prompt:      使用者 prompt
        system:      系統 prompt
        base_url:    Server URL（預設從 config 或 localhost:8080）
        model:       模型名稱（預設從 config 或 gemma-4-26b-a4b）
        max_tokens:  最大回應 token 數
        temperature: 溫度
        timeout:     請求 timeout 秒數

    Returns:
        模型回應的純文字

    Raises:
        ConnectionError: 無法連線到 server
        RuntimeError:    API 回應錯誤
    """
    cfg = _get_config()
    url = base_url or cfg.get("base_url", DEFAULT_BASE_URL)
    model_name = model or cfg.get("model", DEFAULT_MODEL)
    req_timeout = timeout if timeout is not None else cfg.get("timeout", DEFAULT_TIMEOUT)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    def _call() -> str:
        try:
            resp = httpx.post(
                f"{url}/chat/completions",
                json=payload,
                timeout=req_timeout,
            )
        except httpx.ConnectError as e:
            raise ConnectionError(
                f"無法連線到本地 LLM server（{url}）。請確認 llama.cpp / Ollama 已啟動。"
            ) from e

        if resp.status_code != 200:
            raise RuntimeError(f"本地 LLM API 錯誤 {resp.status_code}：{resp.text[:200]}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("本地 LLM 回傳空的 choices")

        return choices[0]["message"]["content"]

    result = with_retry(
        _call,
        max_attempts=3,
        backoff_base=2.0,
        retryable=(ConnectionError, TimeoutError, httpx.TimeoutException, OSError),
    )

    logger.info(f"本地 LLM 回應完成（model={model_name}，{len(result)} 字元）")
    return result
