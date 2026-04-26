"""KB Wiki 寫入統一入口（ADR-011 textbook ingest v2 §3.5）。

統一 textbook-ingest skill / kb-ingest skill / Robin agent 三條 ingest 路徑：
- Concept page = cross-source aggregator（不寫 `## 更新` body append）
- update_merge 走 LLM diff-merge into main body
- update_conflict 結構化寫進 `## 文獻分歧 / Discussion`
- 既有 v1 schema page 第一次被 update 時 lazy migrate 成 v2

落點：
- Concept: `KB/Wiki/Concepts/{slug}.md`
- Entity:  `KB/Wiki/Entities/{slug}.md`
- Chapter source: `KB/Wiki/Sources/Books/{book_id}/ch{n}.md`
- Book entity: `KB/Wiki/Entities/Books/{book_id}.md`
- Backup: `{repo_root}/data/kb_backup/{slug}-{utc-ts}.md`（retain 24h）

LLM call 走 `shared.llm.ask(model="claude-opus-4-7")` — ingest 強制 Opus 4.7
（ADR-011 §2 P2 LLM-readable deep extract）。測試環境 monkeypatch `_ask_llm`。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import yaml

from shared.config import get_vault_path
from shared.log import get_logger
from shared.schemas.kb import (
    ChapterSourcePageV2,
    ConflictBlock,
    FigureRef,
    MigrationReport,
)

logger = get_logger("nakama.kb_writer")

_REPO_ROOT = Path(__file__).resolve().parent.parent

# 路徑常數
KB_CONCEPTS_DIR = "KB/Wiki/Concepts"
KB_ENTITIES_DIR = "KB/Wiki/Entities"
KB_BOOK_SOURCES_DIR = "KB/Wiki/Sources/Books"
KB_BOOK_ENTITIES_DIR = "KB/Wiki/Entities/Books"

# Body 結構常數
DISCUSSION_HEADING = "## 文獻分歧 / Discussion"
H2_ORDER = (
    "## Definition",
    "## Core Principles",
    "## Sub-concepts",
    "## Field-level Controversies",
    "## 文獻分歧 / Discussion",
    "## Practical Applications",
    "## Related Concepts",
    "## Sources",
)
PLACEHOLDER = "_(尚無內容)_"

# v1 → v2 migration 要丟掉的 v1-only 欄位
_V1_ONLY_FIELDS = frozenset({"status", "related_pages"})

# Backup retention: 24h
BACKUP_RETENTION = timedelta(hours=24)

# Slug / book_id sanitization — block path traversal via LLM-emitted strings
# (CJK + alphanumerics + underscore + dash; first char must be a word/CJK char
# so empty-but-truthy values like "-" are rejected). The class deliberately
# excludes `.` `/` `\` `..` so attackers can't coax a slug like
# `../../../tmp/poc` into `_concept_abs_path` or `_backup_path`.
_SAFE_SLUG_RE = re.compile(r"^[\w一-鿿][\w\-一-鿿]*$")


def _validate_slug(value: str, *, kind: str = "slug") -> None:
    """Reject path-traversal-shaped strings before they hit `Path` interpolation.

    Used at every public write entry that takes an LLM-influenced identifier
    (concept slug / book_id). `Path` does not collapse `..` at construction
    time, so without this guard a slug `../../../tmp/poc` lets
    `upsert_concept_page` write outside the vault.
    """
    if not isinstance(value, str) or not _SAFE_SLUG_RE.fullmatch(value):
        raise ValueError(f"unsafe {kind}: {value!r}")


# LLM diff-merge prompt（給 update_merge action 用；ADR-011 §3.3 Step 5）
_DIFF_MERGE_PROMPT = """你是知識庫 aggregator。
下方有既有 concept page body 與一段新 source 的 extract，請把新內容 merge 進主體段落
（Definition / Core Principles / Sub-concepts / Practical Applications），
保留既有所有事實聲明，僅 enrich 不破壞。

【既有 body】
{existing_body}

【新 source extract（來源：{source_link}）】
{new_extract}

【規則】
1. 必須保留所有 H2 結構：## Definition / ## Core Principles / ## Sub-concepts /
   ## Field-level Controversies / ## 文獻分歧 / Discussion / ## Practical Applications /
   ## Related Concepts / ## Sources
2. 既有事實聲明全保留；新內容用 enrich 方式插入相應 section
   （如新 source 給了新機轉，加進 Core Principles）
3. 不寫 imperative todo（「應新增 X」），直接寫實際內容
4. 不重複既有 statement；遇到完全重複的句子保留既有版
5. 若某 H2 既有為 `_(尚無內容)_` 而新 source 提供內容，把 placeholder 替換成內容
6. ## Sources 末尾 append 一行 `- {source_link}`（若不存在）
7. ## 文獻分歧 / Discussion 內既有 topic 全保留，不在此 prompt 處理新 conflict

只回 merged body markdown，不含 frontmatter，不含其他文字。"""


# ---------------------------------------------------------------------------
# LLM 邊界（測試時 monkeypatch 這個 function）
# ---------------------------------------------------------------------------


def _ask_llm(prompt: str, *, system: str = "", max_tokens: int = 16000) -> str:
    """Diff-merge / 內部 LLM call 邊界。預設走 Opus 4.7。

    為什麼包一層：unit test monkeypatch 這個 function 即可，不必動 shared.llm.ask。
    """
    from shared.llm import ask

    return ask(
        prompt=prompt,
        system=system,
        model="claude-opus-4-7",
        max_tokens=max_tokens,
        temperature=0.2,
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _concept_rel_path(slug: str) -> str:
    return f"{KB_CONCEPTS_DIR}/{slug}.md"


def _concept_abs_path(slug: str) -> Path:
    return get_vault_path() / _concept_rel_path(slug)


def _backup_dir() -> Path:
    return _REPO_ROOT / "data" / "kb_backup"


def _backup_path(slug: str, ts: datetime) -> Path:
    stamp = ts.strftime("%Y-%m-%dT%H%M%SZ")
    return _backup_dir() / f"{slug}-{stamp}.md"


# ---------------------------------------------------------------------------
# I/O primitives
# ---------------------------------------------------------------------------


def _load_page(abs_path: Path) -> tuple[dict, str] | None:
    """Read a vault page → (frontmatter dict, body str). None if not exists."""
    if not abs_path.exists():
        return None
    raw = abs_path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return ({}, raw)
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return ({}, raw)
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as e:
        logger.warning(f"YAML parse failed for {abs_path}: {e}")
        return ({}, raw)
    body = parts[2].lstrip("\n")
    return (fm, body)


def _serialize_page(fm: dict, body: str) -> str:
    """Render frontmatter dict + body → full markdown page string.

    Uses width=10**9 to prevent yaml fold corruption on long unicode filenames
    (the bug that broke 4 vault pages — PR #164 fix applied here too).
    """
    fm_str = yaml.dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10**9,
    ).strip()
    body_clean = body.rstrip("\n")
    return f"---\n{fm_str}\n---\n\n{body_clean}\n"


def _write_page_file(abs_path: Path, fm: dict, body: str) -> None:
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(_serialize_page(fm, body), encoding="utf-8")


# ---------------------------------------------------------------------------
# Backup mechanism (retain 24h)
# ---------------------------------------------------------------------------


def _sweep_old_backups(now: datetime | None = None) -> int:
    """Delete backups older than BACKUP_RETENTION. Returns count deleted."""
    if now is None:
        now = datetime.now(timezone.utc)
    bdir = _backup_dir()
    if not bdir.exists():
        return 0
    cutoff = now - BACKUP_RETENTION
    deleted = 0
    for f in bdir.glob("*.md"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def _backup_concept(slug: str, content: str, now: datetime | None = None) -> Path:
    """Snapshot the current concept page bytes to data/kb_backup/. Sweep old backups."""
    if now is None:
        now = datetime.now(timezone.utc)
    bdir = _backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    bpath = _backup_path(slug, now)
    bpath.write_text(content, encoding="utf-8")
    _sweep_old_backups(now)
    return bpath


# ---------------------------------------------------------------------------
# v1 → v2 migration helpers
# ---------------------------------------------------------------------------


def _path_to_wikilink(path_str: str) -> str:
    """Convert a vault-relative path to a `[[stem]]` wikilink.

    "Sources/foo.md" → "[[foo]]"  (matches existing Robin convention; see
    agents/robin/ingest.py:504 `來源：[[{source_stem}]]`)
    """
    stem = Path(path_str).stem
    return f"[[{stem}]]"


def _v1_to_v2_in_memory(fm: dict, body: str) -> tuple[dict, str, list[str]]:
    """Translate a v1 frontmatter dict into v2 schema (in-memory; no write).

    Returns (v2_fm_dict, body, change_log_lines).

    Migration rules (per ADR-011 §3.5.1 migrate_v1_to_v2):
    - schema_version: 1 → 2
    - source_refs (path list) → derive mentioned_in (wikilink list); KEEP source_refs
      for backward compatibility (Robin v1 readers may still expect it)
    - aliases default []
    - discussion_topics default []
    - drop v1-only fields: status, related_pages
    - body `## 更新（date）` block 暫不在此 in-memory pass 處理（需 LLM call；由
      upsert_concept_page 在實際寫入時 LLM diff-merge 一次性處理）
    """
    v2_fm: dict = {}
    changes: list[str] = []

    if fm.get("schema_version") == 2:
        return (dict(fm), body, [])

    # 必填 fields 映射
    v2_fm["schema_version"] = 2
    v2_fm["title"] = fm.get("title", "")
    v2_fm["type"] = "concept"
    v2_fm["domain"] = fm.get("domain", "general")

    # aliases
    aliases = fm.get("aliases") or []
    v2_fm["aliases"] = list(aliases) if isinstance(aliases, list) else []

    # mentioned_in (derive from source_refs if missing)
    mentioned = fm.get("mentioned_in") or []
    if isinstance(mentioned, list) and mentioned:
        v2_fm["mentioned_in"] = list(mentioned)
    else:
        source_refs = fm.get("source_refs") or []
        if isinstance(source_refs, list):
            v2_fm["mentioned_in"] = [_path_to_wikilink(p) for p in source_refs]
        else:
            v2_fm["mentioned_in"] = []
        if v2_fm["mentioned_in"]:
            n = len(v2_fm["mentioned_in"])
            changes.append(f"+ mentioned_in derived from source_refs ({n} entries)")

    # source_refs preserved verbatim (transition compat)
    source_refs = fm.get("source_refs") or []
    v2_fm["source_refs"] = list(source_refs) if isinstance(source_refs, list) else []

    # discussion_topics
    topics = fm.get("discussion_topics") or []
    v2_fm["discussion_topics"] = list(topics) if isinstance(topics, list) else []

    # confidence: v1 had string ("medium"); v2 needs float | None.
    # Exclude bool from int/float branch (bool subclasses int in Python; we
    # don't want `confidence: true` to silently become 1.0). Log unknown
    # strings explicitly so the dry-run migration report shows the drop.
    conf = fm.get("confidence")
    if isinstance(conf, bool):
        v2_fm["confidence"] = None
        changes.append(f"! confidence: bool {conf!r} dropped to None")
    elif isinstance(conf, (int, float)):
        v2_fm["confidence"] = float(conf)
    elif isinstance(conf, str):
        mapped = {"low": 0.3, "medium": 0.6, "high": 0.9}.get(conf.lower())
        v2_fm["confidence"] = mapped
        if mapped is not None:
            changes.append(f"~ confidence: '{conf}' → {mapped}")
        else:
            changes.append(f"! confidence: unknown string {conf!r} dropped to None")
    else:
        v2_fm["confidence"] = None

    # tags
    tags = fm.get("tags") or []
    v2_fm["tags"] = list(tags) if isinstance(tags, list) else []

    # dates
    today = date.today()
    v2_fm["created"] = fm.get("created") or today
    v2_fm["updated"] = fm.get("updated") or today

    # Track dropped v1-only fields
    dropped = [k for k in fm.keys() if k in _V1_ONLY_FIELDS]
    if dropped:
        changes.append(f"- dropped v1-only fields: {', '.join(dropped)}")

    if fm.get("schema_version") != 2:
        changes.append(f"~ schema_version: {fm.get('schema_version')} → 2")

    return (v2_fm, body, changes)


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------


def read_concept_for_diff(slug: str) -> dict | None:
    """Read existing concept page (lazy v1→v2 in-memory migrate; no write).

    Returns dict with keys:
        frontmatter: dict (v2 schema shape)
        body: str
    Or None if page does not exist.

    用於 §3.3 Step 4 把既有 body 注入 extract_concepts prompt。
    """
    abs_path = _concept_abs_path(slug)
    loaded = _load_page(abs_path)
    if loaded is None:
        return None
    fm, body = loaded
    v2_fm, v2_body, _changes = _v1_to_v2_in_memory(fm, body)
    return {"frontmatter": v2_fm, "body": v2_body}


def list_existing_concepts() -> dict[str, dict]:
    """Scan KB/Wiki/Concepts/ → {slug: {frontmatter, body}} (lazy v1→v2 in-memory)."""
    cdir = get_vault_path() / KB_CONCEPTS_DIR
    if not cdir.exists():
        return {}
    out: dict[str, dict] = {}
    for f in sorted(cdir.glob("*.md")):
        slug = f.stem
        loaded = _load_page(f)
        if loaded is None:
            continue
        fm, body = loaded
        v2_fm, v2_body, _ = _v1_to_v2_in_memory(fm, body)
        out[slug] = {"frontmatter": v2_fm, "body": v2_body}
    return out


# ---------------------------------------------------------------------------
# Body manipulation helpers
# ---------------------------------------------------------------------------


def _ensure_h2_skeleton(body: str) -> str:
    """Ensure all H2_ORDER headings exist in body (fill missing with placeholder).

    Idempotent: existing canonical sections are preserved verbatim, and any
    non-canonical H2 sections (user-added `## Methods`, zh-prefixed
    `## 定義（Definition）` from earlier Robin output, or future v3
    sections) are appended after the canonical block — never silently
    dropped. P1 「enrich, not destroy」.
    """
    sections = _split_h2_sections(body)
    rebuilt: list[str] = []
    canonical = set(H2_ORDER)
    for h2 in H2_ORDER:
        content = sections.get(h2, PLACEHOLDER)
        rebuilt.append(f"{h2}\n\n{content.strip()}\n")
    # Forward-compat: keep any leftover H2 (user-added or future schema) after
    # the canonical block. Iterate in document order (Python 3.7+ dict).
    for h2, content in sections.items():
        if h2 == "__prefix__" or h2 in canonical:
            continue
        if h2.startswith("## "):
            rebuilt.append(f"{h2}\n\n{content.strip() or PLACEHOLDER}\n")
    # Preserve any non-H2 prefix (e.g. `# Title`)
    prefix = sections.get("__prefix__", "")
    if prefix:
        return f"{prefix.strip()}\n\n" + "\n".join(rebuilt)
    return "\n".join(rebuilt)


def _split_h2_sections(body: str) -> dict[str, str]:
    """Split body into {h2_heading: content} dict. `__prefix__` for pre-first-H2 text."""
    lines = body.split("\n")
    sections: dict[str, list[str]] = {}
    current: str = "__prefix__"
    sections[current] = []
    for line in lines:
        if line.startswith("## "):
            current = line.strip()
            sections.setdefault(current, [])
        else:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _append_to_section(body: str, heading: str, new_block: str) -> str:
    """Append `new_block` (str) under `heading` (e.g. "## Sources")."""
    if heading not in body:
        # Append the heading at end with the block
        return f"{body.rstrip()}\n\n{heading}\n\n{new_block.strip()}\n"

    sections = _split_h2_sections(body)
    existing = sections.get(heading, "").strip()
    if existing == PLACEHOLDER or not existing:
        sections[heading] = new_block.strip()
    else:
        sections[heading] = f"{existing}\n\n{new_block.strip()}"

    # Re-render (preserve order: prefix, then H2_ORDER, then any unknown H2 tail)
    out: list[str] = []
    prefix = sections.pop("__prefix__", "").strip()
    if prefix:
        out.append(prefix)
    for h2 in H2_ORDER:
        if h2 in sections:
            out.append(f"{h2}\n\n{sections.pop(h2).strip() or PLACEHOLDER}")
    # Any leftover H2 (forward-compat for future v3 sections)
    for h2, content in sections.items():
        if h2.startswith("## "):
            out.append(f"{h2}\n\n{content.strip() or PLACEHOLDER}")
    return "\n\n".join(out) + "\n"


def _conflict_block_to_md(topic: str, source_link: str, c: ConflictBlock) -> str:
    lines = [
        f"### Topic: {topic}",
        f"- **既有**: {c.existing_claim}",
        f"- **{source_link}**: {c.new_claim}",
    ]
    if c.possible_reason:
        lines.append(f"- **可能原因**: {c.possible_reason}")
    if c.consensus:
        lines.append(f"- **共識點**: {c.consensus}")
    if c.uncertainty:
        lines.append(f"- **不確定區**: {c.uncertainty}")
    return "\n".join(lines)


def _strip_legacy_update_blocks(body: str) -> tuple[str, int]:
    """Remove `## 更新（{date}）` body-append blocks that violate v2 aggregator
    principle. Returns (clean_body, count_stripped).
    """
    pattern = re.compile(
        r"\n+(?:---\n+)?## 更新（\d{4}-\d{2}-\d{2}）.*?(?=\n## |\Z)",
        re.DOTALL,
    )
    matches = pattern.findall(body)
    cleaned = pattern.sub("", body)
    return (cleaned.rstrip() + "\n", len(matches))


# ---------------------------------------------------------------------------
# Public write API
# ---------------------------------------------------------------------------


def update_mentioned_in(page_path: Path, source_link: str) -> bool:
    """Append source_link to mentioned_in list (idempotent).

    Lazy-migrates v1 pages to v2 before writing so callers don't end up with a
    hybrid frontmatter (v1 status/related_pages alongside v2 mentioned_in).

    Returns True if appended, False if already present.
    """
    loaded = _load_page(page_path)
    if loaded is None:
        return False
    raw_fm, raw_body = loaded
    fm, body, _mig_changes = _v1_to_v2_in_memory(raw_fm, raw_body)
    mentioned = fm.get("mentioned_in") or []
    if not isinstance(mentioned, list):
        mentioned = []
    if source_link in mentioned:
        return False
    mentioned.append(source_link)
    fm["mentioned_in"] = mentioned
    _write_page_file(page_path, fm, body)
    return True


def aggregate_conflict(
    page_path: Path,
    topic: str,
    source_link: str,
    existing_claim: str,
    new_claim: str,
    possible_reason: str | None = None,
    consensus: str | None = None,
    uncertainty: str | None = None,
) -> None:
    """Append a structured conflict block under `## 文獻分歧 / Discussion`;
    sync `discussion_topics` and `mentioned_in` frontmatter.

    Lazy-migrates v1 pages to v2 before writing (otherwise re-emitting v1
    `status` / `related_pages` alongside v2 fields produces an invalid
    hybrid). Body block is idempotent: re-calling with the same
    (topic, source_link, claims) does not double-append.
    """
    loaded = _load_page(page_path)
    if loaded is None:
        raise FileNotFoundError(f"Page not found: {page_path}")
    raw_fm, raw_body = loaded
    fm, body, _mig_changes = _v1_to_v2_in_memory(raw_fm, raw_body)
    block = ConflictBlock(
        topic=topic,
        existing_claim=existing_claim,
        new_claim=new_claim,
        possible_reason=possible_reason,
        consensus=consensus,
        uncertainty=uncertainty,
    )
    new_md = _conflict_block_to_md(topic, source_link, block)
    if new_md.strip() not in body:
        body = _append_to_section(body, DISCUSSION_HEADING, new_md)

    topics = fm.get("discussion_topics") or []
    if not isinstance(topics, list):
        topics = []
    if topic not in topics:
        topics.append(topic)
    fm["discussion_topics"] = topics

    # Cite source_link in body → must also appear in mentioned_in
    # (symmetry with `upsert_concept_page(action='update_conflict')`).
    mentioned = fm.get("mentioned_in") or []
    if not isinstance(mentioned, list):
        mentioned = []
    if source_link not in mentioned:
        mentioned.append(source_link)
        fm["mentioned_in"] = mentioned

    fm["updated"] = date.today()
    _write_page_file(page_path, fm, body)


def upsert_concept_page(
    slug: str,
    action: Literal["create", "update_merge", "update_conflict", "noop"],
    source_link: str,
    *,
    title: str | None = None,
    domain: str | None = None,
    aliases: list[str] | None = None,
    extracted_body: str | None = None,
    conflict: ConflictBlock | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    now: datetime | None = None,
) -> Path:
    """統一 concept page 寫入入口 (ADR-011 §3.5.1).

    Args:
        slug: page slug (filename minus .md)
        action: one of create / update_merge / update_conflict / noop
        source_link: wikilink form, e.g. "[[Sources/Books/foo/ch1]]"
        title: required for action=create
        domain: required for action=create (default "general" if missing)
        aliases: candidate aliases (merged into existing for update_*)
        extracted_body: body content (action=create / update_merge)
        conflict: ConflictBlock (action=update_conflict)
        tags: optional tag list (create only)
        confidence: 0..1 (create only)
        now: timestamp injection for tests; defaults to UTC now
    """
    _validate_slug(slug, kind="concept slug")
    if now is None:
        now = datetime.now(timezone.utc)

    abs_path = _concept_abs_path(slug)
    today = date.today()

    if action == "create":
        if abs_path.exists():
            logger.warning(f"upsert(create) on existing page {slug}; falling back to update_merge")
            action = "update_merge"
        else:
            if not title:
                raise ValueError("create action requires title")
            if not extracted_body:
                raise ValueError("create action requires extracted_body")
            fm = {
                "schema_version": 2,
                "title": title,
                "type": "concept",
                "domain": domain or "general",
                "aliases": list(aliases or []),
                "mentioned_in": [source_link],
                "source_refs": [],
                "discussion_topics": [],
                "confidence": confidence,
                "tags": list(tags or []),
                "created": today,
                "updated": today,
            }
            body = _ensure_h2_skeleton(extracted_body)
            _write_page_file(abs_path, fm, body)
            logger.info(
                "concept created",
                extra={"slug": slug, "action": "create", "source": source_link},
            )
            return abs_path

    # All update / noop paths
    loaded = _load_page(abs_path)
    if loaded is None:
        raise FileNotFoundError(f"Concept page not found for upsert({action}): {slug}")
    raw_fm, raw_body = loaded

    # Lazy migrate v1 → v2 if needed
    fm, body, mig_changes = _v1_to_v2_in_memory(raw_fm, raw_body)

    needs_legacy_strip = "## 更新（" in body

    if action == "noop":
        # Strip legacy `## 更新（date）` blocks if present — otherwise a v1
        # page whose first v2 touch is a noop ends up permanently stuck with
        # `schema_version=2` frontmatter + legacy body, because future reads
        # short-circuit migration on `schema_version==2`.
        body_changed = False
        if needs_legacy_strip:
            body, stripped = _strip_legacy_update_blocks(body)
            if stripped:
                body_changed = True
                logger.info(
                    f"stripped {stripped} legacy `## 更新` blocks on noop",
                    extra={"slug": slug},
                )
        existing_mentioned = list(raw_fm.get("mentioned_in") or [])
        if source_link not in fm.get("mentioned_in", []):
            fm.setdefault("mentioned_in", []).append(source_link)
        # Write when migration / legacy strip / new source_link — any of
        # them counts as substantive change.
        if mig_changes or body_changed or source_link not in existing_mentioned:
            _write_page_file(abs_path, fm, body)
            logger.info(
                "concept noop persisted",
                extra={
                    "slug": slug,
                    "action": "noop",
                    "source": source_link,
                    "migrated": bool(mig_changes),
                    "stripped_legacy": body_changed,
                },
            )
        return abs_path

    # update_merge / update_conflict — backup first
    pre_content = abs_path.read_text(encoding="utf-8")
    bpath = _backup_concept(slug, pre_content, now)
    logger.info(
        f"backup written: {bpath.name}",
        extra={"slug": slug, "action": action, "backup": str(bpath)},
    )

    # Aliases merge (de-dup)
    if aliases:
        existing_aliases = fm.get("aliases") or []
        for a in aliases:
            if a not in existing_aliases and a != slug:
                existing_aliases.append(a)
        fm["aliases"] = existing_aliases

    if action == "update_merge":
        if not extracted_body:
            raise ValueError("update_merge action requires extracted_body")
        # Strip legacy `## 更新` blocks before LLM merge so we don't carry forward the noise
        if needs_legacy_strip:
            body, stripped = _strip_legacy_update_blocks(body)
            if stripped:
                logger.info(
                    f"stripped {stripped} legacy `## 更新` blocks before merge",
                    extra={"slug": slug},
                )
        # Ensure H2 skeleton present so LLM has stable structure to merge into
        body = _ensure_h2_skeleton(body)
        merge_prompt = _DIFF_MERGE_PROMPT.format(
            existing_body=body,
            new_extract=extracted_body,
            source_link=source_link,
        )
        merged = _ask_llm(merge_prompt)
        body = _ensure_h2_skeleton(merged.strip())
        # Always idempotent-append mentioned_in
        mentioned = fm.get("mentioned_in") or []
        if source_link not in mentioned:
            mentioned.append(source_link)
            fm["mentioned_in"] = mentioned
        fm["updated"] = today
        _write_page_file(abs_path, fm, body)
        logger.info(
            "concept update_merge complete",
            extra={"slug": slug, "action": "update_merge", "source": source_link},
        )

    elif action == "update_conflict":
        if conflict is None:
            raise ValueError("update_conflict action requires conflict ConflictBlock")
        # Strip legacy `## 更新` blocks if present (one-time cleanup on first v2 touch)
        if needs_legacy_strip:
            body, stripped = _strip_legacy_update_blocks(body)
            if stripped:
                logger.info(f"stripped {stripped} legacy `## 更新` blocks", extra={"slug": slug})
        body = _ensure_h2_skeleton(body)
        new_md = _conflict_block_to_md(conflict.topic, source_link, conflict)
        # Idempotency: re-ingest / retry / double-drop should not double-append
        # the same (topic, source_link) block. Frontmatter discussion_topics +
        # mentioned_in already dedup below; this closes the body gap.
        if new_md.strip() not in body:
            body = _append_to_section(body, DISCUSSION_HEADING, new_md)

        topics = fm.get("discussion_topics") or []
        if conflict.topic not in topics:
            topics.append(conflict.topic)
        fm["discussion_topics"] = topics

        mentioned = fm.get("mentioned_in") or []
        if source_link not in mentioned:
            mentioned.append(source_link)
            fm["mentioned_in"] = mentioned

        fm["updated"] = today
        _write_page_file(abs_path, fm, body)
        logger.info(
            "concept update_conflict complete",
            extra={"slug": slug, "action": "update_conflict", "topic": conflict.topic},
        )

    else:
        raise ValueError(f"Unknown action: {action}")

    return abs_path


def write_source_page(
    book_id: str,
    chapter_index: int,
    chapter_title: str,
    *,
    source_md: str,
    lang: str = "en",
    section_anchors: list[str] | None = None,
    page_range: str = "",
    figures: list[FigureRef] | None = None,
    ingested_by: str = "claude-code-opus-4.7",
) -> Path:
    """Write a chapter source page to KB/Wiki/Sources/Books/{book_id}/ch{n}.md.

    `source_md` is the body content (post chapter-summary prompt). Frontmatter
    is built from kwargs and validated against ChapterSourcePageV2.
    """
    _validate_slug(book_id, kind="book_id")
    if not isinstance(chapter_index, int) or chapter_index < 1:
        raise ValueError(f"chapter_index must be a positive int, got {chapter_index!r}")
    page = ChapterSourcePageV2(
        lang=lang,
        book_id=book_id,
        chapter_index=chapter_index,
        chapter_title=chapter_title,
        section_anchors=section_anchors or [],
        page_range=page_range,
        figures=figures or [],
        ingested_at=date.today(),
        ingested_by=ingested_by,
    )
    fm = page.model_dump(mode="json")
    rel = f"{KB_BOOK_SOURCES_DIR}/{book_id}/ch{chapter_index}.md"
    abs_path = get_vault_path() / rel
    _write_page_file(abs_path, fm, source_md)
    logger.info(
        "chapter source written",
        extra={"book_id": book_id, "chapter": chapter_index, "figures": len(page.figures)},
    )
    return abs_path


def upsert_book_entity(
    book_id: str,
    *,
    title: str,
    authors: list[str] | None = None,
    publisher: str = "",
    pub_year: int | None = None,
    book_subtype: str = "textbook_pro",
    domain: str = "general",
    status: Literal["partial", "complete"] = "partial",
    chapters_total: int | None = None,
) -> Path:
    """Write/update KB/Wiki/Entities/Books/{book_id}.md (book entity index page).

    `chapters_ingested` counter auto-increments by scanning existing chapter
    source pages under KB/Wiki/Sources/Books/{book_id}/.
    """
    _validate_slug(book_id, kind="book_id")
    rel = f"{KB_BOOK_ENTITIES_DIR}/{book_id}.md"
    abs_path = get_vault_path() / rel

    # Count existing chapter source pages for this book. Sort by extracted int
    # so ch10 follows ch9, not ch1 (lex sort would render `ch1, ch10, ch11, ch2,
    # ..., ch9` for an 11-chapter book — bug_003).
    sources_dir = get_vault_path() / KB_BOOK_SOURCES_DIR / book_id
    chapters_ingested = 0
    chapter_links: list[str] = []
    _CH_NUM_RE = re.compile(r"^ch(\d+)$")

    def _ch_sort_key(p: Path) -> tuple[int, str]:
        m = _CH_NUM_RE.match(p.stem)
        return (int(m.group(1)) if m else 10**9, p.stem)

    if sources_dir.exists():
        for f in sorted(sources_dir.glob("ch*.md"), key=_ch_sort_key):
            chapters_ingested += 1
            chapter_links.append(f"- [[{f.stem}]]")

    today = date.today()
    fm: dict = {
        "schema_version": 2,
        "title": title,
        "type": "entity",
        "entity_type": "book",
        "book_id": book_id,
        "authors": list(authors or []),
        "publisher": publisher,
        "pub_year": pub_year,
        "book_subtype": book_subtype,
        "domain": domain,
        "status": status,
        "chapters_ingested": chapters_ingested,
        "chapters_total": chapters_total,
        "created": today,
        "updated": today,
    }

    # Preserve created date if page exists
    if abs_path.exists():
        existing = _load_page(abs_path)
        if existing:
            existing_fm, _ = existing
            if existing_fm.get("created"):
                fm["created"] = existing_fm["created"]

    body_lines = [
        f"# {title}",
        "",
        f"**Book ID**: `{book_id}`",
        f"**Status**: {status}",
        f"**Chapters ingested**: {chapters_ingested}"
        + (f" / {chapters_total}" if chapters_total else ""),
        "",
        "## Chapters",
        "",
    ]
    if chapter_links:
        body_lines.extend(chapter_links)
    else:
        body_lines.append(PLACEHOLDER)
    body = "\n".join(body_lines)

    _write_page_file(abs_path, fm, body)
    logger.info(
        "book entity upserted",
        extra={"book_id": book_id, "status": status, "chapters": chapters_ingested},
    )
    return abs_path


# ---------------------------------------------------------------------------
# Migration commands
# ---------------------------------------------------------------------------


def migrate_v1_to_v2(slug: str, dry_run: bool = False) -> MigrationReport:
    """Single-page v1 → v2 schema migration.

    - frontmatter: 翻譯成 v2 dict（含 derive mentioned_in from source_refs）
    - body: strip legacy `## 更新（date）` blocks
    - 一次性 LLM diff-merge `## 更新` 內容進主體 — Phase 1: SKIP（手動 review safer）

    Phase 1 strategy: only frontmatter + strip; LLM merge of legacy blocks deferred to
    upsert_concept_page first-touch (which has a proper backup mechanism).
    """
    abs_path = _concept_abs_path(slug)
    loaded = _load_page(abs_path)
    if loaded is None:
        return MigrationReport(
            slug=slug,
            from_version=0,
            to_version=2,
            dry_run=dry_run,
            skipped_reason="page not found",
        )
    raw_fm, raw_body = loaded
    from_version = int(raw_fm.get("schema_version") or 1)
    if from_version == 2:
        return MigrationReport(
            slug=slug,
            from_version=2,
            to_version=2,
            dry_run=dry_run,
            skipped_reason="already v2",
        )

    v2_fm, body, changes = _v1_to_v2_in_memory(raw_fm, raw_body)
    body, stripped = _strip_legacy_update_blocks(body)
    if stripped:
        changes.append(f"- stripped {stripped} legacy `## 更新` blocks (LLM merge deferred)")

    if not dry_run:
        _write_page_file(abs_path, v2_fm, body)

    return MigrationReport(
        slug=slug,
        from_version=from_version,
        to_version=2,
        dry_run=dry_run,
        changes=changes,
    )


def backfill_all_v1_pages(dry_run: bool = True) -> list[MigrationReport]:
    """Scan KB/Wiki/Concepts/ → migrate every v1 page.

    Default dry_run=True for safety; run with dry_run=False after reviewing report.
    """
    cdir = get_vault_path() / KB_CONCEPTS_DIR
    reports: list[MigrationReport] = []
    if not cdir.exists():
        return reports
    for f in sorted(cdir.glob("*.md")):
        reports.append(migrate_v1_to_v2(f.stem, dry_run=dry_run))
    return reports
