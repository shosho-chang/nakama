"""Agent 跨 session 記憶系統（ADR-002 Tier 2: Warm Memory）。

三層記憶架構：
  Tier 1 (Hot)  — CLAUDE.md，永遠載入
  Tier 2 (Warm) — memory/*.md，按需載入（本模組負責）
  Tier 3 (Cold) — SQLite FTS5，搜尋取回（Phase 2）

目錄結構：
    memory/
    ├── shared.md              ← 全員共用背景
    ├── agents/{name}.md       ← 各 agent 的學習記憶
    ├── claude/                ← Claude Code 跨平台記憶
    └── episodic/              ← 事件記錄（Phase 3）

用法：
    from shared.memory import get_context, load_memory, append_memory

    # 新 API：智能載入（推薦）
    ctx = get_context("robin", task="ingest")

    # 舊 API：向下相容
    mem = load_memory("robin")
    append_memory("robin", "學到 X")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, Tuple

# memory/ 目錄放在 nakama 專案根目錄
_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"


# ---------------------------------------------------------------------------
# Frontmatter 解析
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> Tuple[dict, str]:
    """解析 YAML frontmatter，回傳 (metadata_dict, body_str)。

    若無 frontmatter 則 metadata 為空 dict。
    使用簡易 key: value 解析，避免強制依賴 PyYAML。

    >>> meta, body = parse_frontmatter("---\\ntype: semantic\\n---\\n# Title")
    >>> meta["type"]
    'semantic'
    >>> body.strip()
    '# Title'
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        return {}, text

    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip()
            # 解析 YAML list 語法 [a, b, c]
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            meta[key.strip()] = value

    body = text[match.end():]
    return meta, body


# ---------------------------------------------------------------------------
# 路徑解析（支援 memory/agents/ 子目錄，向下相容 memory/{agent}.md）
# ---------------------------------------------------------------------------

def _memory_path(agent: str) -> Path:
    """回傳 agent 記憶檔路徑。

    優先查 memory/agents/{agent}.md，fallback memory/{agent}.md。
    """
    _MEMORY_DIR.mkdir(exist_ok=True)
    agents_dir = _MEMORY_DIR / "agents"
    if agents_dir.is_dir():
        new_path = agents_dir / f"{agent}.md"
        if new_path.exists():
            return new_path
    return _MEMORY_DIR / f"{agent}.md"


def _ensure_agents_dir() -> Path:
    """確保 memory/agents/ 目錄存在並回傳路徑。"""
    agents_dir = _MEMORY_DIR / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    return agents_dir


# ---------------------------------------------------------------------------
# 核心讀寫（舊 API，向下相容）
# ---------------------------------------------------------------------------

def load_memory(agent: str) -> str:
    """讀取 agent 的記憶內容（body only，不含 frontmatter）。

    若無記憶檔則回傳空字串。向下相容舊介面。
    """
    path = _memory_path(agent)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    _meta, body = parse_frontmatter(text)
    return body.strip()


def save_memory(agent: str, content: str) -> None:
    """覆寫 agent 的整份記憶。寫入 memory/agents/{agent}.md。"""
    path = _ensure_agents_dir() / f"{agent}.md"
    path.write_text(content.strip() + "\n", encoding="utf-8")


def append_memory(agent: str, entry: str) -> None:
    """在記憶檔末尾追加一條紀錄（不破壞舊記憶）。"""
    path = _memory_path(agent)
    if not path.exists():
        path = _ensure_agents_dir() / f"{agent}.md"
    existing = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    separator = "\n\n" if existing else ""
    path.write_text(existing + separator + entry.strip() + "\n", encoding="utf-8")


def clear_memory(agent: str) -> None:
    """清除 agent 的記憶（保留空檔）。"""
    path = _memory_path(agent)
    path.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# 智能載入（新 API — ADR-002 Tier 2）
# ---------------------------------------------------------------------------

def _load_raw(name: str) -> str:
    """載入 memory/{name}.md 或 memory/agents/{name}.md 的原始內容。"""
    path = _memory_path(name)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _load_body(name: str) -> str:
    """載入記憶檔的 body（去除 frontmatter）。"""
    raw = _load_raw(name)
    if not raw:
        return ""
    _meta, body = parse_frontmatter(raw)
    return body.strip()


def get_context(agent: str, task: Optional[str] = None, max_tokens: int = 500) -> str:
    """Tier 2 智能載入：合併 shared + agent 記憶，格式化為 system prompt 區塊。

    Args:
        agent:      agent 名稱（如 "robin"、"franky"）
        task:       任務類型（預留給未來 tag 篩選，目前未使用）
        max_tokens: 最大 token 數估算（預留給未來壓縮，目前未使用）

    Returns:
        格式化後的 system prompt 區塊，若無記憶則回傳空字串。
    """
    shared = _load_body("shared")
    agent_mem = _load_body(agent)

    parts: list[str] = []
    if shared:
        parts.append(f"## 共用背景知識\n\n{shared}")
    if agent_mem:
        parts.append(f"## {agent} 的學習記憶\n\n{agent_mem}")

    return "\n\n---\n\n".join(parts)


def memory_as_system_block(agent: str) -> str:
    """將記憶格式化為可注入 system prompt 的區塊。

    若無記憶則回傳空字串，不影響 system prompt。
    向下相容舊介面，內部呼叫 get_context()。
    """
    ctx = get_context(agent)
    if not ctx:
        return ""
    return f"## 過去學到的知識（跨 session 記憶）\n\n{ctx}"
