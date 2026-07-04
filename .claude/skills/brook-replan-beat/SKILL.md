---
name: brook-replan-beat
description: >
  Thin conversation wrapper for single-beat re-plan during interactive storyboard
  review. Triggers on /brook-replan-beat (legacy alias /foundry-replan) or
  "重 plan beat <N>" style requests.
  Loads agents/brook/script_video/prompts/broll_planner.md as canonical prompt, runs LLM
  on a single beat with user-supplied note, returns new proposal for inline
  approval. Canonical pipeline logic is in agents/brook/script_video/ (Python); this skill
  is escape hatch only.
---

# brook-replan-beat — single-beat re-plan helper（原 foundry-replan，ADR-050 改名）

You are an interactive surface for re-planning a single storyboard beat during conversation review. The canonical broll pipeline lives in `agents/brook/script_video/` (Python). This skill is **escape hatch only** — Brook's UI normally handles re-plans via `/brook/video/<episode>` Bridge route + endpoint. Use this skill when the user wants to iterate on a beat in conversation rather than via UI.

## When to use

Trigger on:
- `/brook-replan-beat` slash invocation（legacy alias：`/foundry-replan`）
- 「重 plan beat 12」「這個 beat 換個 layout 試試」
- 「video line 的 beat 7 我想 calmer」（舊稱 foundry 也觸發）

Do NOT trigger for:
- Bulk replanning (use UI batch actions)
- Initial storyboard generation (use `python -m agents.brook.script_video plan --episode <id>`)
- Render-related questions (separate concern)

## Workflow

1. **Locate context** — ask user for `episode_id` and `beat_id` if not given. Read `data/script_video/<episode_id>/storyboard.yaml` to find the target beat.

2. **Load canonical prompt** — `agents/brook/script_video/prompts/broll_planner.md` (the planner's normal prompt template, PR-3 implements). Load `agents/brook/script_video/STYLE.md` editorial rubric. Load `agents/brook/script_video/guardrails.yaml`.

3. **Confirm scope** — show user the current beat YAML + their requested change. Confirm: "re-plan with note: '<user_note>' — proceed?"

4. **Run single-beat LLM call** — use the standard prompt template with the single beat slice (not the whole transcript). Output must follow the same anchor exact-copy contract.

5. **Validate output** — run `beat_aligner.align_beat()` against current normalized flat_text. On `AnchorNotFoundError`, retry up to 3 times. Else escalate.

6. **Preview new beat** — show side-by-side: before vs after. Ask user to confirm.

7. **Write back** — if confirmed:
   - Update the single beat in `storyboard.yaml`
   - Append entry to `agents/brook/script_video/edit_log/<episode>.jsonl` with `{beat_id, before, after, user_note}`
   - Set `status.text_approved = false` (user must re-approve via UI)

## Rules

- **Single beat only** — if user asks for bulk, redirect them to Bridge UI batch actions
- **Never re-render** — this skill ONLY mutates storyboard.yaml + edit_log. Re-render goes through the UI's normal trigger
- **Always write edit_log** — re-plan with note is the highest-quality taste signal; logging is non-negotiable
- **Anchor exact-copy** — same hard contract as canonical planner: validate before writing

## Skip cases

If `episode_id` is in an in-progress render queue, refuse and ask user to cancel UI render first (avoid race).

If `beat_id` is already `finalized`, ask user to confirm — re-planning a finalized beat unblocks downstream beats.

## Phase 1 caveats

- `prompts/broll_planner.md` is a placeholder until PR-3 (#714) ships the real template — until then this skill cannot operate
- Layout vocabulary restricted to `full_aroll` + `full_broll`; component to `bigstat` (per `guardrails.yaml`)
