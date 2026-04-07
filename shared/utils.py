"""通用工具函式。"""

import re
from pathlib import Path


def slugify(text: str) -> str:
    """將標題轉為檔名安全的 slug（保留中文）。

    例如："AI 驅動的 Longevity 策略" → "AI-驅動的-Longevity-策略"
    """
    text = text.strip()
    text = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text


def read_text(path: Path) -> str:
    """讀取文字檔，自動偵測編碼。"""
    for encoding in ("utf-8", "utf-8-sig", "big5", "gb2312"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def extract_frontmatter(content: str) -> tuple[dict, str]:
    """從 Markdown 內容中分離 frontmatter 和 body。"""
    import yaml

    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}

    body = parts[2].strip()
    return fm, body
