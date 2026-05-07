"""Franky weekly synthesis — two-stage proposal inbox（ADR-023 §1 Weekly + §7 S3）。

每週日 22:00 台北 cron 觸發：
  Input:  7 天 picked items（從 KB/Wiki/Digests/AI/YYYY-MM-DD.md 讀取）
          + franky_context_snapshot（pre-RAG，S2a 產物）
  LLM:    pattern detection 三類（trend / adr_invalidation / issue_match）
  Output: 0-3 條 candidate proposal

Two-stage inbox：
  Stage 1（無條件）: 所有 candidates 寫進 KB/Wiki/Digests/AI/Weekly-YYYY-WW.md
                     + 寫進 proposal_metrics（status=candidate）
  Stage 2（條件）:   promote=true OR ≥2-item rule 命中 → gh issue create
                     + mark_promoted in proposal_metrics

Subcommand modes（agents/franky/__main__.py 端 dispatch）：
  python -m agents.franky synthesis              # full path
  python -m agents.franky synthesis --dry-run    # 不寫 vault、不插 DB、不送 Slack
  python -m agents.franky synthesis --no-publish # 寫 vault + DB 但不送 Slack DM
  python -m agents.franky synthesis --re-scan-promotions  # 掃現有 Weekly page promote=true
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from agents.base import BaseAgent
from agents.franky.state.proposal_metrics import (
    ProposalNotFoundError,
    insert_candidate,
    mark_promoted,
)
from agents.franky.state.proposal_metrics import (
    get as get_proposal,
)
from shared import llm
from shared.obsidian_writer import append_to_file, read_page, write_page
from shared.prompt_loader import load_prompt
from shared.schemas.proposal_metrics import ProposalFrontmatterV1

_TAIPEI = ZoneInfo("Asia/Taipei")
_SNAPSHOT_PATH = Path(__file__).parent / "state" / "franky_context_snapshot.md"
_WEEKLY_DIGEST_DIR = "KB/Wiki/Digests/AI"


# ---------------------------------------------------------------------------
# Panel recommended deterministic positive list (ADR-023 §5)
# ---------------------------------------------------------------------------

# Patterns that trigger panel_recommended=True regardless of LLM judgment.
# Each is a compiled regex tested against the full candidate text (proposal_id +
# title + description + related_adr + related_issues joined).
_PANEL_TRIGGERS: list[re.Pattern] = [
    # 1. Mentions accepted ADR number
    re.compile(r"\bADR-\d+\b"),
    # 2. Involves changing agent public contract (agents/<name>/__main__ or shared API)
    re.compile(r"\bagents/[a-z]+/__main__\b|\bshared\s+API\b", re.IGNORECASE),
    # 3. Introduces new persistent dependency (pyproject / requirements)
    re.compile(r"\bpyproject\.toml\b|\brequirements\.txt\b", re.IGNORECASE),
    # 4. Changes storage schema (state.db migration)
    re.compile(r"\bstate\.db\b|\bmigration\b", re.IGNORECASE),
    # 5. Changes HITL boundary (ADR-006 approval queue behaviour)
    re.compile(r"\bADR-006\b|\bHITL\b|\bapproval queue\b", re.IGNORECASE),
    # 6. Changes Slack/GitHub automation permissions
    re.compile(
        r"\bSlack\s+permission\b|\bGitHub\s+(Actions|automation|permission)\b",
        re.IGNORECASE,
    ),
]


def _panel_trigger_text(cand: dict[str, Any]) -> str:
    """Build the searchable text blob for panel_recommended detection."""
    parts = [
        cand.get("proposal_id", ""),
        cand.get("title", ""),
        cand.get("description", ""),
        " ".join(cand.get("related_adr", [])),
        cand.get("direct_adr_mapping", "") or "",
    ]
    return " ".join(parts)


def _apply_panel_recommended(cand: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `cand` with `panel_recommended` bool + preserved reasons.

    List-triggered = always True. LLM-supplied reasons in panel_recommended_reasons
    are preserved in all cases.
    """
    text = _panel_trigger_text(cand)
    list_triggered = any(p.search(text) for p in _PANEL_TRIGGERS)

    out = dict(cand)
    out["panel_recommended"] = list_triggered
    # Preserve any LLM-supplied reasons; do not lose them.
    out.setdefault("panel_recommended_reasons", [])
    return out


# ---------------------------------------------------------------------------
# Hard quality gate (ADR-023 §7 S3)
# ---------------------------------------------------------------------------


def _passes_quality_gate(cand: dict[str, Any]) -> bool:
    """Return True if candidate passes the hard gate before Stage 2 issue creation.

    Passes if:
    (a) len(supporting_item_ids) >= 2, OR
    (b) direct_issue_mapping is set (non-null, non-empty), OR
    (c) direct_adr_mapping is set (non-null, non-empty).
    """
    items = cand.get("supporting_item_ids") or []
    if len(items) >= 2:
        return True
    direct_issue = cand.get("direct_issue_mapping")
    if direct_issue:
        return True
    direct_adr = cand.get("direct_adr_mapping")
    if direct_adr:
        return True
    return False


# ---------------------------------------------------------------------------
# Collect 7-day picks from vault
# ---------------------------------------------------------------------------

# Match `## N. Title` heading in a daily digest page.
_SECTION_RE = re.compile(
    r"^## (\d+)\.\s+(.+?)$",
    re.MULTILINE,
)
# Extract Publisher line
_PUBLISHER_RE = re.compile(r"^\s*-\s+\*\*Publisher\*\*:\s*(.+)$", re.MULTILINE)
# Extract URL from the → line
_URL_RE = re.compile(r"^\s*-\s+\*\*→\*\*\s+\[([^\]]+)\]", re.MULTILINE)
# Extract Verdict line
_VERDICT_RE = re.compile(r"^\s*-\s+\*\*Verdict\*\*:\s*(.+)$", re.MULTILINE)
# Extract Why line
_WHY_RE = re.compile(r"^\s*-\s+\*\*Why\*\*:\s*(.+)$", re.MULTILINE)


def _parse_digest_page(text: str, *, date: str) -> list[dict[str, Any]]:
    """Extract picked items from a daily digest vault page.

    Returns list of dicts with item_id=YYYY-MM-DD-{rank}.
    item_id is synthetic (the original RSS GUID is not stored in the vault page).
    """
    if not text:
        return []

    sections = _SECTION_RE.findall(text)
    if not sections:
        return []

    items: list[dict[str, Any]] = []
    # Split body into per-item sections for targeted extraction.
    positions = [(m.start(), m.group(1), m.group(2)) for m in _SECTION_RE.finditer(text)]

    for idx, (pos, rank_str, title) in enumerate(positions):
        rank = int(rank_str)
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        chunk = text[pos:end]

        pub_m = _PUBLISHER_RE.search(chunk)
        url_m = _URL_RE.search(chunk)
        verdict_m = _VERDICT_RE.search(chunk)
        why_m = _WHY_RE.search(chunk)

        items.append(
            {
                "item_id": f"{date}-{rank}",
                "date": date,
                "rank": rank,
                "title": title.strip(),
                "publisher": pub_m.group(1).strip() if pub_m else "",
                "url": url_m.group(1).strip() if url_m else "",
                "verdict": verdict_m.group(1).strip() if verdict_m else "",
                "why": why_m.group(1).strip() if why_m else "",
            }
        )
    return items


def _collect_seven_day_picks(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Read up to 7 daily digest pages and return all picked items.

    Pages are from KB/Wiki/Digests/AI/YYYY-MM-DD.md for the 7 most recent days.
    Missing pages are silently skipped (no digest that day).
    """
    if now is None:
        now = datetime.now(tz=_TAIPEI)
    items: list[dict[str, Any]] = []
    for offset in range(7):
        day = (now - timedelta(days=offset)).date()
        date_str = day.isoformat()
        page_path = f"{_WEEKLY_DIGEST_DIR}/{date_str}.md"
        text = read_page(page_path)
        if text:
            items.extend(_parse_digest_page(text, date=date_str))
    return items


# ---------------------------------------------------------------------------
# Context snapshot loader
# ---------------------------------------------------------------------------


def _load_context_snapshot(path: Path = _SNAPSHOT_PATH) -> str:
    """Load pre-RAG context snapshot. Returns empty string if file absent."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ISO week helpers
# ---------------------------------------------------------------------------


def _iso_week_tag(dt: datetime) -> str:
    """Return ISO week string in lowercase: 2026-w18 (not W18, to satisfy proposal_id regex)."""
    iso = dt.isocalendar()
    return f"{iso.year}-w{iso.week:02d}"


def _iso_week_display(dt: datetime) -> str:
    """Return display ISO week string: 2026-W18 (for vault page filename)."""
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# ---------------------------------------------------------------------------
# LLM synthesis call
# ---------------------------------------------------------------------------


def _new_op_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"


def _parse_json(text: str) -> dict:
    """Extract JSON object from LLM response (tolerates markdown fence / prose wrapper)."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"LLM 回應找不到 JSON：{text[:200]}")
    return json.loads(text[start : end + 1])


def _build_picks_summary(picks: list[dict[str, Any]]) -> str:
    """Format picks for LLM prompt injection."""
    if not picks:
        return "（本週無精選）"
    lines: list[str] = []
    current_date = None
    for p in picks:
        if p["date"] != current_date:
            current_date = p["date"]
            lines.append(f"\n### {current_date}")
        lines.append(
            f"- [{p['item_id']}] **{p['title']}** ({p['publisher']})"
            + (f"\n  Verdict: {p['verdict']}" if p["verdict"] else "")
            + (f"\n  Why: {p['why']}" if p["why"] else "")
            + (f"\n  URL: {p['url']}" if p["url"] else "")
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GH issue creation
# ---------------------------------------------------------------------------


def _create_gh_issue(
    cand: dict[str, Any],
    *,
    week_display: str,
    vault_page_path: str,
) -> int | None:
    """Create a GitHub issue for a promoted candidate. Returns the issue number or None."""
    title = f"[Franky proposal] {cand['title']}"

    # Build YAML frontmatter block for issue body
    fm_lines = [
        "```yaml frontmatter",
        f"proposal_id: {cand['proposal_id']}",
        f"metric_type: {cand['metric_type']}",
        f'success_metric: "{cand["success_metric"]}"',
        f"related_adr: {json.dumps(cand.get('related_adr') or [])}",
        f"related_issues: {json.dumps(cand.get('related_issues') or [])}",
        f"panel_recommended: {'true' if cand.get('panel_recommended') else 'false'}",
        "promote: true",
        "```",
    ]

    body_lines = [
        *fm_lines,
        "",
        f"**Weekly synthesis**: {week_display}",
        f"**Pattern type**: {cand.get('pattern_type', '')}",
        f"**Vault page**: {vault_page_path}",
        "",
        "## Description",
        "",
        cand.get("description", ""),
        "",
        "## Success metric",
        "",
        cand.get("success_metric", ""),
        "",
        "## Supporting items",
        "",
        *[f"- `{iid}`" for iid in (cand.get("supporting_item_ids") or [])],
    ]

    if cand.get("try_cost_estimate"):
        body_lines += ["", f"**Try cost estimate**: {cand['try_cost_estimate']}"]
    if cand.get("panel_recommended_reasons"):
        body_lines += [
            "",
            "## Panel recommended reasons",
            "",
            *[f"- {r}" for r in cand["panel_recommended_reasons"]],
        ]

    body = "\n".join(body_lines)

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--label",
                "franky-proposal,needs-triage",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"gh CLI unavailable: {exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(f"gh issue create failed: {result.stderr.strip()}")

    # Parse issue number from URL output (e.g. https://github.com/user/repo/issues/123)
    url = result.stdout.strip()
    m = re.search(r"/issues/(\d+)$", url)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Vault page renderer
# ---------------------------------------------------------------------------


def _render_weekly_page(
    *,
    week_display: str,
    candidates: list[dict[str, Any]],
    op_id: str,
    picks_count: int,
) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_markdown) for the weekly vault page."""
    # Build frontmatter — all candidates share one page.
    # The page-level frontmatter holds synthesis metadata; per-candidate
    # frontmatter is embedded inside the page body as fenced YAML blocks.
    frontmatter: dict[str, Any] = {
        "week_iso": week_display,
        "created_by": "franky",
        "operation_id": op_id,
        "picks_count": picks_count,
        "candidate_count": len(candidates),
        "type": "weekly_synthesis",
    }

    lines = [
        f"# Franky Weekly Synthesis — {week_display}",
        "",
        f"候選來源：{picks_count} 條 7 天精選 / {len(candidates)} 條 proposal candidates",
        "",
        "---",
        "",
    ]
    for idx, cand in enumerate(candidates, 1):
        lines += [
            f"## Candidate {idx}: {cand['title']}",
            "",
            "```yaml frontmatter",
            f"proposal_id: {cand['proposal_id']}",
            f"metric_type: {cand['metric_type']}",
            f'success_metric: "{cand["success_metric"]}"',
            f"related_adr: {json.dumps(cand.get('related_adr') or [])}",
            f"related_issues: {json.dumps(cand.get('related_issues') or [])}",
            f"panel_recommended: {'true' if cand.get('panel_recommended') else 'false'}",
            "promote: false",
            "```",
            "",
            f"**Pattern type**: `{cand.get('pattern_type', '')}`",
            f"**Try cost estimate**: {cand.get('try_cost_estimate', '')}",
            "",
            cand.get("description", ""),
            "",
            "**Supporting items**:",
            *[f"- `{iid}`" for iid in (cand.get("supporting_item_ids") or [])],
        ]
        if cand.get("direct_issue_mapping"):
            lines.append(f"**Direct issue mapping**: {cand['direct_issue_mapping']}")
        if cand.get("direct_adr_mapping"):
            lines.append(f"**Direct ADR mapping**: {cand['direct_adr_mapping']}")
        if cand.get("panel_recommended_reasons"):
            lines += [
                "",
                "**Panel recommended reasons**:",
                *[f"- {r}" for r in cand["panel_recommended_reasons"]],
            ]
        lines += ["", "---", ""]

    return frontmatter, "\n".join(lines)


# ---------------------------------------------------------------------------
# Re-scan promotions (--re-scan-promotions flow)
# ---------------------------------------------------------------------------


def _extract_promote_flag(text: str) -> bool:
    """Return True if the page contains `promote: true` in its fenced YAML blocks."""
    # Match `promote: true` in either leading YAML block or fenced blocks.
    return bool(re.search(r"^promote:\s+true\s*$", text, re.MULTILINE))


def _re_scan_and_promote_page(page_path: str) -> None:
    """Read a weekly vault page, extract any promote=true candidates, create issues.

    Used by --re-scan-promotions CLI flag. Operates on a single page path.
    """
    path = Path(page_path)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if not _extract_promote_flag(text):
        return

    # Extract the page-level frontmatter to get proposal_id (leading --- block).
    # We look for fenced frontmatter blocks (one per candidate) and promote each.
    # Pattern: ```yaml frontmatter\n...\nproposal_id: ...\n...\npromote: true\n```
    fenced_pattern = re.compile(
        r"```ya?ml\s+frontmatter\s*\n(?P<body>.*?)\n```",
        re.DOTALL | re.IGNORECASE,
    )
    for m in fenced_pattern.finditer(text):
        block = m.group("body")
        try:
            parsed = yaml.safe_load(block) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        promote = parsed.get("promote", False)
        if not promote:
            continue
        proposal_id = parsed.get("proposal_id")
        if not proposal_id:
            continue

        # Build a minimal candidate dict for issue creation.
        cand: dict[str, Any] = {
            "proposal_id": proposal_id,
            "title": proposal_id,  # best we can do without full parse
            "metric_type": parsed.get("metric_type", "checklist"),
            "success_metric": parsed.get("success_metric", ""),
            "related_adr": parsed.get("related_adr") or [],
            "related_issues": parsed.get("related_issues") or [],
            "panel_recommended": parsed.get("panel_recommended", False),
            "description": "",
            "supporting_item_ids": [],
            "panel_recommended_reasons": [],
            "try_cost_estimate": "",
            "pattern_type": "",
        }
        # Idempotency guard: skip rows already past `candidate` so re-running
        # `--re-scan-promotions` (e.g. after a transient GH outage) does not
        # spam duplicate issues for proposals already promoted.
        try:
            existing = get_proposal(proposal_id)
        except ProposalNotFoundError:
            existing = None
        if existing is not None and existing.get("status") != "candidate":
            continue
        try:
            issue_number = _create_gh_issue(
                cand,
                week_display=path.stem,  # e.g. "Weekly-2026-W18"
                vault_page_path=str(page_path),
            )
            mark_promoted(proposal_id, issue_number=issue_number)
        except Exception:
            pass  # Non-fatal; human can retry


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


class NewsSynthesisPipeline(BaseAgent):
    """Franky weekly synthesis 子流程（ADR-023 §7 S3）。"""

    name = "franky"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        no_publish: bool = False,
        slack_bot: Any | None = None,
    ) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.no_publish = no_publish
        self._slack_bot_override = slack_bot
        self.operation_id = _new_op_id()

    # ------------------------------------------------------------------

    def run(self) -> str:
        now = datetime.now(tz=_TAIPEI)
        week_tag = _iso_week_tag(now)  # e.g. 2026-w18
        week_display = _iso_week_display(now)  # e.g. 2026-W18

        # 1. Collect 7-day picks
        picks = _collect_seven_day_picks(now=now)
        if not picks:
            return f"7 天無精選，略過 synthesis（week={week_display}）"

        # 2. Load context snapshot
        context_snapshot = _load_context_snapshot()

        # 3. LLM synthesis call
        picks_summary = _build_picks_summary(picks)
        try:
            candidates = self._run_synthesis_llm(picks_summary, context_snapshot, week_tag)
        except Exception as e:
            self.logger.error(f"synthesis LLM 失敗：{e}", exc_info=True)
            return f"synthesis LLM 失敗：{e}"

        # 4. Apply panel_recommended deterministic list to each candidate
        candidates = [_apply_panel_recommended(c) for c in candidates]

        stage2_count = 0
        stage1_count = len(candidates)
        vault_page_path: str = ""

        if not self.dry_run:
            # 5. Stage 1: write vault page (unconditional)
            vault_relpath = f"{_WEEKLY_DIGEST_DIR}/Weekly-{week_display}.md"
            vault_page_path = vault_relpath
            frontmatter, body = _render_weekly_page(
                week_display=week_display,
                candidates=candidates,
                op_id=self.operation_id,
                picks_count=len(picks),
            )
            write_page(vault_relpath, frontmatter, body)
            self._append_kb_log(vault_relpath, stage1_count)

            # 6. Insert proposal_metrics rows (status=candidate) + Stage 2 conditional
            for cand in candidates:
                self._insert_and_maybe_promote(
                    cand,
                    week_tag=week_tag,
                    week_display=week_display,
                    vault_page_path=vault_page_path,
                    stage2_counter=lambda: None,
                )
                if _passes_quality_gate(cand):
                    stage2_count += 1

        # 7. Slack DM summary
        slack_ts: str | None = None
        if not self.dry_run and not self.no_publish:
            slack_ts = self._send_slack_summary(
                week_display=week_display,
                stage1_count=stage1_count,
                stage2_count=stage2_count,
                vault_relpath=vault_page_path,
            )

        return (
            f"week={week_display} picks={len(picks)} candidates={stage1_count} "
            f"stage2_issued={stage2_count} vault={vault_page_path or '(skipped)'} "
            f"slack_ts={slack_ts or '(skipped)'} op={self.operation_id} "
            f"(dry_run={self.dry_run} no_publish={self.no_publish})"
        )

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def _run_synthesis_llm(
        self,
        picks_summary: str,
        context_snapshot: str,
        week_tag: str,
    ) -> list[dict[str, Any]]:
        """One LLM call → 0-3 candidate proposals."""
        prompt = load_prompt(
            "franky",
            "news_synthesis",
            picks_summary=picks_summary,
            context_snapshot=context_snapshot or "（未找到 context snapshot）",
        )
        raw = llm.ask(prompt, max_tokens=4096)
        parsed = _parse_json(raw)
        candidates = parsed.get("candidates") or []
        self.logger.info(f"synthesis LLM returned {len(candidates)} candidates (week={week_tag})")
        return candidates

    # ------------------------------------------------------------------
    # Stage 1 + 2 logic
    # ------------------------------------------------------------------

    def _insert_and_maybe_promote(
        self,
        cand: dict[str, Any],
        *,
        week_tag: str,
        week_display: str,
        vault_page_path: str,
        stage2_counter: Any,
    ) -> None:
        """Insert proposal_metrics row + conditionally open GH issue (Stage 2)."""
        try:
            fm = ProposalFrontmatterV1(
                proposal_id=cand["proposal_id"],
                metric_type=cand["metric_type"],
                success_metric=cand["success_metric"],
                related_adr=cand.get("related_adr") or [],
                related_issues=cand.get("related_issues") or [],
            )
            insert_candidate(
                fm,
                week_iso=week_tag,
                panel_recommended=bool(cand.get("panel_recommended")),
                try_cost_estimate=cand.get("try_cost_estimate"),
                source_item_ids=cand.get("supporting_item_ids") or [],
            )
        except Exception as e:
            self.logger.warning(f"insert_candidate failed for {cand.get('proposal_id')}: {e}")
            return

        # Stage 2: ≥2-item rule fires unconditionally if quality gate passes
        if _passes_quality_gate(cand):
            self._create_issue_and_promote(
                cand,
                week_display=week_display,
                vault_page_path=vault_page_path,
            )

    def _create_issue_and_promote(
        self,
        cand: dict[str, Any],
        *,
        week_display: str,
        vault_page_path: str,
    ) -> None:
        """Open GH issue + mark_promoted in proposal_metrics."""
        try:
            issue_number = _create_gh_issue(
                cand,
                week_display=week_display,
                vault_page_path=vault_page_path,
            )
            mark_promoted(cand["proposal_id"], issue_number=issue_number)
            self.logger.info(f"Stage 2: opened issue #{issue_number} for {cand['proposal_id']}")
        except Exception as e:
            self.logger.error(
                f"Stage 2 issue create failed for {cand.get('proposal_id')}: {e}",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Vault log
    # ------------------------------------------------------------------

    def _append_kb_log(self, relpath: str, count: int) -> None:
        try:
            from datetime import date

            today = date.today().isoformat()
            append_to_file(
                "KB/log.md",
                f"- {today}: Franky weekly synthesis → [{relpath}]({relpath}) "
                f"({count} candidates)\n",
            )
        except Exception as e:
            self.logger.debug(f"KB/log.md append failed: {e}")

    # ------------------------------------------------------------------
    # Slack DM
    # ------------------------------------------------------------------

    def _send_slack_summary(
        self,
        *,
        week_display: str,
        stage1_count: int,
        stage2_count: int,
        vault_relpath: str,
    ) -> str | None:
        bot = self._slack_bot_override
        if bot is None:
            from agents.franky.slack_bot import FrankySlackBot

            bot = FrankySlackBot.from_env()

        text = "\n".join(
            [
                f"Franky Weekly Synthesis — {week_display}",
                f"Stage 1 candidates: {stage1_count}",
                f"Stage 2 issued: {stage2_count}",
                f"vault: {vault_relpath}",
                f"op={self.operation_id}",
            ]
        )
        return bot.post_plain(text, context="news_synthesis")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def run_synthesis(*, dry_run: bool = False, no_publish: bool = False) -> str:
    """Run weekly synthesis. Returns a summary string."""
    pipeline = NewsSynthesisPipeline(dry_run=dry_run, no_publish=no_publish)
    if dry_run:
        return pipeline.run()
    pipeline.execute()
    return f"op={pipeline.operation_id}"
