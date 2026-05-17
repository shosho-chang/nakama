"""Brook Context Bridge — package KB / Project / style / compliance / RCP
context into a single prompt blob for hand-off to Claude.ai.

This module replaces the LLM-driven conversational composer that previously
lived at ``/brook/chat`` (砍除 per ADR-027 §Decision 8). The bridge does
**not** call any LLM. Its only job is to assemble a packaged prompt the
owner can paste into Claude.ai (which lacks local KB / Project / style /
compliance / RCP knowledge).

Public API:

- ``package_context(...)`` → ``PackagedContext`` dataclass
- ``PackagedContext.summary`` → short labels for the bridge page UI
- ``PackagedContext.prompt`` → the full text blob to copy

Design constraints (ADR-027 §Decision 8):
- Pure text assembly; no ``ask_multi`` / ``Anthropic`` calls.
- Compliance vocab + style profile are *reminders* embedded in the prompt;
  no enforcement, no validation.
- RCP is optional — only loaded when a ``source_slug`` is provided **and**
  the Robin annotation/digest paths exist on disk.
- Project frontmatter is read from ``<vault>/Projects/<slug>.md`` when
  ``project_slug`` is provided; missing project → silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.robin.kb_search import search_kb
from agents.brook.style_profile_loader import (
    available_categories,
    detect_category,
    load_style_profile,
)
from shared.compliance.medical_claim_vocab import (
    ABSOLUTE_ASSERTION_TERMS,
    MEDICAL_CLAIM_TERMS,
)
from shared.log import get_logger

logger = get_logger("nakama.brook.context_bridge")


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class ContextSummary:
    """Short, structured labels shown on the bridge page so the owner can
    eyeball what was packaged before copying."""

    topic: str
    project_slug: str | None = None
    project_loaded: bool = False
    kb_chunk_count: int = 0
    style_profile_id: str | None = None
    rcp_source_slug: str | None = None
    rcp_loaded: bool = False
    compliance_vocab_term_count: int = 0


@dataclass
class PackagedContext:
    """Result of ``package_context`` — what the bridge page renders + copies."""

    summary: ContextSummary
    prompt: str
    sections: list[str] = field(default_factory=list)  # for debug / inspection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_project_frontmatter(vault_path: Path, project_slug: str) -> str | None:
    """Read ``<vault>/Projects/<slug>.md`` and return a frontmatter excerpt.

    Returns ``None`` when the file is missing or unreadable. We deliberately
    do NOT parse YAML strictly — the goal is to surface raw frontmatter +
    first paragraph as ``context``, not to validate schema.
    """
    if not project_slug or "/" in project_slug or "\\" in project_slug:
        return None
    candidate = vault_path / "Projects" / f"{project_slug}.md"
    if not candidate.exists():
        return None
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    # Return up to first 2_000 chars — bridge is for hand-off, not bulk dump.
    return text[:2_000]


def _kb_chunks(query: str, vault_path: Path, *, top_k: int = 5) -> list[dict[str, Any]]:
    """Wrap ``search_kb`` with a soft-fail — bridge must render even when KB
    is unreachable. Defaults to top 5 chunks to keep the packaged prompt
    pasteable into Claude.ai's input box."""
    if not query.strip():
        return []
    try:
        return list(search_kb(query.strip(), vault_path, top_k=top_k))
    except Exception as exc:  # pragma: no cover — defensive only
        logger.warning("bridge KB search failed: %s", exc)
        return []


def _try_load_style_profile(topic: str, category: str | None):
    """Pick a style profile: explicit ``category`` wins; else detect from
    topic; else return ``None`` (style section omitted)."""
    resolved = category or detect_category(topic)
    if resolved is None or resolved not in available_categories():
        return None
    try:
        return load_style_profile(resolved)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("bridge style profile load failed for %s: %s", resolved, exc)
        return None


def _try_load_rcp(vault_path: Path, source_slug: str) -> str | None:
    """Load Robin Reading-Context-Package for ``source_slug`` if annotation
    artifacts exist. Returns a markdown excerpt or ``None``.

    The full builder needs five paths; for the bridge use case we only need a
    short excerpt to seed Claude.ai's context, so we pull the digest excerpt
    + annotations summary directly rather than constructing the full
    ``ReadingContextPackage``.
    """
    if not source_slug or "/" in source_slug or "\\" in source_slug:
        return None
    digest_path = vault_path / "KB" / "Wiki" / "Sources" / source_slug / "digest.md"
    notes_path = vault_path / "KB" / "Wiki" / "Sources" / source_slug / "notes.md"
    annotations_path = vault_path / "KB" / "Annotations" / f"{source_slug}.md"

    chunks: list[str] = []
    for label, p in [
        ("digest", digest_path),
        ("notes", notes_path),
        ("annotations", annotations_path),
    ]:
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            chunks.append(f"### {label} ({p.name})\n\n{text[:1_500]}")
    if not chunks:
        return None
    return "\n\n".join(chunks)


def _compliance_reminder_block() -> tuple[str, int]:
    """Return (markdown reminder block, total term count).

    We don't dump the entire vocab into the prompt — Claude.ai would choke.
    Instead we list a handful of representative category names + the absolute
    assertion list, plus the count so the owner knows the scope.
    """
    term_count = sum(len(v) for v in MEDICAL_CLAIM_TERMS.values()) + len(
        ABSOLUTE_ASSERTION_TERMS
    )
    cat_names = ", ".join(sorted(MEDICAL_CLAIM_TERMS.keys()))
    sample_absolute = ", ".join(ABSOLUTE_ASSERTION_TERMS[:6])
    body = (
        "## 合規提醒（台灣藥事法 / 食安法）\n\n"
        "撰寫時請避免：\n"
        f"- 醫療效能宣稱詞彙類別：{cat_names}\n"
        f"- 絕對化斷言（例如：{sample_absolute} …）\n"
        f"- 完整詞庫共 {term_count} 條，由本地 compliance scanner 維護；"
        "正式發稿時會自動掃。\n"
        "撰文時優先用機制描述（「相關研究顯示…」「在某些族群觀察到…」）取代效能斷言。\n"
    )
    return body, term_count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def package_context(
    *,
    topic: str,
    vault_path: Path,
    project_slug: str | None = None,
    source_slug: str | None = None,
    kb_query: str | None = None,
    category: str | None = None,
) -> PackagedContext:
    """Build a single packaged prompt blob for Claude.ai hand-off.

    Args:
        topic: The article / piece the owner is about to write.
        vault_path: Resolved Obsidian vault root.
        project_slug: Optional ``Projects/<slug>.md`` to include frontmatter.
        source_slug: Optional Robin source slug — if present and annotations
            exist on disk, an RCP excerpt is included.
        kb_query: Optional explicit KB search query; defaults to ``topic``.
        category: Optional style profile category override
            (``book-review`` / ``people`` / ``science``).

    Returns:
        ``PackagedContext`` with both the assembled prompt and a structured
        summary for the bridge page UI.
    """
    summary = ContextSummary(topic=topic.strip(), project_slug=project_slug)
    sections: list[str] = []

    header = (
        "# Claude.ai Hand-off Prompt\n\n"
        f"主題：{topic.strip()}\n\n"
        "以下 context 由 Nakama Brook context bridge 在本地組裝，"
        "Claude.ai 沒有本地 KB / Project / style profile / 合規詞庫；"
        "請把此整段貼到 Claude.ai 對話框後再開始寫作協助。\n"
    )
    sections.append(header)

    # ── Style profile ────────────────────────────────────────────────────
    profile = _try_load_style_profile(topic, category)
    if profile is not None:
        summary.style_profile_id = profile.profile_id
        sections.append(
            "## 風格側寫（style profile）\n\n"
            f"profile_id: `{profile.profile_id}`（category={profile.category}, "
            f"word_count={profile.word_count_min}-{profile.word_count_max}, "
            f"forbid_emoji={profile.forbid_emoji}）\n\n"
            "完整 markdown：\n\n"
            f"{profile.body}\n"
        )

    # ── Compliance vocab reminder ────────────────────────────────────────
    compliance_block, term_count = _compliance_reminder_block()
    summary.compliance_vocab_term_count = term_count
    sections.append(compliance_block)

    # ── Project frontmatter ──────────────────────────────────────────────
    if project_slug:
        project_excerpt = _read_project_frontmatter(vault_path, project_slug)
        if project_excerpt is not None:
            summary.project_loaded = True
            sections.append(
                f"## Project 上下文（Projects/{project_slug}.md 摘錄）\n\n"
                "```markdown\n"
                f"{project_excerpt}\n"
                "```\n"
            )

    # ── KB hybrid search chunks ──────────────────────────────────────────
    query = (kb_query or topic).strip()
    kb_hits = _kb_chunks(query, vault_path)
    summary.kb_chunk_count = len(kb_hits)
    if kb_hits:
        lines = ["## KB 檢索摘要", "", f"_查詢：`{query}`，共 {len(kb_hits)} 段命中_", ""]
        for hit in kb_hits:
            title = hit.get("title") or hit.get("path") or "(untitled)"
            ptype = hit.get("type") or ""
            reason = hit.get("relevance_reason") or hit.get("chunk_text", "")[:200]
            lines.append(f"- **{title}** ({ptype}) — {reason}")
        sections.append("\n".join(lines) + "\n")

    # ── RCP excerpt ──────────────────────────────────────────────────────
    if source_slug:
        summary.rcp_source_slug = source_slug
        rcp_block = _try_load_rcp(vault_path, source_slug)
        if rcp_block is not None:
            summary.rcp_loaded = True
            sections.append(
                f"## Reading-Context-Package 摘錄（source={source_slug}）\n\n"
                f"{rcp_block}\n"
            )

    # ── Closing instruction ──────────────────────────────────────────────
    sections.append(
        "## 給 Claude.ai 的閉幕指令\n\n"
        "請在此 context 下協助修修討論結構、骨架、引用安排；"
        "**不要直接代寫第一人稱完成正文**（紅線 by CONTENT-PIPELINE.md Stage 4，"
        "ADR-027）。若需要建議句型，請以「修修可以這樣寫：…」前綴，"
        "讓修修自己決定是否採用。\n"
    )

    prompt = "\n---\n\n".join(s.rstrip() for s in sections) + "\n"
    return PackagedContext(summary=summary, prompt=prompt, sections=sections)
