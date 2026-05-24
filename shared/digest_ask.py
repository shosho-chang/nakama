"""LLM-over-vault: ad-hoc query against recent Robin/Franky digests.

Tier A PR3. Surface lives at ``/bridge/digests/ask`` (POST). This module
holds the pure backend: concat the in-scope digest files, build a Claude
prompt, dispatch via ``shared.anthropic_client.ask_claude``, return the
answer plus the source list used.

Cost discipline:
- Total concatenated context capped at ``MAX_CONTEXT_CHARS``. Older days
  drop out first; we never silently truncate mid-file.
- Days bounded by ``MAX_DAYS`` to stop a 90-day blast from running on a
  $0.20+/call basis.
- Question length capped at ``MAX_QUESTION_CHARS`` defensively.

This is NOT an FTS index or RAG pipeline. Pure path-scoped concat. The
philosophy is documented in ``thousand_sunny/CONTEXT.md`` (LLM-over-vault).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from shared.digest_indexer import DIGEST_TYPES, DigestEntry, DigestIndexer

MAX_DAYS: int = 30
DEFAULT_DAYS: int = 14
MAX_CONTEXT_CHARS: int = 200_000  # ~150k input tokens worst case
MAX_QUESTION_CHARS: int = 500
DEFAULT_MODEL: str = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS: int = 2048

_SYSTEM_PROMPT = (
    "你是修修的研究助理。修修每天看 Robin 整理的 PubMed digest 與 Franky 整理的 AI digest。"
    "使用者會問跨日期的問題，例如「過去兩週有沒有關於 X 的研究」。\n\n"
    "規則：\n"
    "1. 只根據下方提供的 digest 內容回答，不要編造未出現的研究/論文/連結。\n"
    "2. 引用時用 `[type/date]` 格式（例如 `[pubmed/2026-05-24]`），讓使用者點回去看原 digest。\n"
    "3. 找不到相關內容就直接說「過去 N 天的 digest 沒有提到 X」，不要硬湊。\n"
    "4. 回答用繁體中文，簡潔有重點。"
)


class AskValidationError(ValueError):
    """User-facing input error — render inline on the form."""


@dataclass(frozen=True)
class AskRequest:
    question: str
    days: int
    types: tuple[str, ...]


@dataclass(frozen=True)
class AskResult:
    question: str
    answer: str
    sources: tuple[DigestEntry, ...]
    days: int
    types: tuple[str, ...]
    context_chars: int
    truncated: bool  # True iff one or more entries were dropped due to char cap
    dropped_count: int  # number of in-scope entries excluded by the char cap
    oldest_included_date: str | None  # YYYY-MM-DD of oldest kept entry, or None


def parse_request(
    *,
    question: str | None,
    days: str | None,
    types: Sequence[str] | None,
) -> AskRequest:
    q = (question or "").strip()
    if not q:
        raise AskValidationError("請輸入問題")
    if len(q) > MAX_QUESTION_CHARS:
        raise AskValidationError(f"問題太長（>{MAX_QUESTION_CHARS} 字）")

    try:
        d = int(days) if days else DEFAULT_DAYS
    except ValueError:
        raise AskValidationError("天數必須是整數")
    if d < 1 or d > MAX_DAYS:
        raise AskValidationError(f"天數需在 1–{MAX_DAYS} 之間")

    if types:
        chosen = tuple(t for t in types if t in DIGEST_TYPES)
    else:
        chosen = DIGEST_TYPES
    if not chosen:
        raise AskValidationError("至少選一種 digest 類型")

    return AskRequest(question=q, days=d, types=chosen)


def ask(
    req: AskRequest,
    indexer: DigestIndexer,
    *,
    llm: Callable[..., str] | None = None,
    model: str = DEFAULT_MODEL,
) -> AskResult:
    """Run the LLM-over-vault query. ``llm`` defaults to
    ``shared.anthropic_client.ask_claude``; tests inject a stub.
    """
    all_entries = indexer.last_n_days(n=req.days)
    in_scope = [e for e in all_entries if e.type in req.types]

    context_parts: list[str] = []
    used: list[DigestEntry] = []
    total = 0
    truncated = False
    for entry in in_scope:
        body = indexer.load_text(entry.type, entry.date)
        header = f"\n\n=== [{entry.type}/{entry.date}] ===\n\n"
        chunk = header + body
        if total + len(chunk) > MAX_CONTEXT_CHARS:
            truncated = True
            break
        context_parts.append(chunk)
        used.append(entry)
        total += len(chunk)

    if not used:
        return AskResult(
            question=req.question,
            answer=f"過去 {req.days} 天無 digest 可查（指定的類型 / 範圍內沒檔案）。",
            sources=(),
            days=req.days,
            types=req.types,
            context_chars=0,
            truncated=False,
            dropped_count=0,
            oldest_included_date=None,
        )

    context = "".join(context_parts)
    prompt = f"<digests>\n{context}\n</digests>\n\n使用者問題：{req.question}\n\n請依規則回答。"

    if llm is None:
        from shared.anthropic_client import ask_claude

        llm = ask_claude

    answer = llm(
        prompt,
        system=_SYSTEM_PROMPT,
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return AskResult(
        question=req.question,
        answer=answer.strip(),
        sources=tuple(used),
        days=req.days,
        types=req.types,
        context_chars=total,
        truncated=truncated,
        dropped_count=len(in_scope) - len(used),
        oldest_included_date=used[-1].date if used else None,
    )
