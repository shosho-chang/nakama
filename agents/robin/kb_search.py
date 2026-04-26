"""Robin KB search — find KB pages relevant to a given query.

`purpose` parameter（ADR-009 Phase 1.5 Slice D.2）讓 caller 在共享 vault scan +
Haiku ranking pipeline 的同時，根據場景換 prompt 框架（YouTube 製作 / SEO audit
internal link / blog 撰稿 / 通用查詢），避免 Haiku 在錯誤上下文排序 KB 結果。
"""

import json
import re
from pathlib import Path
from typing import Literal

from shared.anthropic_client import get_client, set_current_agent
from shared.utils import extract_frontmatter

TOP_K = 8

_SUBDIR_TO_TYPE = {"Sources": "source", "Concepts": "concept", "Entities": "entity"}

Purpose = Literal["youtube", "seo_audit", "blog_compose", "general"]


def _build_purpose_intro(purpose: Purpose, query: str) -> str:
    """Frame `query` for Haiku ranker — same JSON shape, different context.

    每個 purpose 給 Haiku 一段「使用者在做什麼」的開場，影響相關性判斷時的 lens。
    所有變體的後續輸出 schema（JSON array of {index, relevance_reason}）一致，
    讓下游 caller code 不必依 purpose 分支解析。
    """
    if purpose == "youtube":
        return f"使用者正在製作一支 YouTube 影片，主題是：\n「{query}」"
    if purpose == "seo_audit":
        return (
            f"使用者剛跑完一篇部落格文章的 SEO 體檢，正在尋找可以加 internal link "
            f"的 KB 頁面。目標文章主題是：\n「{query}」\n\n"
            f"請優先挑出可佐證內文觀點 / 補充背景概念 / 對應人物實體的頁面；"
            f"YouTube 製作流程相關的內容請排後。"
        )
    if purpose == "blog_compose":
        return (
            f"使用者正在撰寫一篇部落格文章，需要 KB 內既有素材作為引用 / 對照背景。"
            f"文章主題是：\n「{query}」"
        )
    # general — 不假設 use case，純相關性
    return f"使用者想查詢知識庫中與下列主題相關的頁面：\n「{query}」"


def search_kb(
    query: str,
    vault_path: Path,
    top_k: int = TOP_K,
    *,
    purpose: Purpose = "general",
) -> list[dict]:
    """Return KB pages relevant to `query`, ranked by Claude Haiku.

    Scans KB/Wiki/Sources, Concepts, Entities and asks Claude to rank
    by relevance to the given query string. Returns up to `top_k` results,
    each with keys: type, title, path, relevance_reason.

    Args:
        query: free-text query (article topic / focus keyword / video subject).
        vault_path: Obsidian vault root containing `KB/Wiki/{Sources,Concepts,Entities}`.
        top_k: maximum results to return.
        purpose: framing context for the LLM ranker. Defaults to "general"
            (neutral). Pass `"seo_audit"` from `seo-audit-post` skill,
            `"youtube"` from Zoro / Robin video pipeline, `"blog_compose"`
            from Brook compose. Output schema is identical across purposes.
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
            # Normalise type: "Sources" → "source", "Concepts" → "concept",
            # "Entities" → "entity".
            page_type = _SUBDIR_TO_TYPE[subdir]
            pages.append(
                {
                    "type": page_type,
                    "title": title,
                    "path": f"KB/Wiki/{subdir}/{md_file.stem}",
                    "preview": preview,
                }
            )

    if not pages:
        return []

    pages_text = "\n".join(
        f"[{i + 1}] ({p['type']}) {p['title']}: {p['preview']}" for i, p in enumerate(pages)
    )

    intro = _build_purpose_intro(purpose, query)
    prompt = (
        f"{intro}\n\n"
        f"以下是知識庫中的頁面清單：\n{pages_text}\n\n"
        f"請找出最相關的頁面（最多 {top_k} 個），說明相關原因（一句話，繁體中文）。\n"
        "以 JSON 格式回答，格式如下，只列真正相關的頁面：\n"
        '[{"index": 1, "relevance_reason": "..."}]'
    )

    set_current_agent("robin")
    client = get_client()
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
