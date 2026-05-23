# ADR-028 Phase B Pre-Flight Report

Generated: 2026-05-20
Vault: `E:\Shosho LifeOS`
Repo: `E:\nakama`

## TL;DR

- Total migration ops planned: **103**
- Surprises flagged: **1**

## ⚠️ Surprises (review before greenlighting PR-B1)

- Repo destination `docs/prds` already has content — PR-B1 will merge (verify no name collisions).

## Inventory

### Inbox

- `Inbox/kb/` exists: True
- `Inbox/kb/*.md`: **16** files
- `Inbox/kb/attachments/{slug}/`: **0** slug dirs
- `Inbox/web/*.md`: 0 (greenfield target — should be 0)
- Other Inbox children: 17
  - `Inbox/ACSM's Clinical Exercise Physio - Walter R. Thompson.epub`
  - `Inbox/ACSM's Guidelines for Exercise - Amanda R. Bonikowske.epub`
  - `Inbox/ACSM's Introduction to Exercise - Jeffrey A. Potteiger.epub`
  - `Inbox/ACSM's Nutrition for Exercise S - Dan Benardot, PhD, DHC, RDN, FA.epub`
  - `Inbox/Biochemistry for Sport and Exer - Don MacLaren.epub`
  - `Inbox/effect-of-exercise-on-depression-and-anxiety-symptoms-systematic-umbrella-review.md`
  - `Inbox/Exercise Technique Manual for R - NSCA.epub`
  - `Inbox/Hantavirus in humans_ a review of clinical aspects and management - The Lancet Infectious Diseases.mhtml`
  - `Inbox/hantavirus-in-humans-a-review-of-clinical-aspects-and-management.md`
  - `Inbox/multimodal-clocks-of-human-aging.md`
  - `Inbox/Muscle and Exercise Physiology - Prof.Jerzy A. Zoladz, Ph.D., D.epub`
  - `Inbox/nonlinear-dynamics-of-multi-omics-profiles-during-human-aging.md`
  - `Inbox/personality-and-mental-health-as-mediators-linking-childhood-maltreatment-to-int.md`
  - `Inbox/Physiology of Sport and Exercis - W. Larry Kenney, PhD, Jack H. W.epub`
  - `Inbox/qa-line2-bugs-2026-05-04.md`
  - `Inbox/Structured Exercise after Adjuvant Chemotherapy for Colon Cancer _ New England Journal of Medicine.mhtml`
  - `Inbox/structured-exercise-after-adjuvant-chemotherapy-for-colon-cancer.md`

### Nami Notes (vault stays; folder renamed)

- `Nami/Notes/` exists: True
- Notes: **2** files
- Total size: 8,861 bytes (8.7 KB)

### AgentReports (Franky → repo)

- `AgentReports/` exists: True
- Franky weekly reports: **1** files
- `dev-backlog.md`: present

### AgentBriefs (Nami briefs → AgentOutputs/nami/briefs/)

- `AgentBriefs/` exists: True
- Brief files: **0**

### Projects (marker retro-fit visibility)

- Total `Projects/*.md`: **3**
- Pages with legacy `%%KW-START%%`: **1** (retro-fit candidates)
- Pages with new `%%agent-zoro-keywords-start%%`: 0 (PR-A3 templates haven't been used yet, expected ~0)
- Pages with `<!-- vault:human-only-section -->`: 0

Legacy-marker pages (need retro-fit in Phase B/C):
- `Projects/肌酸的妙用.md`

### Files/ (Category A + B migration)

- `Files/` exists: True
- Total files: **69**
- Files with references in vault: **69**
- Orphan files (no references): **0**

### KB/Attachments/ (flatten target)

- Nested buckets to flatten: ['inbox', 'pubmed']
  - `inbox/`: 1 slug subfolders
  - `pubmed/`: 6 slug subfolders
- Already-flat slugs: 0

### Kill targets (Phase B deletes after migration)

- `KB/Wiki/Outputs/`: 9 files, 1 dirs
- `KB/Wiki/Syntheses/`: 0 files, 0 dirs
- `KB/Wiki/Comparisons/`: 0 files, 0 dirs
- `Case Studies/`: 1 files, 0 dirs
- `Incidents/`: 1 files, 2 dirs
- `Schemas/`: 0 files, 0 dirs
- `AgentBriefs/`: 1 files, 0 dirs

### Repo destination collision check

- `data/agent_reports/franky/weekly` — clean (will be created)
- `data/agent_reports/franky/dev-backlog.md` — clean (will be created)
- `docs/prds` — **EXISTS** (2 children) (PR-B1 merges into existing)
- `docs/case-studies` — clean (will be created)
- `docs/incidents` — clean (will be created)

## Manifest summary

Sub-op counts:
- `files-category-a-paper`: 35
- `files-category-b-journal`: 34
- `inbox-kb-to-web`: 16
- `kb-attachments-flatten`: 7
- `vault-folder-delete-or-relocate`: 3
- `nami-notes-move`: 2
- `franky-weekly-vault-to-repo`: 1
- `franky-dev-backlog-vault-to-repo`: 1
- `case-studies-vault-to-repo`: 1
- `incidents-vault-to-repo`: 1
- `schemas-delete`: 1
- `agent-briefs-delete-after-move`: 1

Full manifest: `.tmp/adr028-migration-manifest.json` (103 ops)
