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

import yaml

from agents.base import BaseAgent
from agents.franky.news import anthropic_html, awesome_diff, github_trending
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
        cfg_path = feeds_config_path or _FEEDS_CONFIG
        self.feeds: list[FeedConfig] = load_feeds(cfg_path)
        # ADR-023 §2 S1: load extra source blocks from the same yaml.
        raw_cfg: dict = {}
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                raw_cfg = yaml.safe_load(f) or {}
        self.awesome_configs = awesome_diff.load_awesome_configs(raw_cfg.get("awesome_diff"))
        self.trending_config = github_trending.load_trending_config(raw_cfg.get("github_trending"))
        self.operation_id = _new_op_id()
        self._source_breakdown: dict[str, int] = {}
        self._trust_tier_breakdown: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(self) -> str:
        if not self.feeds:
            return "無 feed 設定，略過"

        # 1. Fetch + filter + dedupe.
        # Each source is wrapped so one source's failure can never tank the digest
        # (Slice A: official_blogs 內部已 per-feed swallow；merge layer 另外 wrap
        # anthropic_html — 它若拋未預期 exception 不會帶倒 RSS 結果)。
        skip_seen = not self.dry_run
        rss_candidates = gather_candidates(self.feeds, skip_seen=skip_seen)
        try:
            anthropic_candidates = anthropic_html.gather_candidates(skip_seen=skip_seen)
        except Exception as e:
            self.logger.warning(f"anthropic_html source failed: {e}", exc_info=True)
            anthropic_candidates = []
        try:
            awesome_candidates = awesome_diff.gather_candidates(
                self.awesome_configs, skip_seen=skip_seen
            )
        except Exception as e:
            self.logger.warning(f"awesome_diff source failed: {e}", exc_info=True)
            awesome_candidates = []
        try:
            trending_candidates = github_trending.gather_candidates(
                self.trending_config, skip_seen=skip_seen
            )
        except Exception as e:
            self.logger.warning(f"github_trending source failed: {e}", exc_info=True)
            trending_candidates = []
        candidates = (
            rss_candidates + anthropic_candidates + awesome_candidates + trending_candidates
        )
        # Per-source telemetry — used by --dry-run smoke + integration tests
        # to verify the trust tier distribution.
        self._source_breakdown = {
            "rss": len(rss_candidates),
            "anthropic_html": len(anthropic_candidates),
            "awesome_diff": len(awesome_candidates),
            "github_trending": len(trending_candidates),
        }
        self._trust_tier_breakdown = _count_trust_tiers(candidates)
        # Re-sort across sources by recency (each gather sorts internally, but
        # the merged list needs one more pass).
        candidates.sort(key=lambda c: c.get("published_ts", 0.0), reverse=True)
        if not candidates:
            return "所有 feed 無 24h 內新項目（或全已見過）"

        self.logger.info(
            f"news_digest: {len(candidates)} fresh candidates after dedupe "
            f"(rss={len(rss_candidates)}, anthropic_html={len(anthropic_candidates)}, "
            f"awesome_diff={len(awesome_candidates)}, github_trending={len(trending_candidates)}; "
            f"trust_tiers={self._trust_tier_breakdown})"
        )

        # 2. Curate (one LLM call)
        try:
            curated = self._curate(candidates)
        except Exception as e:
            self.logger.error(f"curate 失敗：{e}", exc_info=True)
            return f"curate 失敗：{e}"

        raw_selected = curated.get("selected", []) or []
        selected_meta_by_id: dict[str, dict] = {}
        dropped_no_id = 0
        for s in raw_selected:
            iid = s.get("item_id")
            if iid:
                selected_meta_by_id[iid] = s
            else:
                dropped_no_id += 1
        if dropped_no_id:
            self.logger.warning(f"Curate 回傳 {dropped_no_id} 條缺 item_id 的 selected 項，已略過")
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
            # ADR-023 §2 S1: experimental low-trust tier — cap overall + per-dim
            # at the candidate's score_ceiling (default 4 for trending).
            score_result = _apply_trust_ceiling(cand, score_result, logger=self.logger)
            scored.append(
                {
                    "candidate": cand,
                    "curate_meta": meta,
                    "score_result": score_result,
                }
            )

        # Filter by score-side `pick` flag (news_score.md 規則：overall ≥ 3.5 且 signal ≥ 3
        # 才設 true)。score 沒明確 false 的當 true 處理，避免 LLM 漏欄位整批掉光。
        rejected_by_pick = sum(1 for s in scored if s["score_result"].get("pick", True) is False)
        scored = [s for s in scored if s["score_result"].get("pick", True) is not False]
        if rejected_by_pick:
            self.logger.info(f"score pick=false 過濾掉 {rejected_by_pick} 條")

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
            # Mark ALL candidates seen (including non-selected and pick=false-filtered) —
            # 沿用 PubMed pattern：即便未入選也 mark，避免明天 LLM 又重 curate 同一批
            # （curate 是 N→K 隨機性，同題目今天沒選不代表明天會選 — 一致比起多餘重算划算）。
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
            f"sources={self._source_breakdown} trust_tiers={self._trust_tier_breakdown} "
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
# Trust tier helpers (ADR-023 §2 S1)
# ----------------------------------------------------------------------


def _count_trust_tiers(candidates: list[dict]) -> dict[str, int]:
    """Group candidate count by trust_tier (default `full_trust` if absent)."""
    out: dict[str, int] = {}
    for c in candidates:
        tier = c.get("trust_tier") or "full_trust"
        out[tier] = out.get(tier, 0) + 1
    return out


def _apply_trust_ceiling(cand: dict, score_result: dict, *, logger=None) -> dict:
    """If the candidate is experimental tier with a score_ceiling, cap the
    LLM-returned ``overall`` and per-dim ``scores`` at that ceiling.

    Pure-ish: returns a new dict (does not mutate input). Keeps non-numeric
    or missing fields untouched.
    """
    ceiling = cand.get("score_ceiling")
    if ceiling is None:
        return score_result
    try:
        ceiling = float(ceiling)
    except (TypeError, ValueError):
        return score_result

    out = dict(score_result)
    overall = out.get("overall")
    if isinstance(overall, (int, float)) and overall > ceiling:
        if logger is not None:
            logger.info(
                f"trust ceiling: cap overall {overall} → {ceiling} "
                f"(item_id={cand.get('item_id')}, tier={cand.get('trust_tier')})"
            )
        out["overall"] = ceiling

    scores = out.get("scores")
    if isinstance(scores, dict):
        new_scores = {}
        for k, v in scores.items():
            if isinstance(v, (int, float)) and v > ceiling:
                new_scores[k] = ceiling
            else:
                new_scores[k] = v
        out["scores"] = new_scores

    out["trust_tier"] = cand.get("trust_tier")
    out["score_ceiling_applied"] = ceiling
    return out


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
    "_apply_trust_ceiling",
    "_count_trust_tiers",
]
