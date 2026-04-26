"""Franky AI news daily digest（Slice A — official blogs only）。

每天 06:30 台北跑：抓 10+ 個 AI 大廠官方 blog RSS → curate 5-8 條精選 →
score 每條 → 寫 vault digest 頁 → Slack DM 推給修修。

設計沿用 agents/robin/pubmed_digest.PubMedDigestPipeline。
路徑：KB/Wiki/Digests/AI/YYYY-MM-DD.md（Asia/Taipei TZ）。

Subcommand 三模式（agents/franky/__main__.py 端 dispatch）：
  python -m agents.franky news              # full path：vault + Slack DM
  python -m agents.franky news --no-publish # 寫 vault 但不送 Slack（dev 驗 vault 寫入）
  python -m agents.franky news --dry-run    # 不寫 vault 也不送 Slack（純 log 流程）

Reddit / X / GitHub trending 屬於 Slice B/C，本檔不引入。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agents.base import BaseAgent
from agents.franky.news.official_blogs import (
    SOURCE_KEY,
    FeedConfig,
    gather_candidates,
    load_feeds,
)
from shared import llm
from shared.obsidian_writer import append_to_file, write_page
from shared.prompt_loader import load_prompt
from shared.state import mark_seen

_ROOT = Path(__file__).resolve().parent.parent.parent
_FEEDS_CONFIG = _ROOT / "config" / "ai_news_sources.yaml"


def _new_op_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"


def _today_taipei() -> str:
    """台北時區的今日 ISO date。cron 在 06:30 台北跑，但 datetime.now(UTC) 會落在前一日。"""
    return datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()


def _parse_json(text: str) -> dict:
    """從 LLM 回應擷取 JSON。容忍外層 ```json``` 包裝或前後閒聊。

    沿用 agents/robin/pubmed_digest._parse_json 的契約。
    """
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"LLM 回應找不到 JSON：{text[:200]}")
    return json.loads(text[start : end + 1])


class NewsDigestPipeline(BaseAgent):
    """Franky daily AI news digest 子流程。"""

    name = "franky"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        no_publish: bool = False,
        slack_bot: Any | None = None,
        feeds_config_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.no_publish = no_publish
        self._slack_bot_override = slack_bot
        self.feeds: list[FeedConfig] = load_feeds(feeds_config_path or _FEEDS_CONFIG)
        self.operation_id = _new_op_id()

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(self) -> str:
        if not self.feeds:
            return "無 feed 設定，略過"

        # 1. Fetch + filter + dedupe
        candidates = gather_candidates(self.feeds, skip_seen=not self.dry_run)
        if not candidates:
            return "所有 feed 無 24h 內新項目（或全已見過）"

        self.logger.info(f"news_digest: {len(candidates)} fresh candidates after dedupe")

        # 2. Curate (one LLM call)
        try:
            curated = self._curate(candidates)
        except Exception as e:
            self.logger.error(f"curate 失敗：{e}", exc_info=True)
            return f"curate 失敗：{e}"

        selected_meta_by_id = {
            s.get("item_id"): s for s in curated.get("selected", []) if s.get("item_id")
        }
        cand_by_id = {c["item_id"]: c for c in candidates}

        # 3. Score each selected (one LLM call per pick)
        scored: list[dict] = []
        for item_id, meta in selected_meta_by_id.items():
            cand = cand_by_id.get(item_id)
            if cand is None:
                self.logger.warning(f"Curate 回傳未知 item_id {item_id!r}，略過")
                continue
            try:
                score_result = self._score(cand, meta)
            except Exception as e:
                self.logger.warning(f"Score {item_id!r} 失敗：{e}")
                continue
            scored.append(
                {
                    "candidate": cand,
                    "curate_meta": meta,
                    "score_result": score_result,
                }
            )

        if not scored:
            return f"候選 {len(candidates)} 條，curate/score 後無精選入選"

        # Sort by curate rank (curate 給的優先序)
        rank_of = {m.get("item_id"): m.get("rank", 999) for m in selected_meta_by_id.values()}
        scored.sort(key=lambda x: rank_of.get(x["candidate"]["item_id"], 999))

        # 4. Write vault outputs
        digest_relpath: str | None = None
        if not self.dry_run:
            digest_relpath = self._write_digest_page(scored, curated, len(candidates))
            self._append_kb_log(digest_relpath, len(scored))
            self._update_kb_index(digest_relpath, len(scored))
            for c in candidates:
                mark_seen(SOURCE_KEY, c["item_id"], c.get("url"))
        else:
            self.logger.info(f"[dry-run] 模擬寫入 1 份 digest（{len(scored)} 條精選）")

        # 5. Slack DM
        slack_ts: str | None = None
        if not self.dry_run and not self.no_publish and digest_relpath:
            slack_ts = self._send_slack_dm(scored, curated, len(candidates), digest_relpath)

        return (
            f"fetch={len(candidates)} selected={len(scored)} "
            f"digest={digest_relpath or '(skipped)'} slack_ts={slack_ts or '(skipped)'} "
            f"op={self.operation_id} (dry_run={self.dry_run} no_publish={self.no_publish})"
        )

    # ------------------------------------------------------------------
    # LLM steps
    # ------------------------------------------------------------------

    def _curate(self, candidates: list[dict]) -> dict:
        """LLM 一次 call：從 N 條候選挑 5-8 條精選。"""
        lines = []
        for c in candidates:
            summary_truncated = c["summary"][:600] if c["summary"] else "（無 summary）"
            age = c.get("age_hours", 0.0)
            lines.append(
                f"\n---\nitem_id: {c['item_id']}\n"
                f"Publisher: {c['publisher']}\n"
                f"Title: {c['title']}\n"
                f"Published: {c['published']} ({age:.1f}h ago)\n"
                f"URL: {c['url']}\n"
                f"Summary: {summary_truncated}"
            )

        prompt = load_prompt(
            "franky",
            "news_curate",
            candidates="\n".join(lines),
            total_candidates=str(len(candidates)),
        )
        response = llm.ask(prompt, max_tokens=4096)
        return _parse_json(response)

    def _score(self, cand: dict, curate_meta: dict) -> dict:
        """單篇深度評分。"""
        prompt = load_prompt(
            "franky",
            "news_score",
            title=cand["title"],
            publisher=cand["publisher"],
            published=cand["published"],
            url=cand["url"],
            summary=cand["summary"][:1500] if cand["summary"] else "（無 summary）",
            curate_reason=str(curate_meta.get("reason", "")),
            category=str(curate_meta.get("category", "meta")),
        )
        response = llm.ask(prompt, max_tokens=1024)
        return _parse_json(response)

    # ------------------------------------------------------------------
    # Vault writers
    # ------------------------------------------------------------------

    def _write_digest_page(
        self,
        scored: list[dict],
        curated: dict,
        total_fresh: int,
    ) -> str:
        """寫 KB/Wiki/Digests/AI/YYYY-MM-DD.md。回傳相對路徑字串。"""
        today = _today_taipei()
        summary = curated.get("summary", {})

        publishers = sorted({i["candidate"]["publisher"] for i in scored})
        categories = sorted({i["curate_meta"].get("category", "meta") for i in scored})

        frontmatter = {
            "date": today,
            "created_by": "franky",
            "source": SOURCE_KEY,
            "total_candidates_fresh": total_fresh,
            "selected_count": len(scored),
            "publishers_covered": publishers,
            "categories": categories,
            "operation_id": self.operation_id,
            "type": "digest",
        }

        body = _render_digest_body(
            today=today,
            editor_note=summary.get("editor_note", ""),
            total_fresh=total_fresh,
            scored=scored,
        )

        relative_path = f"KB/Wiki/Digests/AI/{today}.md"
        write_page(relative_path, frontmatter, body)
        self.logger.info(f"news_digest 寫入：{relative_path}")
        return relative_path

    def _append_kb_log(self, digest_relpath: str, count: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = (
            f"\n- {now} franky: AI news digest written → "
            f"`{Path(digest_relpath).name}` ({count} picks)"
        )
        try:
            append_to_file("KB/log.md", line)
        except FileNotFoundError:
            self.logger.debug("KB/log.md 不存在，略過 log 更新")

    def _update_kb_index(self, digest_relpath: str, count: int) -> None:
        today = _today_taipei()
        line = f"\n- [[Digests/AI/{today}|AI 動態 {today}]] — {count} 條精選"
        try:
            append_to_file("KB/index.md", line)
        except FileNotFoundError:
            self.logger.debug("KB/index.md 不存在，略過 index 更新")

    # ------------------------------------------------------------------
    # Slack DM
    # ------------------------------------------------------------------

    def _send_slack_dm(
        self,
        scored: list[dict],
        curated: dict,
        total_fresh: int,
        digest_relpath: str,
    ) -> str | None:
        bot = self._slack_bot_override
        if bot is None:
            from agents.franky.slack_bot import FrankySlackBot

            bot = FrankySlackBot.from_env()

        text = _render_slack_text(
            today=_today_taipei(),
            scored=scored,
            curated=curated,
            total_fresh=total_fresh,
            digest_relpath=digest_relpath,
            operation_id=self.operation_id,
        )
        return bot.post_plain(text, context="news_digest")


# ----------------------------------------------------------------------
# Pure render functions (no side effects)
# ----------------------------------------------------------------------


def _render_digest_body(
    *,
    today: str,
    editor_note: str,
    total_fresh: int,
    scored: list[dict],
) -> str:
    lines = [
        f"# AI 每日情報 — {today}",
        "",
        f"> {editor_note}" if editor_note else "",
        "",
        f"**候選總數**：{total_fresh}　**精選**：{len(scored)}",
        "",
        "---",
        "",
    ]
    for rank, item in enumerate(scored, 1):
        lines.extend(_render_digest_entry(rank, item))
    return "\n".join(lines)


def _render_digest_entry(rank: int, item: dict) -> list[str]:
    cand = item["candidate"]
    meta = item["curate_meta"]
    score = item["score_result"]
    scores = score.get("scores", {})
    return [
        f"## {rank}. {cand['title']}",
        "",
        f"- **Publisher**: {cand['publisher']}",
        f"- **Category**: `{meta.get('category', 'meta')}`",
        f"- **Published**: {cand['published']} ({cand.get('age_hours', 0):.1f}h ago)",
        (
            f"- **Score**: {score.get('overall', '—')}  "
            f"(S{scores.get('signal', '—')}/"
            f"N{scores.get('novelty', '—')}/"
            f"A{scores.get('actionability', '—')}/"
            f"Q{scores.get('noise', '—')})"
        ),
        f"- **Verdict**: {score.get('one_line_verdict', '')}",
        f"- **Why**: {score.get('why_it_matters', '')}",
        f"- **Key**: {score.get('key_finding', '')}",
        f"- **Noise note**: {score.get('noise_note', '')}",
        f"- **→** [{cand['url']}]({cand['url']})",
        "",
    ]


def _render_slack_text(
    *,
    today: str,
    scored: list[dict],
    curated: dict,
    total_fresh: int,
    digest_relpath: str,
    operation_id: str,
) -> str:
    """Slack DM 純文字（無 mrkdwn 粗體；CJK 對 *bold* 不穩 — feedback_slack_cjk_mrkdwn）。"""
    summary = curated.get("summary", {})
    editor_note = summary.get("editor_note", "")

    lines: list[str] = [
        f"Franky AI Daily — {today}",
        f"候選 {total_fresh} / 精選 {len(scored)}",
    ]
    if editor_note:
        lines.append("")
        lines.append(editor_note)
    lines.append("")
    for rank, item in enumerate(scored, 1):
        cand = item["candidate"]
        score = item["score_result"]
        verdict = score.get("one_line_verdict", "") or cand["title"]
        # 一行濃縮：[publisher] verdict
        lines.append(f"{rank}. [{cand['publisher']}] {verdict}")
        lines.append(f"   → {cand['url']}")
    lines.append("")
    lines.append(f"完整 digest（vault）：{digest_relpath}")
    lines.append(f"op={operation_id}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Public entry — used by agents/franky/__main__.py
# ----------------------------------------------------------------------


def run_news_digest(*, dry_run: bool = False, no_publish: bool = False) -> str:
    """Entry called by `python -m agents.franky news`.

    dry_run=True 跳過 BaseAgent.execute() 的 agent_runs / episodic memory
    side effects（CLI dev 重複跑不污染 state.db）。
    """
    pipeline = NewsDigestPipeline(dry_run=dry_run, no_publish=no_publish)
    if dry_run:
        return pipeline.run()
    pipeline.execute()
    return f"op={pipeline.operation_id}"


__all__ = [
    "NewsDigestPipeline",
    "run_news_digest",
    "_render_digest_body",
    "_render_slack_text",
    "_parse_json",
]
