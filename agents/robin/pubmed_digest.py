"""Robin — PubMed 每日研究 digest。

每天早上從 PubMed RSS 抓取新發表的論文，用 LLM 做 curation + 評分，
輸出到 Obsidian vault：
  - KB/Wiki/Digests/PubMed/YYYY-MM-DD.md   每日 digest 頁
  - KB/Wiki/Sources/pubmed-{{pmid}}.md       每篇精選獨立頁
  - KB/log.md                              append 紀錄

與既有 Robin KB ingest pipeline 獨立（那條是檔案導向、全文處理；這條是 abstract-only）。
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import feedparser
import yaml

from agents.base import BaseAgent
from shared.anthropic_client import ask_claude
from shared.journal_metrics import lookup as journal_lookup
from shared.obsidian_writer import append_to_file, write_page
from shared.prompt_loader import load_prompt
from shared.state import is_seen, mark_seen

_ROOT = Path(__file__).resolve().parent.parent.parent
_FEEDS_CONFIG = _ROOT / "config" / "pubmed_feeds.yaml"
_SOURCE_NAME = "pubmed"  # scout_seen 用的 source key


class PubMedDigestPipeline(BaseAgent):
    """Robin 的 PubMed 每日 digest 子流程。"""

    name = "robin"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.feeds = self._load_feeds_config()

    def _load_feeds_config(self) -> list[dict]:
        if not _FEEDS_CONFIG.exists():
            self.logger.warning(f"找不到 feed 設定檔：{_FEEDS_CONFIG}")
            return []
        with open(_FEEDS_CONFIG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("feeds", [])

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self) -> str:
        if not self.feeds:
            return "無 feed 設定，略過"

        # 1. Fetch
        all_candidates = []
        for feed in self.feeds:
            items = self._fetch_feed(feed)
            self.logger.info(f"[{feed['name']}] 抓到 {len(items)} 筆")
            all_candidates.extend(items)

        if not all_candidates:
            return "所有 feed 皆無資料"

        # 2. Dedup（同批內 PMID 重複也去除）
        seen_in_batch = set()
        fresh: list[dict] = []
        for c in all_candidates:
            pmid = c["pmid"]
            if pmid in seen_in_batch:
                continue
            seen_in_batch.add(pmid)
            if is_seen(_SOURCE_NAME, pmid):
                continue
            fresh.append(c)

        self.logger.info(
            f"候選 {len(all_candidates)} 筆，同批/歷史去重後剩 {len(fresh)} 筆待 curate"
        )

        if not fresh:
            return f"候選 {len(all_candidates)} 筆全數已見過，略過"

        # 3. Enrich with journal tier
        for c in fresh:
            info = journal_lookup(journal_name=c["journal"], issn=c.get("issn"))
            c["journal_tier"] = info

        # 4. Curate
        curated = self._curate(fresh)
        selected_pmids = [s["pmid"] for s in curated.get("selected", [])]
        selected_meta = {s["pmid"]: s for s in curated.get("selected", [])}
        pmid_to_candidate = {c["pmid"]: c for c in fresh}

        # 5. Score each selected
        scored: list[dict] = []
        for pmid in selected_pmids:
            cand = pmid_to_candidate.get(pmid)
            if not cand:
                # LLM 幻想了一個 PMID — 跳過
                self.logger.warning(f"Curate 回傳未知 PMID {pmid}，略過")
                continue
            meta = selected_meta[pmid]
            try:
                result = self._score(cand)
            except Exception as e:
                self.logger.warning(f"Score PMID {pmid} 失敗：{e}")
                continue
            scored.append(
                {
                    "candidate": cand,
                    "curate_meta": meta,
                    "score_result": result,
                }
            )

        if not scored:
            return f"候選 {len(all_candidates)} 筆，curate/score 後無精選入選"

        # 6. Write vault outputs
        if self.dry_run:
            self.logger.info(f"[dry-run] 模擬寫入 {len(scored)} 篇 source + 1 份 digest")
        else:
            for item in scored:
                self._write_source_page(item)
            digest_path = self._write_digest_page(scored, curated, len(fresh))
            self._append_kb_log(digest_path, len(scored))
            self._update_kb_index(digest_path, len(scored))

        # 7. 標記所有今次處理到的 PMID 為 seen（避免明天再抓到重複；
        #    即便未入選也記，因為已經 curate 過了）
        if not self.dry_run:
            for c in fresh:
                mark_seen(_SOURCE_NAME, c["pmid"], c.get("url"))

        summary = (
            f"fetch={len(all_candidates)} fresh={len(fresh)} "
            f"selected={len(scored)} "
            f"(dry_run={self.dry_run})"
        )
        return summary

    # ------------------------------------------------------------------
    # Fetch & parse
    # ------------------------------------------------------------------

    def _fetch_feed(self, feed_config: dict) -> list[dict]:
        url = feed_config["url"]
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            self.logger.warning(f"Feed 解析失敗：{url} — {parsed.bozo_exception}")
            return []

        items = []
        for entry in parsed.entries:
            item = self._parse_entry(entry, feed_config.get("name", "default"))
            if item:
                items.append(item)
        return items

    @staticmethod
    def _parse_entry(entry: Any, feed_name: str) -> Optional[dict]:
        """把一筆 PubMed RSS entry 解析成 candidate dict。

        PubMed RSS 特徵：
            - entry.id 格式 "pubmed:12345678"，抽 PMID
            - entry.source / entry.dc_source 是期刊名（不同版本 feedparser 命名不同）
            - entry.summary 或 entry.content 是 abstract（含 HTML tag，需清乾淨）
            - entry.author / entry.authors 是作者清單
        """
        # PMID
        guid = getattr(entry, "id", "") or getattr(entry, "guid", "")
        pmid_match = re.match(r"pubmed:(\d+)", guid)
        if not pmid_match:
            return None
        pmid = pmid_match.group(1)

        # Title
        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            return None

        # Journal — PubMed RSS 把期刊名放在 <source>，feedparser 依版本解析為 str 或 dict
        raw_source = getattr(entry, "source", None) or getattr(entry, "dc_source", "")
        if isinstance(raw_source, dict):
            journal = raw_source.get("title") or raw_source.get("value") or ""
        else:
            journal = raw_source or ""
        journal = _clean_journal(journal)

        # Abstract
        content_html = ""
        if hasattr(entry, "content") and entry.content:
            content_html = entry.content[0].get("value", "")
        if not content_html:
            content_html = getattr(entry, "summary", "") or ""
        abstract = _clean_abstract(_strip_html(content_html))

        # Pub date
        pub_date = getattr(entry, "published", "") or getattr(entry, "updated", "")

        # Authors
        authors_field = getattr(entry, "authors", None)
        if authors_field:
            authors = ", ".join(a.get("name", "") for a in authors_field if a.get("name"))
        else:
            authors = getattr(entry, "author", "")

        # PubMed RSS link 帶一堆 utm/fc/ff tracking 參數，用乾淨版本
        clean_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        return {
            "pmid": pmid,
            "title": title,
            "journal": journal,
            "abstract": abstract,
            "pub_date": pub_date,
            "authors": authors,
            "url": clean_url,
            "feed_source": feed_name,
        }

    # ------------------------------------------------------------------
    # LLM: Curate
    # ------------------------------------------------------------------

    def _curate(self, candidates: list[dict]) -> dict:
        """LLM 一次 call：從 N 筆候選挑 10-15 篇。"""
        lines = []
        for c in candidates:
            tier = c.get("journal_tier")
            tier_str = (
                f"{tier['quartile']} SJR={tier['sjr']}"
                if tier and tier.get("quartile")
                else "未知 tier"
            )
            abstract_truncated = c["abstract"][:800]  # 限制長度防暴脹
            lines.append(
                f"\n---\nPMID: {c['pmid']}\n"
                f"Title: {c['title']}\n"
                f"Journal: {c['journal']} ({tier_str})\n"
                f"Published: {c['pub_date']}\n"
                f"Abstract: {abstract_truncated}"
            )

        prompt = load_prompt(
            "robin",
            "pubmed_digest/curate",
            candidates="\n".join(lines),
            total_candidates=str(len(candidates)),
        )
        response = ask_claude(prompt, max_tokens=4096)
        return _parse_json(response)

    # ------------------------------------------------------------------
    # LLM: Score
    # ------------------------------------------------------------------

    def _score(self, paper: dict) -> dict:
        """LLM 單篇深度評分。"""
        tier = paper.get("journal_tier")
        if tier and tier.get("quartile"):
            tier_info = (
                f"{tier['quartile']}, SJR={tier['sjr']}, H-index={tier['h_index']}, "
                f"Categories={tier.get('categories', 'N/A')}"
            )
        else:
            tier_info = "未收錄於 Scimago，請依你對該期刊的知識判斷"

        prompt = load_prompt(
            "robin",
            "pubmed_digest/score",
            pmid=paper["pmid"],
            title=paper["title"],
            journal=paper["journal"],
            journal_tier_info=tier_info,
            pub_date=paper["pub_date"],
            authors=paper["authors"] or "（未提供）",
            abstract=paper["abstract"],
        )
        response = ask_claude(prompt, max_tokens=2048)
        return _parse_json(response)

    # ------------------------------------------------------------------
    # Vault writers
    # ------------------------------------------------------------------

    def _write_source_page(self, item: dict) -> Path:
        """寫單篇 KB/Wiki/Sources/pubmed-{pmid}.md。"""
        cand = item["candidate"]
        curate_meta = item["curate_meta"]
        score = item["score_result"]
        tier = cand.get("journal_tier")

        frontmatter = {
            "pmid": cand["pmid"],
            "title": cand["title"],
            "journal": cand["journal"],
            "journal_quartile": tier["quartile"] if tier else None,
            "journal_sjr": tier["sjr"] if tier else None,
            "published": cand["pub_date"],
            "authors": cand["authors"],
            "url": cand["url"],
            "domain": curate_meta.get("domain"),
            "study_design": score.get("study_design"),
            "scores": score.get("scores"),
            "overall_score": score.get("overall"),
            "editor_pick": score.get("editor_pick"),
            "source": "pubmed_rss",
            "type": "paper_digest",
        }

        body = _render_source_body(cand, curate_meta, score)
        relative_path = f"KB/Wiki/Sources/pubmed-{cand['pmid']}.md"
        return write_page(relative_path, frontmatter, body)

    def _write_digest_page(
        self,
        scored: list[dict],
        curated: dict,
        total_fresh: int,
    ) -> Path:
        """寫 KB/Wiki/Digests/PubMed/YYYY-MM-DD.md。"""
        today = date.today().isoformat()
        summary = curated.get("summary", {})

        # 按 rank 排序（curate 給的）
        rank_of = {s["pmid"]: s.get("rank", 999) for s in curated.get("selected", [])}
        scored_sorted = sorted(scored, key=lambda x: rank_of.get(x["candidate"]["pmid"], 999))

        # editor picks vs others
        editor_picks = [i for i in scored_sorted if i["score_result"].get("editor_pick")]
        others = [i for i in scored_sorted if not i["score_result"].get("editor_pick")]

        domains = sorted({i["curate_meta"].get("domain", "other") for i in scored})

        frontmatter = {
            "date": today,
            "created_by": "robin",
            "source": "pubmed_rss",
            "total_candidates_fresh": total_fresh,
            "selected_count": len(scored),
            "editor_pick_count": len(editor_picks),
            "domains": domains,
            "type": "digest",
        }

        body = _render_digest_body(
            today=today,
            editor_note=summary.get("editor_note", ""),
            total_fresh=total_fresh,
            selected_count=len(scored),
            editor_picks=editor_picks,
            others=others,
        )

        relative_path = f"KB/Wiki/Digests/PubMed/{today}.md"
        return write_page(relative_path, frontmatter, body)

    def _append_kb_log(self, digest_path: Path, count: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"\n- {now} robin: PubMed digest written → `{digest_path.name}` ({count} papers)"
        append_to_file("KB/log.md", line)

    def _update_kb_index(self, digest_path: Path, count: int) -> None:
        """最簡 index 更新：append 一行到 index.md 的『Recent Digests』區塊下方。

        若 index.md 不存在則略過（vault 尚未初始化）。
        """
        today = date.today().isoformat()
        line = f"\n- [[Digests/PubMed/{today}|PubMed {today}]] — {count} 篇精選"
        try:
            append_to_file("KB/index.md", line)
        except FileNotFoundError:
            self.logger.debug("KB/index.md 不存在，略過 index 更新")


# ----------------------------------------------------------------------
# 純函式工具（無副作用）
# ----------------------------------------------------------------------


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """把 PubMed RSS abstract 的 HTML 標籤剝掉，entity 解碼。"""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_abstract(s: str) -> str:
    """去掉 PubMed RSS 塞在前面的 citation header，留下真正的 abstract body。

    典型格式（PubMed RSS 會前置 citation）：
        "Front Endocrinol. ... doi: ... eCollection. ABSTRACT BACKGROUND: ..."
    把「ABSTRACT」前面的 citation 全部剝掉；若沒 ABSTRACT 關鍵字則保留原樣。
    """
    m = re.search(r"\bABSTRACT\b[:\s]*", s)
    if m:
        return s[m.end() :].strip()
    return s


def _clean_journal(s: str) -> str:
    """PubMed RSS 有時把 journal 寫成 "X : official journal of Y" — 取冒號前的主名稱。"""
    if not s:
        return ""
    # 先切 " : " subtitle
    main = s.split(" : ")[0].strip()
    return main


def _parse_json(text: str) -> dict:
    """從 LLM 回應擷取 JSON。容忍外層 ```json``` 包裝或前後閒聊。"""
    text = text.strip()
    # 優先抓第一個 { 到最後一個 } 之間
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"LLM 回應找不到 JSON：{text[:200]}")
    return json.loads(text[start : end + 1])


def _render_source_body(cand: dict, curate_meta: dict, score: dict) -> str:
    """單篇 Source 頁內文（繁體中文）。"""
    scores = score.get("scores", {})
    return f"""# {cand["title"]}

**PMID**: [{cand["pmid"]}]({cand["url"]})
**Journal**: {cand["journal"]}
**Published**: {cand["pub_date"]}
**Authors**: {cand["authors"] or "（未提供）"}
**Domain**: `{curate_meta.get("domain", "other")}`

## 編輯評分（總分 {score.get("overall", "—")}）

| 維度 | 分數 |
|------|------|
| 嚴謹度 Rigor | {scores.get("rigor", "—")} |
| 影響力 Impact | {scores.get("impact", "—")} |
| 臨床關聯 Clinical Relevance | {scores.get("clinical_relevance", "—")} |
| 實用性 Actionability | {scores.get("actionability", "—")} |
| 警訊 Red Flags | {scores.get("red_flags", "—")} |
| 新穎度 Novelty | {scores.get("novelty", "—")} |

**Editor pick**: {"✅" if score.get("editor_pick") else "❌"}

## 一句話結論

{score.get("one_line_verdict", "")}

## 為什麼值得讀

{score.get("why_it_matters", "")}

## 關鍵發現

{score.get("key_finding", "")}

## 方法學註記

- **Study design**: {score.get("study_design", "—")}
- **Sample**: {score.get("sample_size", "—")}

## 警訊 / 限制

{score.get("red_flags_detail", "")}

## 入選理由（curate 階段）

{curate_meta.get("reason", "")}

## 原始 Abstract

> {cand["abstract"]}
"""


def _render_digest_body(
    *,
    today: str,
    editor_note: str,
    total_fresh: int,
    selected_count: int,
    editor_picks: list[dict],
    others: list[dict],
) -> str:
    """每日 digest 頁 body。"""
    lines = [
        f"# PubMed 每日精選 — {today}",
        "",
        f"> {editor_note}" if editor_note else "",
        "",
        f"**候選總數**：{total_fresh}　**入選**：{selected_count}　"
        f"**Editor's picks**：{len(editor_picks)}",
        "",
    ]

    if editor_picks:
        lines.append("## ⭐ Editor's Picks")
        lines.append("")
        for i, item in enumerate(editor_picks, 1):
            lines.extend(_render_digest_entry(i, item))

    if others:
        lines.append("## 其他精選")
        lines.append("")
        start_idx = len(editor_picks) + 1
        for i, item in enumerate(others, start_idx):
            lines.extend(_render_digest_entry(i, item))

    return "\n".join(lines)


def _render_digest_entry(rank: int, item: dict) -> list[str]:
    cand = item["candidate"]
    curate_meta = item["curate_meta"]
    score = item["score_result"]
    tier = cand.get("journal_tier")
    tier_label = (
        f"{tier['quartile']} · SJR {tier['sjr']}"
        if tier and tier.get("quartile")
        else "未收錄 Scimago"
    )

    return [
        f"### {rank}. {cand['title']}",
        "",
        f"- **Journal**: {cand['journal']} ({tier_label})",
        f"- **Domain**: `{curate_meta.get('domain', 'other')}`",
        f"- **Score**: {score.get('overall', '—')}  "
        f"(R{score.get('scores', {}).get('rigor', '—')}/"
        f"I{score.get('scores', {}).get('impact', '—')}/"
        f"C{score.get('scores', {}).get('clinical_relevance', '—')}/"
        f"A{score.get('scores', {}).get('actionability', '—')}/"
        f"F{score.get('scores', {}).get('red_flags', '—')}/"
        f"N{score.get('scores', {}).get('novelty', '—')})",
        f"- **Verdict**: {score.get('one_line_verdict', '')}",
        f"- **Why**: {score.get('why_it_matters', '')}",
        f"- **→** [[pubmed-{cand['pmid']}]] · [PubMed]({cand['url']})",
        "",
    ]
