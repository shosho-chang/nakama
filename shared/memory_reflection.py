"""Memory reflection / consolidation pass — Memory v2 (the "self-learning" loop).

The extraction pipeline (``shared.memory_extractor``) only ever *appends* facts.
Over time that leaves the ``user_memories`` store with near-duplicate subjects
(`工作習慣` vs `工作時段`), stale contradictions both still "true", and a flat
pile that never sharpens. This module is the periodic **consolidation** pass the
research calls *memory evolution* (A-MEM) / *sleep-time compute* (Letta): an LLM
reviews a user's active memories and proposes conservative, structured edits.

Four operations (validated, applied transactionally, fully auditable):

- ``merge``      — fold duplicate subjects into one canonical memory; the
                   absorbed rows are soft-superseded (history kept).
- ``supersede``  — a memory contradicts a newer one → mark the old invalidated,
                   optionally creating the corrected replacement (bi-temporal,
                   à la Zep/Graphiti — no overwrite, ``superseded_by`` records
                   the provenance).
- ``promote``    — a well-supported pattern → raise confidence (and optionally
                   change type), so repeatedly-observed facts rise in retrieval.
- ``drop``       — pure noise → soft-invalidate (reversible, never hard-deleted).

Soft-invalidation only (via ``agent_memory.supersede``); nothing is destroyed,
so a bad pass is recoverable and every change is traceable. Default run mode is
DRY-RUN — call with ``apply=True`` (or CLI ``--apply``) to mutate.

CLI:
    python -m shared.memory_reflection --agent nami --user U_SHOSHO           # preview
    python -m shared.memory_reflection --agent nami --user U_SHOSHO --apply   # commit
    python -m shared.memory_reflection --all --apply                          # every (agent,user)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from shared import agent_memory, episodic_memory
from shared.agent_memory import VALID_TYPES, UserMemory
from shared.episodic_memory import EpisodicMemory
from shared.llm import ask
from shared.log import get_logger
from shared.state import _get_conn

logger = get_logger("nakama.memory_reflection")

# Reflection is reasoning-heavy (spot contradictions, judge duplicate meaning) so
# it uses Sonnet, not the Haiku the extractor uses. Env-overridable per the router
# convention (MODEL_<...>); defaults to the repo-canonical Sonnet 4.6.
_REFLECT_MODEL = os.getenv("MODEL_MEMORY_REFLECTION") or "claude-sonnet-4-6"

# Don't bother the LLM for a near-empty store; consolidation needs material.
_MIN_MEMORIES = 3

_VALID_OPS = frozenset({"merge", "supersede", "promote", "drop", "share", "promote_episodic"})


_SYSTEM_PROMPT = """你是 AI agent 的「記憶整理員」。給你一個 agent 對某位使用者的現有記憶清單，
你的工作是讓這份記憶**更精準、更一致、更能反映使用者真實樣貌** —— 但要極度保守，寧可不動也不要亂改。

給你兩份清單：**語意記憶**（穩定的事實/偏好，一個 subject 一筆）與**事件日誌**（episodic，
帶時間的觀察，id 前綴 `E`）。每筆語意記憶有 id / type / subject / content / confidence。
你只能輸出以下六種操作：

- `merge`：兩筆以上語意重複/重疊的記憶合併成一筆。給 `ids`（要合併的所有 id）、合併後的
  `subject`/`type`/`content`/`confidence`（content 必須涵蓋所有來源的資訊，不可遺漏）、`reason`。
- `supersede`：某筆記憶被新事實推翻（互相矛盾，且能判斷哪筆較新/正確）。給 `id`（被推翻的舊記憶）、
  `reason`，可選 `replacement`（修正後的新記憶 `subject`/`type`/`content`/`confidence`）。
- `promote`：某筆記憶在多處被佐證、明顯是穩定的核心事實 → 提高 confidence。
  給 `id`、新 `confidence`、可選新 `type`、`reason`。
- `drop`：明顯是噪音/一次性/已無意義的記憶 → 標記失效。給 `id`、`reason`。
- `share`：某筆記憶是關於**使用者本人的通用事實**（身分、專業、語言、長期偏好——
  跟「這個 agent 的特定任務」無關），值得讓**整個 agent 團隊共用** → 提升到 fleet 共享層。
  給 `id`、`reason`。判準：換成別的助理也該知道這件事 → share；
  只跟本 agent 的工作流程有關（例如「要每週趨勢報告」）→ 不 share。
- `promote_episodic`：事件日誌裡出現**重複的模式**（例：連三週週二錄課、多次提到睡眠問題）
  → 萃取成一筆穩定的語意記憶。給 `episodic_ids`（支持此模式的 E 編號，去掉 `E` 前綴只給數字）、
  新語意記憶的 `subject`/`type`/`content`/`confidence`、`reason`。
  **這是 agent 從觀察中學會你的核心機制** —— 但同樣保守：單一一次性事件**不算模式**，
  至少要有重複或明確規律才 promote。

## 鐵則（違反就是失敗）
1. **保守**：只在「明顯重複」「明確矛盾」「明顯通用」或「明確重複模式」時動手。模稜兩可一律不動。
2. **不可虛構 id**：只能引用清單裡實際存在的 id（語意用數字、episodic 用清單裡的 E 編號去前綴）。
3. **merge 不可遺失資訊**：合併後的 content 要保留所有來源筆的有效資訊。
4. **type 必須是** preference / fact / decision / context 之一。
5. **單一事件不是模式**：promote_episodic 至少要有重複/規律佐證。
6. 沒有任何該動的 → 回空陣列 `[]`。不要為了交差而硬湊操作。

## 輸出格式
**直接輸出 JSON 陣列**：回覆的第一個字元必須是 `[`。禁止任何前言、分析、說明或 markdown code fence。
所有理由寫進每個操作的 `reason` 欄位，不要寫在 JSON 之外。範例：

[
  {"op": "merge", "ids": [3, 7], "subject": "工作時段", "type": "preference",
   "content": "早上頭腦最清楚，深度工作排在下午兩點前；不喜歡晚上開會", "confidence": 0.95,
   "reason": "id3 與 id7 都在講工作時段偏好，重複"},
  {"op": "supersede", "id": 5, "reason": "已搬到台北，舊的居住地記憶過時",
   "replacement": {"subject": "居住地", "type": "fact", "content": "現居台北", "confidence": 0.9}},
  {"op": "drop", "id": 11, "reason": "一次性閒聊，無長期價值"}
]"""


@dataclass
class ReflectionResult:
    agent: str
    user_id: str
    reviewed: int = 0
    merged: int = 0
    superseded: int = 0
    promoted: int = 0
    dropped: int = 0
    shared: int = 0
    promoted_episodic: int = 0
    episodic_reviewed: int = 0
    applied: bool = False
    ops: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # invalid ops + why

    def summary(self) -> str:
        mode = "applied" if self.applied else "dry-run"
        return (
            f"[{mode}] {self.agent}/{self.user_id}: reviewed={self.reviewed} "
            f"episodic={self.episodic_reviewed} merged={self.merged} "
            f"superseded={self.superseded} promoted={self.promoted} dropped={self.dropped} "
            f"shared={self.shared} promoted_episodic={self.promoted_episodic} "
            f"skipped={len(self.skipped)}"
        )


def _format_memories_for_prompt(memories: list[UserMemory]) -> str:
    lines = []
    for m in memories:
        lines.append(
            f"- id={m.id} [{m.type}] subject={m.subject!r} confidence={m.confidence:.2f}\n"
            f"    content: {m.content}"
        )
    return "\n".join(lines)


def _format_episodic_for_prompt(events: list[EpisodicMemory]) -> str:
    # E-prefixed ids so the LLM can't confuse them with semantic memory ids.
    return "\n".join(f"- E{e.id} ({e.occurred_at[:10]}) {e.observation}" for e in events)


def _extract_array(s: str) -> str | None:
    """Pull the first balanced top-level ``[...]`` out of ``s``.

    Reasoning models (Sonnet) often preface the JSON with analysis prose despite
    the prompt — observed live: ``"我來分析這份記憶清單...\n\n[{...}]"``. Bracket-depth
    match from the first ``[`` recovers the array instead of dropping valid output.
    """
    start = s.find("[")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _parse_ops(raw: str) -> list[dict]:
    """Tolerant JSON-array parse. Handles a code fence, a bare array, or an array
    embedded after prose preamble (reasoning models do this even when told not to)."""
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    data: Any = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        candidate = _extract_array(raw)
        if candidate is not None:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                data = None
    if data is None:
        logger.warning("reflection returned invalid JSON: %s", raw[:200])
        return []
    if not isinstance(data, list):
        logger.warning("reflection returned non-list: %s", type(data).__name__)
        return []
    return data


def _valid_replacement(repl: Any) -> dict | None:
    if not isinstance(repl, dict):
        return None
    subject = repl.get("subject")
    content = repl.get("content")
    type_ = repl.get("type")
    if type_ not in VALID_TYPES:
        return None
    if not isinstance(subject, str) or not subject.strip():
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    conf = repl.get("confidence", 0.8)
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.8
    return {
        "subject": subject.strip(),
        "type": type_,
        "content": content.strip(),
        "confidence": conf,
    }


def _apply_op(
    op: dict,
    *,
    agent: str,
    user_id: str,
    valid_ids: set[int],
    valid_episodic_ids: set[int] | None = None,
) -> str:
    """Apply one validated op. Returns the op kind on success, raises on bad shape.

    ``valid_ids`` is the set of active semantic memory ids for this (agent, user);
    ``valid_episodic_ids`` the active episodic ids. Every id an op references must
    be in the matching set (defends against the LLM hallucinating ids).
    """
    valid_episodic_ids = valid_episodic_ids or set()
    kind = op.get("op")
    if kind == "merge":
        ids = [i for i in op.get("ids", []) if isinstance(i, int)]
        if len({*ids} & valid_ids) < 2 or {*ids} - valid_ids:
            raise ValueError(f"merge ids invalid/insufficient: {op.get('ids')}")
        repl = _valid_replacement(
            {k: op.get(k) for k in ("subject", "type", "content", "confidence")}
        )
        if repl is None:
            raise ValueError("merge missing valid subject/type/content")
        survivor = agent_memory.add(
            agent=agent,
            user_id=user_id,
            type=repl["type"],
            subject=repl["subject"],
            content=repl["content"],
            confidence=repl["confidence"],
        )
        for i in ids:
            if i != survivor:
                agent_memory.supersede(i, replaced_by=survivor)
        return "merge"

    if kind == "supersede":
        mid = op.get("id")
        if mid not in valid_ids:
            raise ValueError(f"supersede id not active: {mid}")
        new_id = None
        repl = _valid_replacement(op.get("replacement")) if op.get("replacement") else None
        if repl is not None:
            new_id = agent_memory.add(
                agent=agent,
                user_id=user_id,
                type=repl["type"],
                subject=repl["subject"],
                content=repl["content"],
                confidence=repl["confidence"],
            )
        agent_memory.supersede(mid, replaced_by=new_id)
        return "supersede"

    if kind == "promote":
        mid = op.get("id")
        if mid not in valid_ids:
            raise ValueError(f"promote id not active: {mid}")
        conf = op.get("confidence")
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError) as e:
            raise ValueError(f"promote bad confidence: {op.get('confidence')}") from e
        new_type = op.get("type") if op.get("type") in VALID_TYPES else None
        agent_memory.update(mid, confidence=conf, type=new_type)
        return "promote"

    if kind == "drop":
        mid = op.get("id")
        if mid not in valid_ids:
            raise ValueError(f"drop id not active: {mid}")
        agent_memory.supersede(mid, replaced_by=None)
        return "drop"

    if kind == "share":
        mid = op.get("id")
        if mid not in valid_ids:
            raise ValueError(f"share id not active: {mid}")
        agent_memory.share(mid)
        return "share"

    if kind == "promote_episodic":
        eids = [i for i in op.get("episodic_ids", []) if isinstance(i, int)]
        if not eids or {*eids} - valid_episodic_ids:
            raise ValueError(f"promote_episodic ids invalid: {op.get('episodic_ids')}")
        sem = _valid_replacement(
            {k: op.get(k) for k in ("subject", "type", "content", "confidence")}
        )
        if sem is None:
            raise ValueError("promote_episodic missing valid subject/type/content")
        semantic_id = agent_memory.add(
            agent=agent,
            user_id=user_id,
            type=sem["type"],
            subject=sem["subject"],
            content=sem["content"],
            confidence=sem["confidence"],
        )
        episodic_memory.mark_promoted(eids, semantic_id)
        return "promote_episodic"

    raise ValueError(f"unknown op kind: {kind!r}")


def reflect(
    agent: str,
    user_id: str,
    *,
    apply: bool = False,
    model: str | None = None,
) -> ReflectionResult:
    """Run one consolidation pass over a user's active memories.

    ``apply=False`` (default) previews: the LLM proposes ops and we validate them
    but do not mutate. ``apply=True`` commits. Safe to run repeatedly; converges
    because each pass shrinks duplicates/contradictions and stamps reviewed rows.
    """
    memories = agent_memory.list_active(agent, user_id)
    episodic = episodic_memory.list_recent(agent, user_id, days=30, include_promoted=False)
    result = ReflectionResult(
        agent=agent,
        user_id=user_id,
        reviewed=len(memories),
        episodic_reviewed=len(episodic),
    )
    # Run if there's enough semantic material OR any episodic backlog to promote.
    if len(memories) < _MIN_MEMORIES and not episodic:
        logger.info("reflection skipped (only %d memories) %s/%s", len(memories), agent, user_id)
        return result

    valid_ids = {m.id for m in memories}
    valid_episodic_ids = {e.id for e in episodic}
    sections = ["## 語意記憶\n" + (_format_memories_for_prompt(memories) or "（無）")]
    if episodic:
        sections.append(
            "## 事件日誌（episodic，找重複模式 → promote_episodic）\n"
            + _format_episodic_for_prompt(episodic)
        )
    prompt = "\n\n".join(sections) + "\n\n請輸出整理操作（JSON 陣列；沒有該動的就回 []）。"

    try:
        raw = ask(
            prompt=prompt,
            system=_SYSTEM_PROMPT,
            model=model or _REFLECT_MODEL,
            max_tokens=2048,
        )
    except Exception as e:
        logger.warning("reflection LLM call failed %s/%s: %s", agent, user_id, e)
        return result

    ops = _parse_ops(raw)
    counts = {
        "merge": 0,
        "supersede": 0,
        "promote": 0,
        "drop": 0,
        "share": 0,
        "promote_episodic": 0,
    }
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in _VALID_OPS:
            result.skipped.append(f"bad op shape: {op!r:.120}")
            continue
        if not apply:
            result.ops.append(op)
            counts[op["op"]] = counts.get(op["op"], 0) + 1
            continue
        try:
            kind = _apply_op(
                op,
                agent=agent,
                user_id=user_id,
                valid_ids=valid_ids,
                valid_episodic_ids=valid_episodic_ids,
            )
            counts[kind] += 1
            result.ops.append(op)
        except ValueError as e:
            result.skipped.append(str(e))
            logger.warning("reflection skipped op %s/%s: %s", agent, user_id, e)

    result.merged = counts["merge"]
    result.superseded = counts["supersede"]
    result.promoted = counts["promote"]
    result.dropped = counts["drop"]
    result.shared = counts["share"]
    result.promoted_episodic = counts["promote_episodic"]
    result.applied = apply

    if apply:
        agent_memory.mark_reflected([m.id for m in memories])
    logger.info("reflection %s", result.summary())
    return result


def _all_agent_user_pairs() -> list[tuple[str, str]]:
    """Distinct (agent, user_id) with at least one active memory."""
    agent_memory._ensure_schema()
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT DISTINCT agent, user_id FROM user_memories WHERE {agent_memory._ACTIVE_CLAUSE}"
    ).fetchall()
    return [(r["agent"], r["user_id"]) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory reflection / consolidation pass")
    parser.add_argument("--agent", help="agent name (e.g. nami)")
    parser.add_argument("--user", help="user id (e.g. U_SHOSHO)")
    parser.add_argument("--all", action="store_true", help="every (agent,user) pair with memories")
    parser.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    args = parser.parse_args()

    if args.all:
        pairs = _all_agent_user_pairs()
    elif args.agent and args.user:
        pairs = [(args.agent, args.user)]
    else:
        parser.error("provide --all, or both --agent and --user")

    for agent, user_id in pairs:
        result = reflect(agent, user_id, apply=args.apply)
        print(result.summary())
        if not args.apply:
            for op in result.ops:
                print(f"    {json.dumps(op, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
