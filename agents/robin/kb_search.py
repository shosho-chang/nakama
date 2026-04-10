"""Robin KB search — find KB pages relevant to a given query."""

import json
import re
from pathlib import Path

from shared.anthropic_client import get_client
from shared.utils import extract_frontmatter

TOP_K = 8


def search_kb(query: str, vault_path: Path, top_k: int = TOP_K) -> list[dict]:
    """Return KB pages relevant to `query`, ranked by Claude Haiku.

    Scans KB/Wiki/Sources, Concepts, Entities and asks Claude to rank
    by relevance to the given query string.  Returns up to `top_k` results,
    each with keys: type, title, path, relevance_reason.
    """
    wiki_path = vault_path / "KB" / "Wiki"
    pages: list[dict] = []

    for subdir in ("Sources", "Concepts", "Entities"):
        dir_path = wiki_path / subdir
        if not dir_path.exists():
            continue
        for md_file in sorted(dir_path.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            fm, body = extract_frontmatter(content)
            title = fm.get("title") or md_file.stem
            preview = (body or "").strip()[:200]
            # Normalise type: "Sources" → "source", "Entities" → "entity"
            page_type = subdir.rstrip("s").lower()
            if page_type == "entit":
                page_type = "entity"
            pages.append({
                "type": page_type,
                "title": title,
                "path": f"KB/Wiki/{subdir}/{md_file.stem}",
                "preview": preview,
            })

    if not pages:
        return []

    pages_text = "\n".join(
        f"[{i + 1}] ({p['type']}) {p['title']}: {p['preview']}"
        for i, p in enumerate(pages)
    )

    prompt = (
        f"使用者正在製作一支 YouTube 影片，主題是：\n「{query}」\n\n"
        f"以下是知識庫中的頁面清單：\n{pages_text}\n\n"
        f"請找出最相關的頁面（最多 {top_k} 個），說明相關原因（一句話，繁體中文）。\n"
        "以 JSON 格式回答，格式如下，只列真正相關的頁面：\n"
        '[{"index": 1, "relevance_reason": "..."}]'
    )

    client = get_client("robin")
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    json_match = re.search(r"\[[\s\S]*\]", text)
    if not json_match:
        return []

    try:
        ranked = json.loads(json_match.group())
    except json.JSONDecodeError:
        return []

    results = []
    for item in ranked:
        idx = item.get("index", 0) - 1
        if 0 <= idx < len(pages):
            result = dict(pages[idx])
            result["relevance_reason"] = item.get("relevance_reason", "")
            results.append(result)

    return results
