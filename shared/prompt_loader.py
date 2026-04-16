"""Prompt 載入器：集中管理所有 agent 的 prompt，支援 shared partials 插值。

目錄結構：
    prompts/
    ├── shared/
    │   ├── writing-style.md     # 語言與格式規範（繁中、專有名詞）
    │   ├── domain.md            # 領域知識背景（longevity、wellness）
    │   └── vault-conventions.md # 知識庫頁面規範（頁面類型、連結格式）
    ├── robin/
    │   ├── summarize.md
    │   ├── extract_concepts.md
    │   ├── write_concept.md
    │   └── write_entity.md
    ├── nami/
    │   └── morning_brief.md
    └── zoro/
        └── intel_report.md

用法：
    from shared.prompt_loader import load_prompt

    # 載入 Robin 的 summarize prompt，自動插入 shared partials
    prompt = load_prompt("robin", "summarize", title="...", content="...")

    # 載入 shared partial（直接用）
    style = load_shared("writing-style")
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_shared(name: str) -> str:
    """載入 shared partial（不含 .md 副檔名）。

    Args:
        name: partial 名稱，如 "writing-style"、"domain"

    Returns:
        partial 內容字串，不存在則回傳空字串
    """
    path = _PROMPTS_DIR / "shared" / f"{name}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_prompt(agent: str, name: str, *, content_nature: str = "", **kwargs: str) -> str:
    """載入 agent prompt 並套用變數插值。

    自動將以下 shared partials 注入為可用變數：
        {writing_style}      → prompts/shared/writing-style.md
        {domain}             → prompts/shared/domain.md
        {vault_conventions}  → prompts/shared/vault-conventions.md

    Args:
        agent:           agent 名稱（如 "robin"、"nami"）
        name:            prompt 檔名（不含 .md），如 "summarize"
        content_nature:  內容性質（如 "research"、"textbook"），用於載入類別專屬 prompt。
                         空字串或 "popular_science" 使用預設 prompt。
        **kwargs:        額外的 format 變數

    Returns:
        格式化後的 prompt 字串

    Raises:
        FileNotFoundError: prompt 檔不存在
    """
    # 嘗試載入類別專屬 prompt（若指定且非 default）
    path = None
    if content_nature and content_nature != "popular_science":
        category_path = _PROMPTS_DIR / agent / "categories" / content_nature / f"{name}.md"
        if category_path.exists():
            path = category_path

    # Fallback 到預設 prompt
    if path is None:
        path = _PROMPTS_DIR / agent / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt 不存在：{path}")

    template = path.read_text(encoding="utf-8")

    # 注入 shared partials（優先，讓 kwargs 可覆寫）
    variables = {
        "writing_style": load_shared("writing-style"),
        "domain": load_shared("domain"),
        "vault_conventions": load_shared("vault-conventions"),
    }
    variables.update(kwargs)

    return template.format_map(variables)
