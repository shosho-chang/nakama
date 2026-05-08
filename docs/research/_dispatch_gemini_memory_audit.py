"""Gemini panel audit dispatch for Memory System Redesign v1.

Multi-agent panel step 3 of 5:
  1. Claude drafted 2026-05-08-memory-system-redesign-v1.md
  2. Codex (GPT-5) audit at 2026-05-08-codex-memory-redesign-audit.md
  3. Gemini multilingual / multi-agent / different-prior lens audit ← THIS SCRIPT
  4. Claude integrates 3-way audit
  5. User final sign-off

Run from repo root or from this directory:
  E:/nakama-design/.venv/Scripts/python.exe E:/nakama-design/docs/research/_dispatch_gemini_memory_audit.py > E:/nakama-design/docs/research/2026-05-08-gemini-memory-redesign-audit.md

Falls back to direct google-generativeai if shared.gemini_client unavailable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path("E:/nakama-design")  # design worktree
ARTIFACT_PATH = REPO_ROOT / "docs/research/2026-05-08-memory-system-redesign-v1.md"
CODEX_AUDIT_PATH = REPO_ROOT / "docs/research/2026-05-08-codex-memory-redesign-audit.md"
TOPIC = "Memory System Redesign"

# Add nakama main repo to sys.path so we can import shared.gemini_client
sys.path.insert(0, str(Path("E:/nakama")))


def _load_env():
    try:
        from dotenv import load_dotenv  # type: ignore
        # .env lives in main repo, not design worktree
        load_dotenv(Path("E:/nakama/.env"))
    except ImportError:
        pass


def _ask_via_project_client(prompt: str, system: str) -> str:
    from shared.gemini_client import ask_gemini  # type: ignore
    return ask_gemini(
        prompt,
        system=system,
        model="gemini-2.5-pro",
        max_tokens=8192,
        thinking_budget=2048,
        temperature=0.3,
    )


def _ask_via_genai(prompt: str, system: str) -> str:
    import google.generativeai as genai  # type: ignore
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not in env. Source E:/nakama/.env first.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-pro",
        system_instruction=system,
        generation_config={"max_output_tokens": 8192, "temperature": 0.3},
    )
    response = model.generate_content(prompt)
    return response.text


def ask_gemini(prompt: str, system: str) -> str:
    try:
        return _ask_via_project_client(prompt, system)
    except (ImportError, ModuleNotFoundError) as e:
        print(f"# project gemini_client unavailable ({e}), falling back to genai SDK", file=sys.stderr)
        return _ask_via_genai(prompt, system)


SYSTEM = (
    "You are an independent third-party auditor on a multi-agent panel review. "
    "The artifact is a memory-system redesign for a multi-agent (Claude + Codex), "
    "multi-platform (Win + Mac + cloud Sandcastle) AI development workflow. "
    "The owner has explicitly asked for push-back from your unique perspective as Gemini — "
    "do NOT rubber-stamp Claude's draft or Codex's audit. "
    "Your value is your different viewpoint: stronger multilingual recall, "
    "different reasoning chain than Claude or GPT-5, and willingness to challenge "
    "framing both other models share. Be concrete, cite specifics, disagree where appropriate."
)


def build_prompt(artifact: str, codex_audit: str) -> str:
    return f"""# Memory System Redesign Audit — Multi-Agent Panel Step 3 (Gemini)

You are the THIRD reviewer. Claude drafted a memory-system redesign for the `nakama` repo (Health & Wellness AI agent system, solo developer 修修, multi-window Claude Code, Codex coming online). Codex (GPT-5) already audited it. Now you audit both — adding a Gemini-specific perspective.

## Background

The `nakama` repo currently has 297 markdown files in `memory/claude/` (155 feedback, 114 project, 4 user, 23 reference), with no maintenance / TTL / dedup mechanism. A separate SQLite-backed memory system (in `shared/memory_maintenance.py`) exists but only for the OLD schema in `memory/shared.md` and `memory/agents/{{robin,franky}}.md` — the NEW file-based memory has no backend.

Constraints:
- C1 cross-platform durability (Win + Mac + cloud Sandcastle) — repo storage non-negotiable
- C2 multi-agent: Codex coming online as a second collaborating agent
- C3 Sandcastle parallel sub-agents committing back via git
- C4 solo developer (no second human reviewer)
- C5 signal-to-noise (155 feedback files is search-degrading)
- C6 backward compat (no big-bang migration of 297 existing files)

The trigger pattern that initiated this redesign: the user's existing feedback `feedback_conversation_end.md` instructs Claude that "清對話" (clear conversation) should auto-write important memories + commit + push. This produced ~9 memory commits/day, all going through PR + CI, contributing to GHA Actions quota at 90% used.

The proposed redesign:
- Memory ≠ session log (durable knowledge only)
- Append-only files, generated MEMORY.md index (no hand edits)
- Memory commits bypass PR via dedicated `memory-trunk` branch
- Cross-agent layout: `memory/{{shared,claude,codex,_archive}}/`
- Schema upgrade: add `visibility/confidence/created/expires/tags` (additive, backward-compat)
- Trigger reform: L3 forbids auto-write on "清對話" (confirm-mode instead)

## What you should produce

A 1500-2500 word audit in 6 sections. Focus on what's UNIQUELY valuable from Gemini perspective. Where you agree with Claude or Codex, acknowledge briefly and move on. Where you disagree or have additional insight, dig in.

### Section 1 — MULTILINGUAL & i18n CONSIDERATIONS

The repo uses 繁體中文 + English mixed. Memory frontmatter `name`/`description` are often Chinese (e.g. `name: 對話結束時自動存記憶`). The codebase has documented user feedback that "agent reads English-language `feedback_*.md` and applies it to a Chinese conversation" — does the proposal handle bilingual memory retrieval, especially when:
- File paths and frontmatter keys are English
- Body content is Chinese
- The proposal mentions `memory/shared/decision/` overlap with `docs/decisions/` (English ADRs)
- Codex (English-trained) may match Chinese feedback poorly even with same path access

Push back if the cross-agent layout glosses over how a model "subscribes" to memory in a different language than it primarily reasons in.

### Section 2 — DIFFERENT PRIOR

Where does your training prior differ from Claude's or GPT-5's? Specifically on:
- File-based knowledge bases vs database-backed memory
- Convention-driven multi-agent coordination vs protocol-driven (e.g. MCP)
- Solo-developer documentation vs team workflows
- Long-tail handoff memos as signal vs noise

Cite where the proposal makes assumptions that match Claude's prior but where another reasoning chain would reach a different conclusion.

### Section 3 — CLAUDE / CODEX BLIND SPOTS

Where do Claude and Codex likely share the same bias? What did both miss? Use specific evidence from the proposal text or Codex audit (which you'll receive verbatim below). Examples worth checking:
- Both Claude and Codex are trained heavily on Western dev workflows; they may miss East Asian solo-dev patterns
- Both have similar "doc-driven design" defaults that may overweight written rules over enforced tooling
- Both default to "respect existing structure" which may anchor too hard on old `memory/claude/` layout

### Section 4 — MIGRATION RISK & DEPENDENCY GRAPH

Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 is a 4-phase migration over 1-2 months. What's the dependency graph? Where can phases run in parallel? What rollback story exists if Phase 1 (`memory_maintenance.py` extension) breaks? What happens if user gets pulled away mid-migration and old + new layouts coexist for 6 months?

### Section 5 — ARCHITECTURAL CONCERNS

Big-picture issues escaping line-level audit. Particularly:
- Is `memory-trunk` branch model isomorphic to git submodule (and submodule is a known-painful pattern)?
- Does the proposal lock in markdown-as-database when the actual access pattern is search/retrieve (where SQLite-of-record + git-as-sync would be cleaner)?
- Multi-agent shared memory inherently has a CRDT-like conflict-resolution problem; is "append-only files + reindex" really sufficient, or is this papering over a deeper sync challenge?

### Section 6 — FINAL VERDICT

- Approve / approve with modifications / reject
- If modifications: top 3-5 specific changes, prioritized
- Cite proposal section names verbatim; cite Codex audit by section number
- Be specific on whether Phase 0 should ship as-drafted or be split

## Required style

- English (matches Codex audit, helps integration)
- Concrete and specific. Push back where you disagree. Avoid hedging language.
- This will be read directly by the owner (修修) for the final call.

## Codex (GPT-5) audit verbatim

{codex_audit}

## {TOPIC} content under review

{artifact}

---

Begin your 6-section Gemini audit now."""


def main() -> int:
    _load_env()

    artifact = ARTIFACT_PATH.read_text(encoding="utf-8")

    if not CODEX_AUDIT_PATH.exists():
        print(f"# ERROR: Codex audit not yet at {CODEX_AUDIT_PATH}", file=sys.stderr)
        print(f"# Wait for Codex agent to complete, then re-run.", file=sys.stderr)
        return 1

    codex_audit = CODEX_AUDIT_PATH.read_text(encoding="utf-8")

    prompt = build_prompt(artifact, codex_audit)

    print("=== Gemini panel audit dispatch ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Artifact: {ARTIFACT_PATH.name} ({len(artifact)} chars)", file=sys.stderr)
    print(f"Codex audit: {CODEX_AUDIT_PATH.name} ({len(codex_audit)} chars)", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt)//4} tokens)", file=sys.stderr)
    print(f"Model: gemini-2.5-pro", file=sys.stderr)
    print(f"---", file=sys.stderr)

    response = ask_gemini(prompt, system=SYSTEM)
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
