"""S0 探針 — annotation_merger Agent SDK 遷移前的三個必答問題。

一次性 spike，不進 CI、不進生產。實測結果記錄在
docs/research/2026-08-18-merger-sdk-spike-findings.md。

計畫見 docs/plans/2026-08-18-annotation-merger-agent-sdk-plan.md 的 S0。

前置（VPS 上跑）：
    /home/nakama/.env 需有 CLAUDE_CODE_OAUTH_TOKEN（訂閱認證）
    claude-agent-sdk 已安裝（bundled CLI 隨附）

跑法：
    python3 scripts/spikes/merger_sdk_probe.py q1    # tool_choice 等價物 + 10 次強制呼叫成功率
    python3 scripts/spikes/merger_sdk_probe.py q2    # Opus 4.7 / 4.8 訂閱可用性
    python3 scripts/spikes/merger_sdk_probe.py q3    # to_thread 內 asyncio.run 穩定性 + 殭屍進程

⚠️ 血淚教訓（findings §操作性發現）：ClaudeAgentOptions **必須**傳
``env={"CLAUDE_CODE_OAUTH_TOKEN": tok, "ANTHROPIC_API_KEY": ""}``。
忘了傳 = 子進程繼承 process env 的 API key、壓過 OAuth（實測優先序），
額度空時每次 2 秒內死於難解的「error result: success」。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import subprocess
import sys
import time

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

_ENV_PATH = "/home/nakama/.env"
_BUNDLED_CLI = "/usr/local/lib/python3.12/dist-packages/claude_agent_sdk/_bundled/claude"


def _auth_env() -> dict[str, str]:
    """訂閱認證覆寫 — S1 會泛化成 shared/agent_sdk.subscription_env()。"""
    tok = None
    for line in open(_ENV_PATH, encoding="utf-8"):
        m = re.match(r"^CLAUDE_CODE_OAUTH_TOKEN=(.+)$", line.strip())
        if m:
            tok = m.group(1).strip()
    assert tok, f"no CLAUDE_CODE_OAUTH_TOKEN in {_ENV_PATH}"
    return {"CLAUDE_CODE_OAUTH_TOKEN": tok, "ANTHROPIC_API_KEY": ""}


# ── Q1 樣本：6 slug + 4 註記（2 明確匹配 / 1 間接 / 1 干擾項）──────────────

_CONCEPTS = [
    "sleep-debt",
    "attention-residue",
    "compound-interest",
    "zone-2-training",
    "identity-based-habits",
    "deep-work",
]

_ANNOTATIONS = """[
  {"text": "作者說睡眠不足會累積成債，週末補眠只能還一部分", "note": "跟我自己輪班經驗吻合，值得寫進睡眠主題"},
  {"text": "每次切換任務都會留下注意力殘留，深度工作需要至少 90 分鐘不中斷", "note": "這解釋了為什麼我開會後寫不了稿"},
  {"text": "本書第三章講到芝加哥學派的價格理論", "note": "跟主題無關，純粹覺得有趣"},
  {"text": "習慣的複利：每天進步 1%，一年後是 37 倍", "note": "這個和投資複利是同一個數學"}
]"""

_PROMPT = (
    "You are a knowledge-base curator. Map the following annotations to the most "
    "relevant concept pages.\n\n"
    "Existing concept slugs:\n" + ", ".join(_CONCEPTS) + "\n\n"
    "Annotations (JSON):\n" + _ANNOTATIONS + "\n\n"
    "For each matched concept, produce a callout block in Traditional Chinese "
    "attributed to the source. Only include concepts with a genuine thematic match.\n\n"
    "You MUST submit your result by calling the merge_annotations tool exactly once. "
    "Do not reply with plain text."
)


async def _q1_one_run(i: int) -> bool:
    box: dict = {}

    @tool("merge_annotations", "Submit the per-concept callout mapping.", {"mapping": dict})
    async def merge_annotations(args: dict) -> dict:
        box["mapping"] = args.get("mapping")
        return {"content": [{"type": "text", "text": "ok"}]}

    server = create_sdk_mcp_server(name="merger", version="0.0.1", tools=[merge_annotations])
    opts = ClaudeAgentOptions(
        model="claude-opus-4-7",
        tools=[],  # 安全紅線（Nami S0-Q1 驗證過的語意）
        setting_sources=[],
        mcp_servers={"merger": server},
        allowed_tools=["mcp__merger__merge_annotations"],
        max_turns=3,
        env=_auth_env(),
    )
    t0 = time.time()
    try:
        async for _ in query(prompt=_PROMPT, options=opts):
            pass
    except Exception as e:  # noqa: BLE001 — spike：紀錄不中斷
        print(f"run {i}: EXC {str(e)[:100]}", flush=True)
    mp = box.get("mapping")
    ok = (
        isinstance(mp, dict)
        and len(mp) > 0
        and all(isinstance(k, str) and isinstance(v, str) for k, v in mp.items())
    )
    keys = sorted(mp.keys()) if isinstance(mp, dict) else None
    print(
        f"run {i}: invoked={'mapping' in box} valid={ok} concepts={keys} "
        f"{time.time() - t0:.0f}s",
        flush=True,
    )
    return ok


async def q1() -> None:
    fields = [f.name for f in dataclasses.fields(ClaudeAgentOptions)]
    print("Q1a — ClaudeAgentOptions fields:", len(fields))
    print(
        "Q1a — tool_choice-like fields:",
        [f for f in fields if "tool" in f.lower() or "choice" in f.lower()],
    )
    results = [await _q1_one_run(i) for i in range(10)]
    print("Q1B SUCCESS RATE:", sum(results), "/ 10")


def q2() -> None:
    env = _auth_env()
    import os

    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    sub_env.update({k: v for k, v in env.items() if v})
    for model in ["claude-opus-4-7", "claude-opus-4-8"]:
        args = [
            _BUNDLED_CLI,
            "--print",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--tools",
            "",
            "--output-format",
            "json",
            "--model",
            model,
        ]
        try:
            p = subprocess.run(
                args,
                input="say OK",
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                env=sub_env,
                cwd="/tmp",
            )
            d = json.loads(p.stdout.strip())
            print(
                f"{model} | rc={p.returncode} | is_error={d.get('is_error')} "
                f"| result={str(d.get('result'))[:80]}"
            )
        except Exception as e:  # noqa: BLE001
            print(f"{model} | EXC: {str(e)[:120]}")


def _count_cli() -> int:
    r = subprocess.run(
        ["pgrep", "-c", "-f", "_bundled/claude"], capture_output=True, text=True
    )
    return int(r.stdout.strip() or 0)


def _q3_sync_merge_like(i: int) -> bool:
    """鏡像 robin.py:699 的形狀：sync fn 內 asyncio.run（route 用 to_thread 呼叫）。"""

    async def inner() -> bool:
        opts = ClaudeAgentOptions(
            model="claude-haiku-4-5",
            tools=[],
            setting_sources=[],
            max_turns=1,
            env=_auth_env(),
        )
        ok = False
        try:
            async for m in query(prompt=f"Reply exactly: RUN_{i}_OK", options=opts):
                if isinstance(m, ResultMessage) and not m.is_error:
                    ok = f"RUN_{i}_OK" in str(m.result)
        except Exception as e:  # noqa: BLE001
            print(f"run {i} EXC: {str(e)[:100]}", flush=True)
        return ok

    return asyncio.run(inner())


async def q3() -> None:
    before = _count_cli()
    fails = 0
    t0 = time.time()
    for i in range(20):
        ok = await asyncio.to_thread(_q3_sync_merge_like, i)
        if not ok:
            fails += 1
    await asyncio.sleep(5)
    after = _count_cli()
    print(
        f"Q3 RESULT: 20 runs, fails={fails}, cli_procs before={before} after={after}, "
        f"{time.time() - t0:.0f}s total"
    )


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "q1":
        asyncio.run(q1())
    elif cmd == "q2":
        q2()
    elif cmd == "q3":
        asyncio.run(q3())
    else:
        sys.exit("usage: merger_sdk_probe.py {q1|q2|q3}")


if __name__ == "__main__":
    main()
