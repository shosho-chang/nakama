"""把語意工作交給**當下正在跑的 agent**，而不是另外開一個供應商的子行程。

`_composition.build_production_application` 早就留了 `process_runner` 參數，但
`run_finished_cut_production.py` 從來不傳，於是永遠落到 `SubprocessCodexProcessRunner`
——不管當下是誰在跑，Director / DP / visual_review 都會去開 Codex。修修講過很多次：
語意工作由當下執行的 agent 自己做，code 寫死供應商是 bug 不是指示
（`memory/claude/feedback_semantic_work_runs_on_host_agent.md`）。

這支 runner 實作同一個 `CodexProcessRunner` protocol，但不 spawn 任何東西：它把
packet 攤在一個**持久**目錄裡，然後停下來等 `response.json`。等到了就把內容寫進
adapter 指定的 `--output-last-message` 路徑，回傳 exit 0——對 adapter 而言與子行程
完全沒有差別，所以 proposal 照樣會過 visual_review、素材建置與整條驗收，也照樣留在
`runs/semantic-dispatch/` 帳本裡。

**為什麼是阻塞式**：adapter 的 workspace 是 `TemporaryDirectory`，`run()` 一回傳就
連同 schema 與輸出路徑一起消失。要非阻塞就得另外造一套 request/resume 狀態機；在
「agent 本人就在現場」這個情境裡，停下來等它把答案寫出來是最小且不失真的做法。
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ._codex_semantic import CodexProcessResult

_RESPONSE_NAME = "response.json"
_PROMPT_NAME = "prompt.md"
_SCHEMA_NAME = "schema.json"


class SemanticHandoffError(RuntimeError):
    """交接目錄無法建立或 packet 缺欄位——停，不要假裝 worker 回答過。"""


def _flag_value(argv: tuple[str, ...], flag: str) -> Path | None:
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            return Path(argv[index + 1])
    return None


@dataclass(frozen=True, slots=True)
class HandoffPaths:
    """交接目錄裡那三個檔——announce 出去給人（或 agent）照著做。"""

    directory: Path
    prompt: Path
    schema: Path
    response: Path


class AgentHandoffProcessRunner:
    """Stop and let the running agent answer the semantic packet.

    介面與 `SubprocessCodexProcessRunner` 相同，因此 `CodexSemanticAdapter` 不需要
    知道自己被誰服務。
    """

    def __init__(
        self,
        handoff_root: Path,
        *,
        poll_sec: float = 2.0,
        announce: Callable[[HandoffPaths], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = Path(handoff_root)
        self._poll_sec = poll_sec
        self._announce = announce or _default_announce
        self._sleep = sleep
        self._now = now

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        prompt: str,
        timeout_sec: float,
    ) -> CodexProcessResult:
        output_path = _flag_value(argv, "--output-last-message")
        if output_path is None:
            raise SemanticHandoffError("packet 少了 --output-last-message，無從交回答案")
        schema_path = _flag_value(argv, "--output-schema")

        directory = self._root / _handoff_name(cwd, fallback=output_path.parent.name)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / _PROMPT_NAME).write_text(prompt, encoding="utf-8")
            if schema_path is not None and schema_path.is_file():
                (directory / _SCHEMA_NAME).write_text(
                    schema_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            # prompt 第一句就是「Read packet.json」，而 workspace 是 TemporaryDirectory：
            # 不整份帶過來，接手的 agent 讀到一半就沒東西可讀了。**目錄也要帶**——
            # visual_review 的預覽畫格放在 workspace 的 media/ 子目錄，只複製檔案的話
            # 審查員手上只剩一份 asset_ref，看不到畫面就只能照文字猜，正是 2026-09-09
            # 一整天在追的那個病根（4:18 側躺素材、看不懂的中央圖都是這樣過關的）。
            for source in sorted(cwd.iterdir()):
                if source.name in {_PROMPT_NAME, _SCHEMA_NAME, _RESPONSE_NAME}:
                    continue
                if source.is_dir():
                    shutil.copytree(source, directory / source.name, dirs_exist_ok=True)
                elif source.is_file():
                    (directory / source.name).write_bytes(source.read_bytes())
        except OSError as error:
            raise SemanticHandoffError(f"無法建立交接目錄 {directory}：{error}") from error

        paths = HandoffPaths(
            directory=directory,
            prompt=directory / _PROMPT_NAME,
            schema=directory / _SCHEMA_NAME,
            response=directory / _RESPONSE_NAME,
        )
        self._announce(paths)

        deadline = self._now() + timeout_sec
        while True:
            if paths.response.is_file():
                try:
                    payload = paths.response.read_text(encoding="utf-8")
                    json.loads(payload)  # 壞 JSON 現在就擋，不要讓 adapter 收到半個字
                except (OSError, ValueError) as error:
                    return CodexProcessResult(
                        returncode=1,
                        stderr=f"{paths.response} 不是合法 JSON：{error}",
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(payload, encoding="utf-8")
                return CodexProcessResult(returncode=0, stdout=payload)
            if self._now() >= deadline:
                return CodexProcessResult(
                    returncode=None,
                    timed_out=True,
                    stderr=(
                        f"等不到 {paths.response}（{timeout_sec:.0f}s）。"
                        "packet 還在，補上 response.json 再重跑同一個 command。"
                    ),
                )
            self._sleep(self._poll_sec)


def _handoff_name(cwd: Path, *, fallback: str) -> str:
    """用 request_id 當目錄名——temp workspace 的亂碼看不出這是哪一次交接。"""
    packet = cwd / "packet.json"
    try:
        request_id = json.loads(packet.read_text(encoding="utf-8"))["request"]["request_id"]
    except (OSError, ValueError, KeyError, TypeError):
        return fallback
    return request_id if isinstance(request_id, str) and request_id.strip() else fallback


def _default_announce(paths: HandoffPaths) -> None:
    # stderr：stdout 是 CLI 的 JSON 結果，混進去會讓呼叫端解析失敗。
    import sys

    print(
        "\n".join(
            (
                "",
                "=== 語意工作交接給當下的 agent ===",
                f"  prompt   : {paths.prompt}",
                f"  schema   : {paths.schema}",
                f"  回答寫到 : {paths.response}",
                "  （寫完就會自動往下跑；逾時的話 packet 留著，補完重跑同一個 command）",
                "",
            )
        ),
        file=sys.stderr,
        flush=True,
    )
