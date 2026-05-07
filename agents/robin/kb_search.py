"""Robin KB search — find KB pages relevant to a given query.

`purpose` parameter（ADR-009 Phase 1.5 Slice D.2）讓 caller 在共享 vault scan +
Haiku ranking pipeline 的同時，根據場景換 prompt 框架（YouTube 製作 / SEO audit
internal link / blog 撰稿 / 通用查詢），避免 Haiku 在錯誤上下文排序 KB 結果。

`engine` parameter（issue #431 Phase 1a）：
  "haiku"  — 既有 LLM ranker 路徑（default，零改動既有 caller）
  "hybrid" — BM25 + dense-vec RRF 路徑（shared.kb_hybrid_search）
"""

import json
import re
from pathlib import Path
from typing import Literal, TypedDict

from shared.kb_indexer import _KB_SUBDIRS
from shared.llm import ask
from shared.llm_context import set_current_agent
from shared.utils import extract_frontmatter

TOP_K = 8

# ADR-021 §2: annotation chunks live under `KB/Annotations/{slug}` (not a Wiki
# subdir). Mapped here so `_hybrid_results` can stamp the right `type` on hits
# whose page_path comes from `_index_annotations` rather than the Wiki walker.
_SUBDIR_TO_TYPE = {
    "Sources": "source",
    "Concepts": "concept",
    "Entities": "entity",
    "Annotations": "annotation",
}

Purpose = Literal["youtube", "seo_audit", "blog_compose", "general", "book_review"]
Engine = Literal["haiku", "hybrid"]


class KBHit(TypedDict, total=False):
    """Wrapper return shape — superset of haiku/hybrid result fields.

    Common keys (always present):
        type, title, path, preview, relevance_reason

    Hybrid-engine-only keys (ADR-021 §2 + Codex amendment #5 — Brook synthesize
    builds evidence cards from these):
        chunk_text, heading, chunk_id, rrf_score
    """

    type: str
    title: str
    path: str
    preview: str
    relevance_reason: str
    chunk_text: str
    heading: str
    chunk_id: int
    rrf_score: float


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
    if purpose == "general":
        return f"使用者想查詢知識庫中與下列主題相關的頁面：\n「{query}」"
    if purpose == "book_review":
        return (
            f"使用者正在讀一本書並寫讀書心得，剛劃線了書中的一段話，請從 KB 中找出可作為對照、"
            f"佐證、或延伸閱讀的頁面 — 優先 PubMed digest / 其他書同主題章節 / 既有 Concept 頁面。"
            f"劃線文字：\n「{query}」"
        )
    raise ValueError(
        f"Unknown purpose: {purpose!r}; expected one of "
        f'("youtube", "seo_audit", "blog_compose", "general", "book_review")'
    )


def _page_type_from_path(path: str) -> str:
    """Map an indexed page path to a `_SUBDIR_TO_TYPE` value.

    Two shapes possible (ADR-021 §2):
      - `KB/Wiki/{Sources|Concepts|Entities}/...`  → split[2] is the subdir
      - `KB/Annotations/{slug}`                    → split[1] is "Annotations"
    """
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "Wiki":
        return _SUBDIR_TO_TYPE.get(parts[2], "concept")
    if len(parts) >= 2 and parts[1] in _SUBDIR_TO_TYPE:
        return _SUBDIR_TO_TYPE[parts[1]]
    return "concept"


def _hybrid_results(query: str, vault_path: Path, top_k: int) -> list[KBHit]:
    """Delegate to shared.kb_hybrid_search and map SearchHit → KBHit dict.

    Preserves chunk-level metadata (chunk_text / heading / chunk_id / rrf_score)
    so Brook synthesize can build evidence cards (ADR-021 §2 + Codex #5). One
    result per chunk — no page-level dedup, because two annotations on the
    same source page are different evidence rows.
    """
    from shared import kb_hybrid_search  # noqa: PLC0415

    hits = kb_hybrid_search.search(query, top_k)
    results: list[KBHit] = []
    for hit in hits:
        results.append(
            KBHit(
                type=_page_type_from_path(hit.path),
                title=hit.page_title,
                path=hit.path,
                preview=hit.chunk_text[:200],
                relevance_reason="",
                chunk_text=hit.chunk_text,
                heading=hit.heading,
                chunk_id=hit.chunk_id,
                rrf_score=hit.rrf_score,
            )
        )
        if len(results) >= top_k:
            break
    return results


def search_kb(
    query: str,
    vault_path: Path,
    top_k: int = TOP_K,
    *,
    purpose: Purpose = "general",
    engine: Engine = "hybrid",
) -> list[KBHit]:
    """Return KB pages relevant to `query`, ranked by the chosen engine.

    Default engine is `hybrid` (ADR-021 §2 + Codex amendment #5): the Haiku
    ranker hits the prompt-token wall on corpora > 50 chunks, and Brook 廣搜
    is always above that threshold. The `haiku` lens is still selectable
    explicitly for small-corpus interactive flows.

    Hybrid hits include chunk-level metadata (`chunk_text` / `heading` /
    `chunk_id` / `rrf_score`) for downstream evidence-card builders; haiku
    hits include only the page-level fields (`type` / `title` / `path` /
    `preview` / `relevance_reason`) — see `KBHit` TypedDict.

    Args:
        query: free-text query (article topic / focus keyword / video subject).
        vault_path: Obsidian vault root containing `KB/Wiki/{Sources,Concepts,Entities}`.
        top_k: maximum results to return.
        purpose: framing context for the LLM ranker. Defaults to "general"
            (neutral). Only used when engine="haiku".
        engine: retrieval engine.
            "hybrid" — BM25 + dense-vec RRF (default, requires indexed kb_index.db).
            "haiku"  — Claude Haiku LLM ranker (legacy lens; small corpora only).
    """
    if engine == "hybrid":
        return _hybrid_results(query, vault_path, top_k)
    wiki_path = vault_path / "KB" / "Wiki"
    pages: list[dict] = []

    # ADR-021 §2 + Codex amendment: recursive `rglob("*.md")` so nested files
    # like `KB/Wiki/Sources/Books/{book_id}/notes.md` get scanned too. Subdirs
    # come from `shared.kb_indexer._KB_SUBDIRS` (single source of truth — no
    # hardcode duplication).
    for subdir in sorted(_KB_SUBDIRS):
        dir_path = wiki_path / subdir
        if not dir_path.exists():
            continue
        for md_file in sorted(dir_path.rglob("*.md")):
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
            rel = md_file.relative_to(wiki_path).with_suffix("").as_posix()
            pages.append(
                {
                    "type": page_type,
                    "title": title,
                    "path": f"KB/Wiki/{rel}",
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
    # Route via shared.llm.ask facade so cost tracking fires through
    # shared.llm_observability.record_call (the previous direct
    # `client.messages.create` skipped cost tracking — see follow-up A3 in
    # project_seo_d2_f_merged_2026_04_26.md).
    text = ask(
        prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
    )
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
