# Memory System Redesign — Panel Integration Matrix

**Step 4 of 5** of multi-agent panel review.
**Date:** 2026-05-08
**Sources:**
- Claude v1 draft: `2026-05-08-memory-system-redesign-v1.md`
- Codex audit: `2026-05-08-codex-memory-redesign-audit.md`
- Gemini audit: `2026-05-08-gemini-memory-redesign-audit.md`

## 整合矩陣（按議題分組）

### Cluster A — 事實錯誤（Codex 的 code-grounding 強項）

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 297 = 155+114+4+23 嗎？ | 297 | **錯，296（多的是 MEMORY.md）** | 沒提 | Codex 獨見（事實） | **採納** — v2 修正為 296+1 index |
| `feedback_conversation_end.md` 已被改寫？ | 文中暗示「is rewritten」 | **沒有，原樣未改** | 沒提 | Codex 獨見（事實） | **採納** — v2 必須明確標「pending rewrite」 |
| Schema「乾淨 3 欄」？ | 是 | **不是 — `project_nakama_overview.md` 含 tags/created/updated/confidence/ttl，`feedback_test_realism.md` 連 type 都沒** | 沒提 | Codex 獨見（事實） | **採納** — 現實比 v1 雜亂，v2 schema upgrade 要含 lint+migrate 工具 |
| Tracked 檔數 vs filesystem 檔數 | 297 file system 297 = 297 | **filesystem 297，git ls-files 只 289（8 個 untracked）** | 沒提 | Codex 獨見（事實） | **採納** — v2 區分這兩個數字 |

### Cluster B — `memory-trunk` branch 設計

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 是否採用 `memory-trunk` branch | **採納（Decision A）** | **拒絕，過度工程，用 `paths-ignore` + 直 push main** | **同意 Codex，且 isomorphic 到 git-flow develop branch（已退流行）** | **2 vs 1，Claude 是少數** | **採納 Codex/Gemini** — drop memory-trunk，改用 `paths-ignore` |

### Cluster C — L3 「清對話」trigger

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| L3 confirm-mode 是否合理 | 是（候選列表 + user 確認） | **「underspecified to meaningless」拒絕** | **「medicalizes natural behavior」反對；應教 agent 分辨 ephemeral vs durable** | **2 vs 1 但理由不同** | **重新設計** — 不要 confirm-mode，改成：(1) agent 內建判斷力（教它區分 durable vs ephemeral），(2) ephemeral memo 走 `.nakama/session_handoff.md` git-ignored，下個 session 讀完即刪 |

### Cluster D — 跨 agent 多語言問題（Gemini 的獨見）

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 中英混雜 memory 對 Codex 可用？ | **未討論** | **未討論** | **核心盲點：filesystem access ≠ semantic understanding。Codex 的英文 query 無法檢索 `name: 對話結束時自動存記憶` 這類中文 frontmatter 的檔案** | **Gemini 獨見，且高分量** | **採納** — v2 必加：(1) shared/ 強制 bilingual frontmatter（`name_zh`/`name_en`/`description_zh`/`description_en`），(2) reindex 跑跨語言 embedding，(3) 已有舊檔啟動時批量補 |

### Cluster E — Tool-driven vs Doc-driven（Gemini 的框架挑戰）

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 規矩在 docs 還是 tools | **CLAUDE.md + SCHEMA.md（doc）** | 預設 doc-driven 沒挑戰 | **強烈反對 — 對 solo dev 應該是 tool-driven。寫 `nakama memory save --type X --name Y` CLI 而非靠人類記得「commit 到 memory-trunk」** | **Gemini 獨見** | **部分採納** — Phase 0 仍寫 doc（但不靠 doc 強制），Phase 1 Build CLI tool，doc 變成 reference 不是 enforcement |

### Cluster F — Markdown-as-database

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 把 markdown 當 DB 是否合理 | 預設是 | 預設是 | **反對 — schema + INDEX + commits + maintenance 已經是漸進建 DB，是 anti-pattern。應該 SQLite 為 source-of-truth + `export` 跑 markdown 給 git sync** | **Gemini 獨見，但深度高** | **延後** — Phase 0/1 仍走 markdown（簡單）；Phase 4 + 評估是否升級成 SQLite-as-source-of-truth + markdown-export hybrid。短期不投資。 |

### Cluster G — 跨 agent shared/ 寫衝突

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| append-only 解多 agent 衝突 | **是** | 沒挑戰 | **拒絕 — `memory/shared/user/preferences.md` 兩 agent 都更新會 race condition；append-only 只解新增不解 update** | **Gemini 獨見** | **採納** — v2 設計：(1) shared/ 預設**唯讀**（只讀，需要 update 走「propose」流程），(2) 把所有更新寫到 agent 自己的 dir，shared/ 只在明確同步點 batch-merge |

### Cluster H — Phase 0 是否要拆

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| Phase 0 一個 PR 能裝 | 是 | **拆 PR A（CI hygiene）+ PR B（schema）** | **拆 PR A（即時止血）+ PR B（設計 + scaffolding）** | **3-way 同意拆**（細節略不同） | **採納** — 分 PR A + PR B |

### Cluster I — 多 agent / Codex 框架

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 多 agent 切割是否需 redesign | **要（shared/claude/codex/）** | **不必，prefix + frontmatter `agent:` 就夠** | **要，且還要加 bilingual** | **Codex 反對 redesign，Gemini 加碼 redesign** | **折衷** — 採目錄切割（Claude 提案）但 schema 加 `agent:` 欄（Codex 建議）作為冗餘標籤；shared/ 強制 bilingual（Gemini） |

### Cluster J — 替代方案（minimum viable cleanup）

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 只刪 `project_session_*` 是不是夠 | 沒考慮 | **是最強短期替代，addresses C4 + C5 with zero architectural change** | **延伸：那些 memo 應該走 ephemeral file 不進 git** | **Codex+Gemini 共同主張** | **採納** — Phase 0 PR A 加：清理現有 `project_session_*` 進 `_archive/`，**並停止再產生新的** |

### Cluster K — Memory 是否該分離成 nakama-memory repo

| 項目 | Claude v1 | Codex | Gemini | 3-way pattern | 處置 |
|---|---|---|---|---|---|
| 獨立 repo？ | 沒考慮（C1 暗示在主 repo） | **submodule complicates Sandcastle** | **`nakama-memory` 獨立 repo cleaner** | **2 vs 1** | **不採納** — 維持主 repo 內。Codex 的 Sandcastle compatibility 觀察夠重要 |

---

## 整合後 v2 設計（compressed summary）

### 必改

1. **Drop `memory-trunk` branch** — 改用 `.github/workflows/ci.yml` 的 `paths-ignore: memory/**`（已在我們 stash 的 PR 內）+ memory commit 直 push main
2. **Drop L3 confirm-mode** — 改成：
   - 教 agent 分辨「durable knowledge」vs「ephemeral session state」（refined system prompt instruction）
   - Ephemeral 走 `.nakama/session_handoff.md` git-ignored 檔（next session 讀完即刪）
   - Durable 才寫 `memory/`
3. **Bilingual frontmatter for shared/** — `name_zh` / `name_en` / `description_zh` / `description_en`
4. **shared/ 預設唯讀** — 兩個 agent 寫各自 dir，shared/ 走「propose」明確同步
5. **Schema lint + migrate tooling** — Phase 1 必須含 `validate` 跟 `migrate` 指令
6. **修正事實錯誤** — 296+1, 8 個 untracked, 既有 schema 不乾淨

### 保留

- 整體目錄分割（shared/claude/codex/_archive/）— Claude 原案，Gemini 同意
- Schema 升級（visibility/confidence/created/expires/tags）— additive
- Memory ≠ session log 原則
- Append-only files + generated INDEX.md（但加 transition-aware reindex）
- Migration with backward-compat（但改成 transition-aware index）
- Phase 1+ 的維護 cadence（reindex/expire/compact-sessions）

### 延後

- SQLite-as-source-of-truth + markdown-export hybrid（Gemini 提，但 Phase 0/1 太重）
- Cross-lingual embeddings（先做 bilingual frontmatter，embedding 在 Phase 2+）
- CLI tool（`nakama memory save`） — Phase 1 build，Phase 0 暫先靠 doc

### Phase 0 重新切

**PR A（即時止血，今天 ship）：**
1. CI workflow `paths-ignore: memory/**` + cache（已 stash 內，pop 出來）
2. 改寫 `feedback_conversation_end.md` — 移除 auto-commit/push，改成「durable 才寫，ephemeral 走 .nakama/session_handoff.md」
3. 加 `.gitignore` 的 `.nakama/session_handoff.md` 規則
4. 把現有 `memory/claude/project_session_*.md` 全部移到 `memory/_archive/2026-05/`（git mv，保留歷史）

**PR B（設計 + scaffolding，這個 PR 後落地）：**
1. 這個 design doc + 3 audits 進 `docs/research/`
2. 新 `memory/SCHEMA.md`（含 bilingual 規則）
3. 空 `memory/{shared,codex}/...` 目錄殼 + .gitkeep
4. CLAUDE.md 新增 3 段（精簡版，doc 是 reference 不是 enforcement）
5. Phase 1 task tickets 開出去（`memory_maintenance.py` 擴充、CLI tool）

---

## Open questions for user sign-off

### Q1：是否同意 panel verdict — drop `memory-trunk`？

3-way 一致 push back。我（Claude v1）原本主張的 `memory-trunk` branch 模型，Codex 跟 Gemini 都認為過度工程。現在的方案是用 `paths-ignore` + 直 push main。

### Q2：是否同意 ephemeral session handoff via `.nakama/session_handoff.md`？

Gemini 的提案。比 L3 confirm-mode 直觀，且解決 user 的真正痛點（不想每次提醒「記下來」），同時不污染長期記憶。但意味「修修不再有 session 收工 memo 的 git 歷史可翻」。

### Q3：是否同意 bilingual frontmatter（shared/ 強制）？

Gemini 的核心發現。會增加每個 shared/ memory 的撰寫成本（要英中各寫一次 name/description）。但解決 Codex 看不懂中文 memory 的問題。

### Q4：Phase 0 拆 PR A + PR B？

3-way 同意拆。我把 stash 的 CI fix 跟 design 合在一個 PR 是太貪心。

### Q5：是否現在就清舊 `project_session_*`（移進 _archive/）？

Codex 主張這是最強短期替代。但會碰到 6 個月以前的 memo（已經是長期 stable 的歷史），可能砍太狠。建議：移過去 30 天前的（5/8 - 30 天 = 4/8），4/8 之前的留著。

---

## 我推薦的 next step

回答 Q1-Q5 → 我寫 v2 design doc → 你最終 sign off → Phase 0 PR A 落地（今天）→ PR B 落地（明天/週內）。
