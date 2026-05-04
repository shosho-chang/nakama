# Line 2 Annotation Pipeline — QA 驗收步驟

> 用途：驗 PRD #337 三 slice（PR #342/#343/#344）端到端工作 — annotation 永久存活、cross-source aggregate、sync state UX。Line 2 讀書心得手跑前的最後 gate。
> 對應 PRD：GH issue [#337](https://github.com/shosho-chang/nakama/issues/337) closed
> 對應 ADR：[`docs/decisions/ADR-017-annotation-kb-integration.md`](../decisions/ADR-017-annotation-kb-integration.md)
> 對應 7 層架構：[CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) Stage 2（閱讀註記）→ Stage 3（整合）
> 估時：第一次跑 ~30 min（含視覺驗收）；之後例行 ~10 min/批

---

## Phase 0 — Pre-flight checklist

### 0.1 環境變數

```bash
cd E:/nakama
grep -E "^VAULT_PATH|^DISABLE_ROBIN|^ANTHROPIC_API_KEY" .env
# 期待：VAULT_PATH 指 F:/Shosho LifeOS（或修修當前 active 路徑）
# 期待：DISABLE_ROBIN 不存在或空（本機需要 Robin）
# 期待：ANTHROPIC_API_KEY 有值（sync 走 LLM merge）
```

### 0.2 套件 + 服務

```bash
.venv/Scripts/python -c "from agents.robin.annotation_merger import ConceptPageAnnotationMerger; from shared.annotation_store import AnnotationStore, get_annotation_store, annotation_slug; print('imports OK')"
# 期待：imports OK
```

### 0.3 Smoke：annotation 相關 unit + integration test 全綠

```bash
.venv/Scripts/python -m pytest tests/shared/test_annotation_store.py tests/agents/robin/test_annotation_merger.py tests/test_robin_router.py tests/test_sync_annotations_router.py -q
# 期待：xx passed, 0 failed
```

紅了 → **這份 QA 不要繼續往下跑** — 先回頭修 test。

### 0.4 啟動本機 Thousand Sunny

```bash
.venv/Scripts/python -m uvicorn thousand_sunny.app:app --host 127.0.0.1 --port 8000 --reload
# 另開 browser tab → http://127.0.0.1:8000/login → 過 auth
```

### 0.5 Vault 狀態 baseline

```bash
ls "F:/Shosho LifeOS/KB/Annotations/" 2>/dev/null
# 期待：不存在或為空 dir（這次 QA 從零開始，避免舊 annotation 干擾）
# 若有舊檔 → 移到備份 dir 不要刪
```

---

## Phase 1 — Reader 開檔 sanity（4 來源）

目標：確認 Reader 4 種檔案來源都能開、frontmatter 解析正常。

### 1.1 Inbox 一般 markdown

1. 在 `F:/Shosho LifeOS/Inbox/` 隨便放一個 `.md` 檔（或挑現有的）
2. Browser 開 `http://127.0.0.1:8000/` → 應看到 Inbox 列表
3. 點檔案 → reader 應渲染 markdown body

**人工 gate**：
- [ ] reader 正常渲染標題 / 段落 / 程式碼區塊
- [ ] header 顯示 "0 條未 sync" badge（淺色、隱藏狀態）
- [ ] 沒有 console error（按 F12 看 Network + Console tab）

### 1.2 KB Source 既有雙語頁

1. Browser 開 `http://127.0.0.1:8000/read?base=sources&file=<某個既有 KB/Wiki/Sources/.md>`（直接 URL 過去）
2. 應看到 PubMed/Reader 之前 ingest 的雙語內容

**人工 gate**：
- [ ] base=sources 路徑正常解析（不 404）
- [ ] 雙語段落正常呈現

### 1.3 中文 EPUB → markdown 路徑（修修明確 sanity check #1）

> 這條是 Line 2 critical path — 修修要看一本中文書但**從未實測**過 EPUB ingest 中文書的實際成果。

1. 從修修書庫挑一本**短的**中文 EPUB（< 200 頁理想）
2. 丟 `F:/Shosho LifeOS/Inbox/`
3. 走 Reader `/start` flow → 走完 ingest pipeline → 結果在 `KB/Wiki/Sources/<book-slug>.md`
4. 開那個 markdown 檢查

**人工 gate**：
- [ ] EPUB 成功轉 markdown，不是亂碼 / 空檔
- [ ] 章節結構保留（## 一級標題或類似）
- [ ] 中文段落讀起來通順、不缺字 / 切錯
- [ ] frontmatter 含 `title` / `source_type: book`（[`agents/robin/agent.py:37`](../../agents/robin/agent.py#L37)）

> 失敗的話 → 開 GH issue 紀錄 EPUB → md 中文書 ingest gap，這是 Line 2 critical path blocker；annotation QA 改用既有 markdown 檔繼續跑。

### 1.4 英文 EPUB / 學術論文（修修 sanity check #3）

1. 用 PubMed digest 路徑或直接 PDF/EPUB 走 ingest 後產出的雙語 md
2. 開 reader 確認 `is_bilingual: true` 渲染雙語切換正常

**人工 gate**：
- [ ] 英文書 / 論文 ingest 成功
- [ ] 雙語切換 button 工作（如有）

---

## Phase 2 — Annotation 持久化（PR #342 核心）

目標：annotation 跟 source lifecycle 解耦 — 標完不消失，重 load 還在。

### 2.1 標 highlight + annotation

1. 開 Phase 1.1 的 Inbox markdown
2. 選一段文字 → 用快捷鍵 / button 標 `==highlight==`（純重點）
3. 選另一段 → 標 annotation（重點 + 個人註解 note）
4. 觀察 header `unsynced_count` badge 從 0 → N

**人工 gate**：
- [ ] highlight 視覺呈現（黃 / 暗模式對應色）
- [ ] annotation 視覺呈現（callout block 帶 note）
- [ ] badge 數字正確（標 N 條 = badge 顯示 N）
- [ ] badge 顏色為 `warn`（淺橘 / 對應暗色）

### 2.2 檢視 KB/Annotations/ 物理檔

```bash
ls "F:/Shosho LifeOS/KB/Annotations/"
# 期待：出現 <slug>.md（slug 對應 source filename / frontmatter title）

cat "F:/Shosho LifeOS/KB/Annotations/<slug>.md"
# 期待：
# - frontmatter 含 schema_version / source_slug / source_path / last_synced_at: null（還沒 sync）
# - body 是 structured callout：每條 highlight + annotation 帶 stable id / reftext / created_at / modified_at / note
```

**人工 gate**：
- [ ] KB/Annotations/<slug>.md 存在
- [ ] frontmatter 完整（5 個 field 全有）
- [ ] body 結構符合 ADR-017
- [ ] **原 source 檔（Inbox 那份）內容沒被 mutate**（diff 跟 Phase 1.1 開檔前一致）

### 2.3 Reload survives

1. Browser 關 tab → 重新開同一個 `/read` URL
2. 標的 highlight + annotation 應原樣呈現

**人工 gate**：
- [ ] highlight 不消失
- [ ] annotation note 完整保留
- [ ] badge 數字保留（仍顯示 N 條未 sync）

### 2.4 跨 session 持久化

1. `systemctl --user restart`（Mac）或 Ctrl+C 結束 uvicorn → 重啟
2. 重新開 `/read` URL

**人工 gate**：
- [ ] 標記全保留（檔在 disk 不在 memory）

---

## Phase 3 — Sync 到 Concept page（PR #343 核心）

目標：annotation merge 進對應 Concept page `## 個人觀點` section，per-source boundary marker，full replace。

### 3.1 起始狀態

挑一個 **既有 Concept page**（`KB/Wiki/Concepts/<concept>.md`），目標是讓 Phase 2 標的 annotation 至少有一條會 merge 進這個 page。

```bash
# 紀錄 baseline
cp "F:/Shosho LifeOS/KB/Wiki/Concepts/<concept>.md" /tmp/concept-before.md
```

### 3.2 觸發 sync

1. 回 Phase 2.1 的 reader tab
2. 按 header 「同步到 KB」按鈕
3. 觀察 syncIndicator（loading spinner）→ badge 變 ok（淺綠 「✓ 全部 sync」）

**人工 gate**：
- [ ] sync 按鈕響應、不卡住
- [ ] 完成後 badge 從 warn 變 ok
- [ ] unsyncedCount 變 0

### 3.3 驗 Concept page mutation

```bash
diff /tmp/concept-before.md "F:/Shosho LifeOS/KB/Wiki/Concepts/<concept>.md"
# 期待 diff：
# - 多了 ## 個人觀點 section（如果之前沒有）
# - section 內含 boundary marker：<!-- annotation-from: <slug> --> ... <!-- /annotation-from: <slug> -->
# - boundary 內 callout 結構：> [!annotation] from [[<slug>]] · YYYY-MM-DD\n> **段落**: reftext\n> **修修**: note
```

**人工 gate**：
- [ ] `## 個人觀點` heading 存在
- [ ] boundary marker pair 完整
- [ ] callout 渲染正確（在 Obsidian 開 concept page 視覺確認）
- [ ] author=修修 + source backlink `[[<slug>]]` 顯示
- [ ] 段落順序按 annotation modified_at 時間（不是 source 分組）

### 3.4 KB/Annotations/<slug>.md last_synced_at 更新

```bash
grep last_synced_at "F:/Shosho LifeOS/KB/Annotations/<slug>.md"
# 期待：last_synced_at: <剛才 sync 的 ISO timestamp>
```

**人工 gate**：
- [ ] timestamp 對齊剛才 sync 時間（±60s）

### 3.5 Idempotency — 連按兩次 sync 結果相同

1. 不改 annotation
2. 再按一次「同步到 KB」
3. 觀察 Concept page

```bash
diff <(cat "F:/Shosho LifeOS/KB/Wiki/Concepts/<concept>.md") /tmp/concept-after-1st-sync.md
# 期待：無 diff（content-identical）
```

**人工 gate**：
- [ ] Concept page 內容一個字不變
- [ ] boundary marker 沒重複塞、沒換行飄

---

## Phase 4 — Cross-source aggregate（PR #343 完整能力）

目標：兩本書同 concept 都標 annotation → 同 page 多 boundary block 不互污。

### 4.1 第二來源標 annotation

1. 挑另一個 Inbox / KB Source markdown（書 B、跟書 A 不同 slug）
2. Phase 2 流程標 annotation，目標是 LLM merge 時會落在**同一個 Concept page**（書 A 已 sync 過的那個）

### 4.2 觸發第二次 sync

按書 B reader 的「同步到 KB」。

### 4.3 驗 Concept page

```bash
grep -c "annotation-from:" "F:/Shosho LifeOS/KB/Wiki/Concepts/<concept>.md"
# 期待：4（兩個 source × 開/關 boundary marker）

grep "annotation-from:" "F:/Shosho LifeOS/KB/Wiki/Concepts/<concept>.md"
# 期待：兩 source slug 各出現一次 open marker、一次 close marker
```

**人工 gate**：
- [ ] Concept page 內兩個 source 各自獨立 boundary block
- [ ] 書 A 的觀點 + 書 B 的觀點都在
- [ ] 視覺區隔清楚（不混在一起）
- [ ] section 內整體仍按 annotation 時間順序（**section 內 flat 排序**，不切按 source 分組 — ADR-017 §Q13）

### 4.4 重 sync 書 A → 書 B 不被污染

1. 對書 A reader 再按一次 sync（不改 annotation）
2. 驗 Concept page

**人工 gate**：
- [ ] 書 A boundary block 不變
- [ ] 書 B boundary block **完全沒被動到**（per-source full replace 隔離）

---

## Phase 5 — Edit / Delete 傳播（full replace 語意）

目標：在 reader 改 annotation 後 sync，Concept page 跟著變更（含刪除）。

### 5.1 Edit annotation note

1. 回書 A reader
2. 改某一條 annotation 的 note 文字
3. badge 應從 ok 變 warn（出現未 sync count）
4. 按 sync

**人工 gate**：
- [ ] Concept page 該條 callout note 文字更新
- [ ] 其他條沒被動到

### 5.2 Delete annotation

1. 在 reader 刪除某一條 annotation
2. badge 變 warn → 按 sync

**人工 gate**：
- [ ] Concept page 該條 callout 從 boundary block 內**完全消失**
- [ ] 其他條不變

### 5.3 全部刪光 → 重 sync

1. 刪光書 A 所有 annotation
2. 按 sync
3. 驗 Concept page

**人工 gate**：
- [ ] 書 A boundary block 仍存在但內容空（或 boundary 整對 wiped — 看實作）
- [ ] 書 B boundary block 完整保留

> 實作 detail：boundary 對是否在 annotation 全清後保留 vs 整對 remove，看 `agents/robin/annotation_merger.py` 行為；驗收看 Concept page 視覺乾淨即可，不糾結 marker 殘留。

---

## Phase 6 — 修修手寫心得 closure（Stage 4 LLM 不介入）

目標：跑完一輪後，修修可以在 Project 頁面手寫心得（**不是 agent 做**）。

### 6.1 Project bootstrap（修修 sanity check #2）

```
Slack DM Nami / 或本機 /project-bootstrap "讀書心得 — <書名>"
```

期待生成：
- `F:/Shosho LifeOS/Projects/<project-name>.md`
- `Tasks/` 下 3 個 default task

**人工 gate**：
- [ ] template 正常生成、frontmatter 對
- [ ] tasks 數量 / 內容合理（不 stale）

### 6.2 Cross-link：心得 → KB/Annotations/

修修在 Project page 手寫心得時：
1. 引用書 A 觀點 → wikilink `[[<book-A-slug>]]`
2. 點 wikilink 應跳到 `KB/Wiki/Sources/<book-A-slug>.md`
3. 該 source 反向看 backlink 應出現 Project page

**人工 gate**：
- [ ] Obsidian 雙向 wikilink 正常 resolve
- [ ] Concept page `## 個人觀點` 也能在 Project page 內引用 / 看到

---

## Phase 7 — Bug log（在 vault Inbox / Slack DM Nami）

修修這次手跑過程，**任何卡 / 慢 / 不順 / 想多一個按鈕**的點都記下來：

```markdown
F:/Shosho LifeOS/Inbox/qa-line2-bugs-2026-05-04.md

# Line 2 QA Bug Log — 2026-05-04

## 卡點
- [ ] <描述>，blocker / friction
- [ ] ...

## 想多的功能
- [ ] <描述>
- [ ] ...

## 美學 / UX
- [ ] <描述>
- [ ] ...
```

跑完後 → grill 一輪 → 凍結 next feature（禁止 over-design 原則保留）。

---

## Beta Acceptance Sign-off

```
QA 日期:       ___________________________
測試書 A:      ___________________________ (Inbox / KB Source / EPUB)
測試書 B:      ___________________________
跑完總時長:    _______ min

[ ] Phase 0 pre-flight 全綠
[ ] Phase 1.1 Inbox markdown reader 正常
[ ] Phase 1.2 KB Source 雙語頁正常
[ ] Phase 1.3 中文 EPUB ingest sanity（or 開 issue）
[ ] Phase 1.4 英文 EPUB / 論文 ingest sanity
[ ] Phase 2 annotation 持久化（mark / reload / 跨 session）
[ ] Phase 3 sync to Concept page（含 idempotency）
[ ] Phase 4 cross-source aggregate（兩 source 不互污）
[ ] Phase 5 edit / delete propagation
[ ] Phase 6 project bootstrap + 心得 manual write 路徑通

bug log 條數: _______
```

簽完 → Line 2 工程驗收 closed，可以開始**真實**手跑一本書心得。

---

## Known limitations（出問題時先看這）

1. **EPUB → markdown 中文書未實測** — 1.3 是新驗收項；失敗的話 annotation 工程仍 ship，但 Line 2 critical path 換成 EPUB ingest 議題優先
2. **Reader UI overlay 是 client-side merge** — annotation 渲染靠 JS regex；段落結構大改可能 reftext 失準（PRD §Implementation Decisions）
3. **LLM merge 對 concept linkage 判斷可能誤匹** — sync 把 annotation 塞錯 Concept page 是已知 risk；發現 → 手動 revert + 開 issue
4. **Highlight 不進 Concept page** — ADR-017 §Q4 凍結 asymmetric；只 Annotation（含 note）會 sync
5. **Stage 4 心得 outline 工具不存在** — Phase 6 手寫，沒 LLM 拉 annotation list；deferred 到下一輪
6. **沒 reading session 邊界** — 同 source 多次標 = in-place CRUD，沒「次」first-class 概念

---

## Diagnostics（出錯時依序查）

### Symptom: badge 一直 warn 數字不變
→ POST `/save-annotations` 失敗。F12 Network tab 看 response；最常見：auth cookie 過期（重 login）/ AnnotationStore 寫不進 vault（VAULT_PATH 錯 / 權限）

### Symptom: 按 sync 沒反應 / loading 卡住
→ POST `/sync-annotations/{slug}` timeout 或 LLM call 慢。看 `journalctl --user -u thousand-sunny` 或 uvicorn stdout；最常見：ANTHROPIC_API_KEY 缺 / Claude API quota 撞 / 並行 sync 撞鎖

### Symptom: Concept page boundary marker 沒出現
→ LLM merge 沒匹到任何 concept。查 `KB/Wiki/Concepts/` 是否有對應 page；merger fallback 是 no-op，需要先有 concept page

### Symptom: 跨 source sync 把書 A 的觀點蓋掉書 B
→ boundary marker 寫錯 / parser bug。看 PR #343 review NOTE；附 Concept page raw markdown + 兩 source slug 開 P0 issue

### Symptom: Reader 開檔 400 "此檔案格式不支援線上閱讀"
→ 不是 `.md` / `.txt`。EPUB / PDF 要先走 ingest pipeline 轉 markdown（`/start` flow 或 textbook-ingest skill）

### Symptom: KB/Annotations/<slug>.md 寫不進去
→ `shared/vault_rules.assert_reader_can_write` violation 或 disk 路徑錯。看 uvicorn stdout error trace

### Symptom: 中文 EPUB ingest 出來亂碼
→ encoding / parser 問題。先查 `pymupdf4llm` / `Docling` 中文支援、開 issue 附 EPUB sample（避免直接傳整本書 — 抓問題段落剪 30 頁）

---

## 後續 follow-up（Beta 後，依痛點觸發）

- [ ] 手跑一本完整書 → 凍結 Stage 2/3 next feature（grill 後決定）
- [ ] EPUB ingest 中文書如有 gap → 補 issue + 排修順序
- [ ] Stage 4 心得 outline 工具：等修修明確要 LLM 介入再 build
- [ ] reading session 邊界：同 above
- [ ] Reader UI 美學迭代（按 [docs/design-system.md](../design-system.md)）
- [ ] Cross-source conflict 自動 warn marker（ADR-017 §Q8 否決過，等真實衝突再 grill）
