"""Agent 跨 session 記憶系統。

每個 agent 有一個 memory/<name>.md 檔，格式為 YAML frontmatter + 自由文字。
記憶會在每次 agent 執行時注入 system prompt，讓 agent「記得」過去學到的東西。

用法：
    from shared.memory import load_memory, save_memory, append_memory

    # 讀取記憶（用於注入 system prompt）
    mem = load_memory("nami")

    # 覆寫整份記憶（agent 自行決定要記住什麼）
    save_memory("nami", "使用者偏好簡短的 brief，重點放在行動項目而非背景說明。")

    # 追加一條記憶（不覆寫舊的）
    append_memory("robin", "2026-04-10：學到 longevity 概念頁應拆分「機制」與「干預」兩個子概念。")
"""

from pathlib import Path

# memory/ 目錄放在 nakama 專案根目錄
_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"


def _memory_path(agent: str) -> Path:
    _MEMORY_DIR.mkdir(exist_ok=True)
    return _MEMORY_DIR / f"{agent}.md"


def load_memory(agent: str) -> str:
    """讀取 agent 的記憶內容。若無記憶檔則回傳空字串。"""
    path = _memory_path(agent)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_memory(agent: str, content: str) -> None:
    """覆寫 agent 的整份記憶。"""
    path = _memory_path(agent)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def append_memory(agent: str, entry: str) -> None:
    """在記憶檔末尾追加一條紀錄（不破壞舊記憶）。"""
    path = _memory_path(agent)
    existing = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    separator = "\n\n" if existing else ""
    path.write_text(existing + separator + entry.strip() + "\n", encoding="utf-8")


def clear_memory(agent: str) -> None:
    """清除 agent 的記憶（保留空檔）。"""
    path = _memory_path(agent)
    path.write_text("", encoding="utf-8")


def memory_as_system_block(agent: str) -> str:
    """將記憶格式化為可注入 system prompt 的區塊。

    若無記憶則回傳空字串，不影響 system prompt。
    """
    mem = load_memory(agent)
    if not mem:
        return ""
    return f"## 過去學到的知識（跨 session 記憶）\n\n{mem}"
