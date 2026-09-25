"""ADR-070 S0 探針：量 U4 / U6 / U7 / U10 還缺的數字。

每個子指令把結果以 JSON 印到 stdout（log 一律導到 stderr），LLM 呼叫能少就少、能小就小。
每個子指令都有 ``--dry-run``：只印「會做什麼」，不起任何子進程、不呼叫 LLM。

    python scripts/llm_lane_probe.py aliases                     # 離線，不呼叫 LLM（U7）
    python scripts/llm_lane_probe.py latency --model haiku --n 5  # n 次 Haiku 一次性呼叫（U4）
    python scripts/llm_lane_probe.py structured --model haiku     # 3 次 structured output（U10）
    python scripts/llm_lane_probe.py openrouter-alias --model haiku  # 1 次，走 OpenRouter（U6）

Lane 的 env 規則：

- 訂閱 lane（latency、structured）：``ClaudeAgentOptions(env=subscription_env())``，並且
  在呼叫期間把 ``ANTHROPIC_AUTH_TOKEN``、``ANTHROPIC_BASE_URL``、``ANTHROPIC_API_KEY``
  和雲端 provider 旗標從本 process 的 ``os.environ`` 拿掉。SDK 會把整份 ``os.environ``
  併進子進程 env，``options.env`` 只能覆寫、不能刪除（SDK 0.2.134
  ``_internal/transport/subprocess_cli.py:791-797``），所以只能從來源拿掉。
  沒設 ``CLAUDE_CODE_OAUTH_TOKEN`` 時，CLI 會退回自己的登入（``~/.claude/.credentials.json``）；
  結果裡的 ``auth_source`` 會寫明是哪一種。
- OpenRouter lane（openrouter-alias）：fail closed。``OPENROUTER_API_KEY`` 沒設就 exit 2；
  子進程 env 不含任何 OAuth token（``CLAUDE_CODE_OAUTH_TOKEN``、``NAMI_SDK_OAUTH_TOKEN``
  都拿掉），``CLAUDE_CONFIG_DIR`` 指到一個空的暫存目錄，讓 CLI 也看不到本機登入檔。
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import math
import mmap
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import aclosing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from shared.agent_sdk import (  # noqa: E402
    describe_sdk_message,
    log_sdk_exception,
    log_sdk_message,
    sdk_message_kind,
    subscription_env,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api"
DEFAULT_TIMEOUT_S = 120.0

_OAUTH_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "NAMI_SDK_OAUTH_TOKEN")
_PROVIDER_FLAG_KEYS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
# 會把 CLI 帶離訂閱 lane 的 env（ADR-070 F4、D2 第 1 項）
SUBSCRIPTION_STRIP_KEYS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    *_PROVIDER_FLAG_KEYS,
)
OPENROUTER_STRIP_KEYS = (*_OAUTH_KEYS, *_PROVIDER_FLAG_KEYS)
# 會改變別名解析結果的 env：只回報有沒有設，不動它
_ALIAS_OVERRIDE_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
# 值可以明文印出的 key（不是憑證）
_PLAIN_VALUE_KEYS = frozenset({"ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"})

LATENCY_PROMPT = "Reply with exactly one word: ok"
STRUCTURED_PROMPT = "What is 2 + 3? Put the number in `answer` and its English word in `word`."
SCHEMA_DRAFT_07: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {"answer": {"type": "integer"}, "word": {"type": "string"}},
    "required": ["answer", "word"],
    "additionalProperties": False,
}
SCHEMA_2020_12: dict[str, Any] = {
    **SCHEMA_DRAFT_07,
    "$schema": "https://json-schema.org/draft/2020-12/schema",
}
# (label, schema, max_turns)：先 max_turns=1，再 max_turns=3，最後 2020-12 宣告一次
STRUCTURED_ATTEMPTS: tuple[tuple[str, dict[str, Any], int], ...] = (
    ("draft-07", SCHEMA_DRAFT_07, 1),
    ("draft-07", SCHEMA_DRAFT_07, 3),
    ("2020-12", SCHEMA_2020_12, 3),
)

_STDERR_MAX_LINES = 50
_TEXT_LIMIT = 2000


class ProbeRefused(Exception):
    """前置條件不成立，拒絕執行（不呼叫任何東西）。"""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


# ── env 規劃 ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class EnvPlan:
    lane: str
    remove: tuple[str, ...]  # 呼叫期間從 os.environ 拿掉的 key
    options_env: dict[str, str]  # ClaudeAgentOptions(env=...)
    auth_source: str
    config_dir_isolated: bool = False

    def child_env(self, environ: Mapping[str, str]) -> dict[str, str]:
        """模擬 SDK 組子進程 env 的方式（subprocess_cli.py:791-797）。"""
        base = {k: v for k, v in environ.items() if k not in self.remove and k != "CLAUDECODE"}
        return {**base, **self.options_env}

    def describe(self, environ: Mapping[str, str]) -> dict[str, Any]:
        """給 dry-run / 結果用的描述；憑證只標 ``<redacted>``。"""
        return {
            "lane": self.lane,
            "auth_source": self.auth_source,
            "removed_from_inherited_env": [k for k in self.remove if k in environ],
            "options_env": {
                k: (v if k in _PLAIN_VALUE_KEYS or not v else "<redacted>")
                for k, v in self.options_env.items()
            },
            "config_dir_isolated": self.config_dir_isolated,
            "alias_override_env_present": [k for k in _ALIAS_OVERRIDE_KEYS if environ.get(k)],
        }


def _claude_config_dir() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(config_dir) if config_dir else Path.home() / ".claude"


def build_subscription_plan(environ: Mapping[str, str]) -> EnvPlan:
    options_env = subscription_env()
    if options_env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        auth_source = "env:CLAUDE_CODE_OAUTH_TOKEN"
    else:
        exists = (_claude_config_dir() / ".credentials.json").is_file()
        auth_source = (
            "cli_login (CLAUDE_CODE_OAUTH_TOKEN 未設；CLI 讀自己的登入，"
            f".credentials.json {'存在' if exists else '不存在'})"
        )
    return EnvPlan(
        lane="subscription",
        remove=SUBSCRIPTION_STRIP_KEYS,
        options_env=options_env,
        auth_source=auth_source,
    )


def oauth_token_values(environ: Mapping[str, str]) -> set[str]:
    return {environ[k] for k in _OAUTH_KEYS if environ.get(k)}


def assert_no_oauth_leak(
    plan: EnvPlan, environ: Mapping[str, str], oauth_values: set[str] | None = None
) -> None:
    """OpenRouter lane 的 fail-closed 檢查：子進程 env 裡不准出現任何 OAuth token。

    ``oauth_values``：已經從 os.environ 拿掉 OAuth key 之後再檢查時，傳入先前記下的值，
    確保「值」的比對不會因為 key 已被拿掉而變成空檢查。
    """
    child = plan.child_env(environ)
    for key in _OAUTH_KEYS:
        if child.get(key):
            raise ProbeRefused(f"fail closed：子進程 env 仍含 {key}", code=3)
    if oauth_values is None:
        oauth_values = oauth_token_values(environ)
    leaked = sorted(k for k, v in child.items() if v and v in oauth_values)
    if leaked:
        raise ProbeRefused(f"fail closed：子進程 env 的 {leaked} 的值等於 OAuth token", code=3)
    token = child.get("ANTHROPIC_AUTH_TOKEN", "")
    if token.startswith("sk-ant-oat"):
        raise ProbeRefused("fail closed：ANTHROPIC_AUTH_TOKEN 看起來是 Claude OAuth token", code=3)
    if child.get("ANTHROPIC_BASE_URL") != OPENROUTER_BASE_URL:
        raise ProbeRefused("fail closed：ANTHROPIC_BASE_URL 不是 OpenRouter", code=3)
    if child.get("ANTHROPIC_API_KEY", "") != "":
        raise ProbeRefused("fail closed：ANTHROPIC_API_KEY 沒有清空", code=3)


def build_openrouter_plan(environ: Mapping[str, str], *, config_dir: str | None = None) -> EnvPlan:
    key = (environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise ProbeRefused(
            "OPENROUTER_API_KEY 沒設：openrouter-alias 一律 fail closed，不會改用其他憑證。"
            "請在有 OPENROUTER_API_KEY 的機器（VPS）上跑。",
            code=2,
        )
    options_env = {
        "ANTHROPIC_BASE_URL": OPENROUTER_BASE_URL,
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_API_KEY": "",
    }
    if config_dir is not None:
        options_env["CLAUDE_CONFIG_DIR"] = config_dir
    plan = EnvPlan(
        lane="openrouter",
        remove=OPENROUTER_STRIP_KEYS,
        options_env=options_env,
        auth_source="env:OPENROUTER_API_KEY",
        config_dir_isolated=config_dir is not None,
    )
    assert_no_oauth_leak(plan, environ)
    return plan


@contextmanager
def scrubbed_environ(keys: tuple[str, ...]) -> Iterator[list[str]]:
    """呼叫期間把 ``keys`` 從 os.environ 拿掉，結束後原樣放回。"""
    saved = {k: os.environ.pop(k) for k in keys if k in os.environ}
    try:
        yield sorted(saved)
    finally:
        os.environ.update(saved)


# ── SDK / CLI 資訊與別名表（離線）────────────────────────────────────────


def sdk_info() -> dict[str, Any]:
    try:
        import claude_agent_sdk as sdk  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"installed": False, "error": f"{type(e).__name__}: {e}"}
    pkg_dir = Path(sdk.__file__).resolve().parent
    cli_name = "claude.exe" if platform.system() == "Windows" else "claude"
    bundled = pkg_dir / "_bundled" / cli_name
    try:
        from claude_agent_sdk._cli_version import __cli_version__ as cli_const  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        cli_const = None
    return {
        "installed": True,
        "sdk_version": getattr(sdk, "__version__", None),
        "sdk_path": str(pkg_dir),
        "bundled_cli": str(bundled),
        "bundled_cli_exists": bundled.is_file(),
        "bundled_cli_version_constant": cli_const,
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def run_cli_version(cli_path: str) -> dict[str, Any]:
    """``<cli> --version``：不呼叫 LLM。"""
    try:
        proc = subprocess.run(
            [cli_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip()[:_TEXT_LIMIT],
    }


_JS_KEY_RE = re.compile(r"([{,])\s*([A-Za-z_$][\w$]*)\s*:")
_LATEST_RE = re.compile(rb"latest_per_family:\{")
_ALIAS_WINDOW = 16384


def _balanced_object(data: Any, start: int, limit: int = 65536) -> bytes | None:
    """從 ``data[start] == '{'`` 起取出對稱的 ``{...}``（略過字串裡的括號）。"""
    chunk = bytes(data[start : start + limit])
    depth = 0
    in_str = False
    escaped = False
    for i, c in enumerate(chunk):
        if in_str:
            if escaped:
                escaped = False
            elif c == 0x5C:  # backslash
                escaped = True
            elif c == 0x22:  # "
                in_str = False
            continue
        if c == 0x22:
            in_str = True
        elif c == 0x7B:  # {
            depth += 1
        elif c == 0x7D:  # }
            depth -= 1
            if depth == 0:
                return chunk[: i + 1]
    return None


def js_object_to_python(text: str) -> Any:
    """把 ``{opus:"claude-opus-5",...}`` 這種 JS 物件字面值轉成 dict（key 補上引號）。"""
    return json.loads(_JS_KEY_RE.sub(r'\1"\2":', text))


def _parse_object_at(data: Any, brace_index: int) -> Any:
    raw = _balanced_object(data, brace_index)
    if raw is None:
        return None
    try:
        return js_object_to_python(raw.decode("utf-8", "replace"))
    except ValueError:
        return None


def parse_alias_tables(data: Any) -> dict[str, Any]:
    """在 CLI binary（bytes 或 mmap）裡找 ``latest_per_family:{...}``，取非空的那一個。

    同一段還有 ``aliases:{...}``（含 ``per_provider``）與 ``best:"..."``，一併回傳。
    """
    candidates: list[tuple[int, dict[str, str]]] = []
    empty_matches = 0
    for m in _LATEST_RE.finditer(data):
        parsed = _parse_object_at(data, m.end() - 1)
        if (
            isinstance(parsed, dict)
            and parsed
            and all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items())
        ):
            candidates.append((m.start(), parsed))
        else:
            empty_matches += 1
    result: dict[str, Any] = {
        "latest_per_family": None,
        "aliases": None,
        "best": None,
        "non_empty_matches": len(candidates),
        "other_matches": empty_matches,
    }
    if not candidates:
        return result
    pos, latest = candidates[0]
    result["latest_per_family"] = latest
    if len(candidates) > 1:
        result["other_non_empty"] = [c for _, c in candidates[1:]]
    window_start = max(0, pos - _ALIAS_WINDOW)
    window = bytes(data[window_start:pos])
    alias_at = window.rfind(b"aliases:{")
    if alias_at >= 0:
        aliases = _parse_object_at(window, alias_at + len(b"aliases:"))
        if isinstance(aliases, dict):
            result["aliases"] = aliases
    best = re.findall(rb'best:"([^"]+)"', window[alias_at if alias_at >= 0 else 0 :])
    if best:
        result["best"] = best[-1].decode("utf-8", "replace")
    return result


# ── 一次 SDK 呼叫 ───────────────────────────────────────────────────────


def _make_options(**kwargs: Any) -> tuple[Any, list[str]]:
    """組 ``ClaudeAgentOptions``；舊版 SDK 沒有的欄位丟掉並回報。"""
    from claude_agent_sdk import ClaudeAgentOptions  # noqa: PLC0415

    known = {f.name for f in dataclasses.fields(ClaudeAgentOptions)}
    dropped = sorted(k for k in kwargs if k not in known)
    return ClaudeAgentOptions(**{k: v for k, v in kwargs.items() if k in known}), dropped


def _truncate(value: Any, limit: int = _TEXT_LIMIT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…[truncated {len(value) - limit} chars]"
    return value


def new_call_record() -> dict[str, Any]:
    return {
        "ok": False,
        "wall_s": None,
        "first_result_s": None,
        "models": [],
        "result": None,
        "extra_results": 0,
        "rate_limit_events": [],
        "assistant_errors": [],
        "exception": None,
        "stderr_tail": [],
        "dropped_options": [],
    }


def collect_message(rec: dict[str, Any], msg: Any, elapsed_s: float) -> None:
    """把一個 stream message 的證據收進 ``rec``（純函式，測試用假物件即可）。"""
    kind = sdk_message_kind(msg)
    if kind == "AssistantMessage":
        model = getattr(msg, "model", None)
        if isinstance(model, str) and model and model not in rec["models"]:
            rec["models"].append(model)
    described = describe_sdk_message(msg)
    if described is None:
        return
    event, fields = described
    if event == "sdk_rate_limit":
        rec["rate_limit_events"].append(fields)
    elif event == "sdk_assistant_error":
        rec["assistant_errors"].append(fields)
    elif kind == "ResultMessage":
        if rec["result"] is not None:
            rec["extra_results"] += 1
            return
        rec["first_result_s"] = round(elapsed_s, 3)
        structured = getattr(msg, "structured_output", None)
        rec["result"] = {
            **fields,
            "event": event,
            "duration_api_ms": getattr(msg, "duration_api_ms", None),
            "model_usage": getattr(msg, "model_usage", None),
            "structured_output": structured,
            "structured_output_populated": structured is not None,
            "result_text": _truncate(getattr(msg, "result", None), 500),
        }
        rec["ok"] = event == "sdk_result_ok"


async def one_call(
    *,
    site: str,
    prompt: str,
    model: str,
    plan: EnvPlan,
    max_turns: int,
    timeout_s: float,
    output_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一次一次性 SDK 呼叫：``tools=[]``、``setting_sources=[]``。"""
    import anyio  # noqa: PLC0415
    from claude_agent_sdk import query  # noqa: PLC0415

    rec = new_call_record()
    stderr_lines: list[str] = rec["stderr_tail"]

    def _on_stderr(line: str) -> None:
        if len(stderr_lines) < _STDERR_MAX_LINES:
            stderr_lines.append(line[:500])

    kwargs: dict[str, Any] = {
        "model": model,
        "tools": [],
        "setting_sources": [],
        "max_turns": max_turns,
        "env": dict(plan.options_env),
        "stderr": _on_stderr,
    }
    if output_format is not None:
        kwargs["output_format"] = output_format
    options, rec["dropped_options"] = _make_options(**kwargs)
    if "output_format" in rec["dropped_options"]:
        rec["exception"] = {"type": "Unsupported", "text": "此版 SDK 沒有 output_format"}
        return rec

    t0 = time.perf_counter()
    try:
        with anyio.fail_after(timeout_s):
            async with aclosing(query(prompt=prompt, options=options)) as stream:
                async for msg in stream:
                    log_sdk_message(site, msg)
                    collect_message(rec, msg, time.perf_counter() - t0)
    except Exception as e:  # noqa: BLE001 — 探針要把失敗原文收進結果
        log_sdk_exception(site, e)
        rec["ok"] = False
        rec["exception"] = {"type": type(e).__name__, "text": _truncate(str(e))}
    rec["wall_s"] = round(time.perf_counter() - t0, 3)
    return rec


# ── RSS 取樣 ────────────────────────────────────────────────────────────


def psutil_unavailable_reason() -> str | None:
    try:
        import psutil  # noqa: F401, PLC0415
    except Exception as e:  # noqa: BLE001
        return f"psutil 無法 import：{type(e).__name__}: {e}"
    return None


class RssSampler:
    """背景 thread 每 ``interval`` 秒量一次本 process 所有子孫進程的 RSS。"""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.unavailable_reason = psutil_unavailable_reason()
        self._psutil: Any = None
        if self.unavailable_reason is None:
            import psutil  # noqa: PLC0415

            self._psutil = psutil
        self.peak_single_bytes = 0
        self.peak_single_name: str | None = None
        self.peak_total_bytes = 0
        self.samples = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample_once(self) -> None:
        psutil = self._psutil
        total = 0
        for child in psutil.Process().children(recursive=True):
            try:
                rss = child.memory_info().rss
                name = child.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            total += rss
            if rss > self.peak_single_bytes:
                self.peak_single_bytes = rss
                self.peak_single_name = name
        self.peak_total_bytes = max(self.peak_total_bytes, total)
        self.samples += 1

    def _run(self) -> None:
        while True:
            try:
                self._sample_once()
            except Exception as e:  # noqa: BLE001
                self.unavailable_reason = f"取樣失敗：{type(e).__name__}: {e}"
                return
            if self._stop.wait(self.interval):
                return

    def __enter__(self) -> RssSampler:
        if self._psutil is not None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def result(self) -> dict[str, Any] | None:
        if self._psutil is None:
            return None
        return {
            "peak_single_process_bytes": self.peak_single_bytes,
            "peak_single_process_mb": round(self.peak_single_bytes / 2**20, 1),
            "peak_single_process_name": self.peak_single_name,
            "peak_descendants_total_bytes": self.peak_total_bytes,
            "peak_descendants_total_mb": round(self.peak_total_bytes / 2**20, 1),
            "samples": self.samples,
            "interval_s": self.interval,
            "error": self.unavailable_reason,
        }


# ── 統計 ────────────────────────────────────────────────────────────────


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile（n 小時 p95 就是最大值）。"""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _stats(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "min": min(values),
        "max": max(values),
        "method": "nearest-rank",
    }


# ── 子指令 ──────────────────────────────────────────────────────────────


def _one_call_plan(model: str, max_turns: int, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "tools": [],
        "setting_sources": [],
        "max_turns": max_turns,
        **extra,
    }


def _header(subcommand: str, *, dry_run: bool) -> dict[str, Any]:
    return {
        "subcommand": subcommand,
        "dry_run": dry_run,
        "ts": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "sdk": sdk_info(),
    }


def cmd_aliases(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    out = _header("aliases", dry_run=args.dry_run)
    cli_path = args.cli_path or out["sdk"].get("bundled_cli")
    out["cli_path"] = cli_path
    out["nakama_claude_cli_env"] = os.environ.get("NAKAMA_CLAUDE_CLI")
    out["llm_calls"] = 0
    if not cli_path or not Path(cli_path).is_file():
        out["error"] = f"找不到 CLI binary：{cli_path}"
        return out, 1
    if args.dry_run:
        out["would"] = [
            f"執行 `{cli_path} --version`（不呼叫 LLM）",
            f"以 mmap 唯讀掃描 {cli_path}（{Path(cli_path).stat().st_size} bytes）"
            "找 latest_per_family:{...}",
        ]
        return out, 0
    out["cli_version"] = run_cli_version(cli_path)
    with open(cli_path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        out["binary_bytes"] = len(mm)
        out.update(parse_alias_tables(mm))
    return out, 0 if out["latest_per_family"] else 1


def cmd_latency(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    out = _header("latency", dry_run=args.dry_run)
    plan = build_subscription_plan(os.environ)
    out["env_plan"] = plan.describe(os.environ)
    out["prompt"] = LATENCY_PROMPT
    out["llm_calls"] = args.n
    out["call"] = _one_call_plan(args.model, 1)
    out["rss_null_reason"] = psutil_unavailable_reason()
    if args.dry_run:
        out["would"] = f"依序做 {args.n} 次一次性呼叫，每次 asyncio.run 一次（同正式呼叫點）"
        return out, 0
    calls: list[dict[str, Any]] = []
    with scrubbed_environ(plan.remove):
        for i in range(args.n):
            with RssSampler() as sampler:
                rec = asyncio.run(
                    one_call(
                        site="probe.latency",
                        prompt=LATENCY_PROMPT,
                        model=args.model,
                        plan=plan,
                        max_turns=1,
                        timeout_s=args.timeout,
                    )
                )
            rec["i"] = i + 1
            rec["rss"] = sampler.result()
            calls.append(rec)
    ok = [c for c in calls if c["ok"]]
    rss = [c["rss"] for c in calls if c["rss"]]
    out["calls"] = calls
    out["summary"] = {
        "n": len(calls),
        "ok": len(ok),
        "wall_s": _stats([c["wall_s"] for c in ok]),
        "first_result_s": _stats([c["first_result_s"] for c in ok if c["first_result_s"]]),
        "models": sorted({m for c in calls for m in c["models"]}),
        "total_cost_usd": [c["result"].get("total_cost_usd") for c in calls if c["result"]],
        "rate_limit_events": [e for c in calls for e in c["rate_limit_events"]],
        "peak_rss_single_process_mb": max((r["peak_single_process_mb"] for r in rss), default=None),
        "peak_rss_descendants_total_mb": max(
            (r["peak_descendants_total_mb"] for r in rss), default=None
        ),
        "rss_null_reason": out["rss_null_reason"],
    }
    return out, 0 if ok else 1


def cmd_structured(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    out = _header("structured", dry_run=args.dry_run)
    plan = build_subscription_plan(os.environ)
    out["env_plan"] = plan.describe(os.environ)
    out["prompt"] = STRUCTURED_PROMPT
    out["llm_calls"] = len(STRUCTURED_ATTEMPTS)
    out["calls_planned"] = [
        _one_call_plan(
            args.model,
            max_turns,
            schema_label=label,
            output_format={"type": "json_schema", "schema": schema},
        )
        for label, schema, max_turns in STRUCTURED_ATTEMPTS
    ]
    if args.dry_run:
        return out, 0
    attempts: list[dict[str, Any]] = []
    with scrubbed_environ(plan.remove):
        for label, schema, max_turns in STRUCTURED_ATTEMPTS:
            rec = asyncio.run(
                one_call(
                    site="probe.structured",
                    prompt=STRUCTURED_PROMPT,
                    model=args.model,
                    plan=plan,
                    max_turns=max_turns,
                    timeout_s=args.timeout,
                    output_format={"type": "json_schema", "schema": schema},
                )
            )
            result = rec["result"] or {}
            rec["schema_label"] = label
            rec["max_turns"] = max_turns
            rec["structured_output_populated"] = bool(result.get("structured_output_populated"))
            rec["num_turns"] = result.get("num_turns")
            rec["rejected"] = rec["exception"] is not None or result.get("event") != "sdk_result_ok"
            attempts.append(rec)
    out["attempts"] = attempts
    return out, 0


def cmd_openrouter_alias(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    out = _header("openrouter-alias", dry_run=args.dry_run)
    if args.dry_run:
        plan = build_openrouter_plan(os.environ, config_dir="<新建的空暫存目錄>")
        out["env_plan"] = plan.describe(os.environ)
        out["llm_calls"] = 1
        out["call"] = _one_call_plan(args.model, 1)
        out["prompt"] = LATENCY_PROMPT
        return out, 0
    with tempfile.TemporaryDirectory(
        prefix="nakama-probe-claude-config-", ignore_cleanup_errors=True
    ) as config_dir:
        plan = build_openrouter_plan(os.environ, config_dir=config_dir)
        out["env_plan"] = plan.describe(os.environ)
        out["llm_calls"] = 1
        oauth_values = oauth_token_values(os.environ)
        with scrubbed_environ(plan.remove):
            assert_no_oauth_leak(plan, os.environ, oauth_values)  # spawn 前再檢查一次
            rec = asyncio.run(
                one_call(
                    site="probe.openrouter_alias",
                    prompt=LATENCY_PROMPT,
                    model=args.model,
                    plan=plan,
                    max_turns=1,
                    timeout_s=args.timeout,
                )
            )
    out["requested_model"] = args.model
    out["call"] = rec
    out["ok"] = rec["ok"]
    out["models"] = rec["models"]
    out["error_text"] = _openrouter_error_text(rec)
    return out, 0 if rec["ok"] else 1


def _openrouter_error_text(rec: dict[str, Any]) -> str | None:
    parts: list[str] = []
    if rec["exception"]:
        parts.append(f"exception: {rec['exception']['text']}")
    for err in rec["assistant_errors"]:
        parts.append(f"assistant.error={err.get('error')}: {err.get('text')}")
    result = rec["result"]
    if result and result.get("event") == "sdk_result_error":
        parts.append(f"result.errors={result.get('errors')} result={result.get('result')}")
    return "\n".join(parts) or None


# ── 進入點 ──────────────────────────────────────────────────────────────


def _load_env() -> None:
    """跟正式程式一樣從 repo 的 .env 補環境變數（不覆寫已經設的）。"""
    from shared.config import load_config  # noqa: PLC0415

    load_config()


def _route_logs_to_stderr() -> None:
    """stdout 只放 JSON 結果：把 nakama logger 印到 stdout 的 handler 改到 stderr。"""
    from shared.log import get_logger  # noqa: PLC0415

    get_logger("nakama.llm_lane")  # 確保 handler 已掛上
    for handler in logging.getLogger("nakama").handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.setStream(sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm_lane_probe", description="ADR-070 S0 探針（結果以 JSON 印到 stdout）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("aliases", help="離線讀內附 CLI 的版本與別名表（不呼叫 LLM）")
    p.add_argument("--cli-path", default=None, help="要掃描的 CLI binary（預設 SDK 內附的）")
    p.add_argument("--dry-run", action="store_true")

    for name, help_text in (
        ("latency", "n 次一次性呼叫的延遲、實際 model、cost、CLI 子進程 RSS（U4）"),
        ("structured", "structured output：max_turns 1 / 3、2020-12 schema（U10）"),
        ("openrouter-alias", "用 OpenRouter env 跑一次別名，看認不認得（U6）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--model", default="haiku")
        p.add_argument(
            "--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="每次呼叫的秒數上限"
        )
        p.add_argument("--dry-run", action="store_true")
        if name == "latency":
            p.add_argument("--n", type=int, default=5)
    return parser


_COMMANDS = {
    "aliases": cmd_aliases,
    "latency": cmd_latency,
    "structured": cmd_structured,
    "openrouter-alias": cmd_openrouter_alias,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "n", 1) < 1:
        print("--n 至少要 1", file=sys.stderr)
        return 2
    _load_env()
    _route_logs_to_stderr()
    try:
        payload, code = _COMMANDS[args.cmd](args)
    except ProbeRefused as e:
        print(f"[llm_lane_probe] 拒絕執行：{e}", file=sys.stderr)
        return e.code
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
