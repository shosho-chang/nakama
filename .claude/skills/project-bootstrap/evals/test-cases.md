# project-bootstrap — Manual Eval Cases

Two scenarios covering the main branches. Run by invoking the skill with the
input message and comparing against expected behavior.

## Case 1 — Research (default path)

**User input**:
> 幫我建立一個關於超加工食品的 project

**Expected skill behavior**:
1. Extract `topic="超加工食品"`, no explicit content_type
2. Ask content_type (topic is abstract concept, user didn't hint video/blog)
3. User replies: `research`
4. Skill applies defaults: `area=health` (topic is health-related),
   `priority=medium`, `search_topic` not set (research type)
5. Show plan:
   ```
   要建立的 Project：
     標題：超加工食品
     類型：research
     領域：health
     優先級：medium

   預設 3 個 Task：
     1. 超加工食品 - Literature Review
     2. 超加工食品 - Synthesis
     3. 超加工食品 - Write-up
   ```
6. User: `確認` / `ok` / `go`
7. Skill runs:
   ```
   python scripts/run_project_bootstrap.py --title "超加工食品" \
       --content-type research --tasks "Literature Review" "Synthesis" "Write-up" \
       --area health --priority medium
   ```
8. Report success with `obsidian://` URI and next-step hint about
   `📚 KB Research` button (not keyword-research, since research type)

**Pass criteria**:
- No keyword-research mention in next-steps (research type doesn't need it)
- Area proactively set to `health` without asking (topic hint)
- No unnecessary questions beyond content_type

## Case 2 — YouTube (explicit content_type + search_topic)

**User input**:
> 腸道菌，要拍 YouTube 影片，幫我開個 project

**Expected skill behavior**:
1. Extract `topic="腸道菌"`, `content_type=youtube` (explicit from "拍 YouTube")
2. Skip content_type question (already stated)
3. Apply defaults: `area=health`, `priority=medium`, `search_topic=腸道菌`
4. Show plan with youtube tasks (Pre-production / Filming / Post-production)
5. User confirms
6. Skill runs with `--content-type youtube --search-topic 腸道菌`
7. Report success; next-steps hint mentions `🗝️ 關鍵字研究` button AND the
   `keyword-research` skill as follow-up (youtube type)

**Pass criteria**:
- content_type question skipped (user was explicit)
- Next-steps mentions keyword-research path
- search_topic defaults to topic without asking

## Case 3 — Conflict

**Setup**: Run Case 1 first so `Projects/超加工食品.md` exists.

**User input (in new conversation)**:
> 再建一個關於超加工食品的 project

**Expected**:
1. Same extraction flow
2. User confirms at plan gate
3. Script exits code 2 with `ProjectExistsError`
4. Skill tells user:
   > 已經有同名 project 或 task 了：Projects/超加工食品.md
   > 要改標題（例如加日期後綴 "超加工食品 2026"）還是先處理舊的？
5. Skill does NOT auto-retry, waits for user decision

**Pass criteria**:
- No auto-rename, no overwrite
- Clear choice presented to user

---

## How to Run

Manual runs via Claude Code:
1. Start a fresh conversation
2. Paste the "User input" line
3. Check that the skill triggers (you should see SKILL.md loaded)
4. Play the user role for each confirmation step
5. Verify the output files exist at the expected paths

Automated eval harness (future): use `skill-creator` skill to wrap these cases
into structured evals with LLM-graded rubrics.
