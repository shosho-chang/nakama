# Annotation Lifecycle Follow-up — ADR-018 落地計畫

> 用途：把 [ADR-018 annotation lifecycle redesign](../decisions/ADR-018-annotation-lifecycle-redesign.md) 的設計落地。
> 對應 ADR：[`docs/decisions/ADR-018-annotation-lifecycle-redesign.md`](../decisions/ADR-018-annotation-lifecycle-redesign.md)
> 對應 CONTEXT：[`agents/robin/CONTEXT.md`](../../agents/robin/CONTEXT.md)
> Grill session：2026-05-04 夜（修修 + Claude Opus 4.7 inline grill-with-docs）
> 估時：~5-8h（4 slice 累計，可 parallel slice 1+2）

---

## 1. 背景

ADR-018 凍結「砍除『同步到 KB』獨立按鈕、改自動觸發 (X)+(Y) + Reader header 加『Ingest 進 KB』按鈕」。本 plan 把這個 design 拆 4 個 vertical slice 落地。

ADR-018 真實要解的 user pain：

| Pain | ADR-017 設計 | ADR-018 解法 |
|---|---|---|
| 「我按 ingest 後不知 annotation 進沒進 KB」 | 兩個獨立按鈕 | (X) ingest 結束 auto-push |
| 「我補標 annotation 之後要再按一次同步」 | 又一個 manual button | (Y) annotation save debounce auto-push |
| 「Reader 看完想 ingest 必須回 inbox」 | reader 跟 ingest UX 分離 | Reader header 加「Ingest 進 KB」按鈕 |
| 「『同步到 KB』按鈕不知做什麼」 | 動作含混 | 砍除按鈕、改 lifecycle event 自動 |

---

## 2. Slice 拆解

### Dependency graph

```
Slice 1 (X) auto-push ──┐
                        ├──→ Slice 3 砍 sync button ──→ Slice 4 Reader ingest button
Slice 2 (Y) debounce ───┘
```

**順序原則**：必須先有 (X)+(Y) auto-push 真實 work（Slice 1+2），才能砍按鈕（Slice 3） — 否則 user 沒 trigger 機制就 ship 會壞功能。Slice 4 跟前三條獨立，可隨時插。

---

### Slice 1 — (X) source ingest 結束 auto-push annotation

**目標**：Robin source ingest pipeline 結束時自動呼叫 `ConceptPageAnnotationMerger.sync_source_to_concepts(slug)`，把這個 source 的全部 annotation push 到對應 concept page。

**改檔**：
- `agents/robin/ingest.py`：ingest pipeline tail（成功寫完 source page + concept page 之後）加 trigger annotation merger
- `tests/agents/robin/test_ingest.py`：新增「ingest 結束會 trigger merger」test，mock merger 驗 call

**接點 detail**：
1. Find ingest pipeline 成功寫完 source / concept 的 tail call point
2. Call `ConceptPageAnnotationMerger().sync_source_to_concepts(slug)`，傳 ingest 完的 source slug
3. 若 annotation file 不存在 → AnnotationStore.load 返回 None → SyncReport with error；ingest 仍視為 success（annotation 失敗不該 fail ingest）

**驗收**：
- [ ] inbox 一篇有 annotation 的檔，`/start` ingest 跑完 → concept page `## 個人觀點` section 出現對應 callout block
- [ ] inbox 一篇沒 annotation 的檔，`/start` ingest 跑完 → 不報錯、concept page 無變化
- [ ] tests/agents/robin/test_ingest.py 加 mock merger test 全綠

**估時**：~1.5h

---

### Slice 2 — (Y) annotation save debounce auto-push

**目標**：Reader 標 annotation 後（auto-save 觸發），若 source 已 ingest 過，debounce 30s + blur tab 後自動觸發 push to concept page。

**改檔**：
- `thousand_sunny/templates/robin/reader.html`：reader 端 JS 加 debounce timer + blur tab 觸發
- `thousand_sunny/routers/robin.py`：`/save-annotations` endpoint response 加 `source_ingested: bool` 欄位（client 判斷該不該 trigger debounce push）
- `tests/test_save_annotations_router.py`：新增「response 帶 source_ingested 欄位」test

**設計 detail**：

```js
// reader.html 端 (annotation save 後 client logic)
let pushDebounceTimer = null;
const PUSH_DEBOUNCE_MS = 30_000;

function scheduleAnnotationPush() {
  if (!sourceIngested) return;  // source 未 ingest → 等 (X) trigger
  clearTimeout(pushDebounceTimer);
  pushDebounceTimer = setTimeout(triggerPush, PUSH_DEBOUNCE_MS);
}

window.addEventListener('blur', () => {
  if (pushDebounceTimer) {
    clearTimeout(pushDebounceTimer);
    triggerPush();  // blur tab → 立即觸發
  }
});

async function triggerPush() {
  await fetch('/sync-annotations/' + encodeURIComponent(SLUG), { method: 'POST' });
  // 不顯 indicator — 這是 background 自動行為，不打擾閱讀
}
```

**source_ingested 偵測**：reader 開檔時判斷 `KB/Wiki/Sources/{slug}.md` 是否存在。後端 `/read` response 已含 slug，新增 `source_ingested` boolean 即可。

**驗收**：
- [ ] reader 標 annotation → 30s 後 concept page 出現對應 callout
- [ ] reader 標 annotation → 切 tab → concept page 立即出現對應 callout（不等 30s）
- [ ] reader 連續標 5 條 annotation 在 30s 內 → 30s 後只觸發一次 push（debounce reset 機制）
- [ ] source 未 ingest 過 → reader 標 annotation 不 trigger push

**估時**：~2h

---

### Slice 3 — 砍除「同步到 KB」按鈕

**目標**：Slice 1+2 ship 後，按鈕成為 dead UI；砍除前端按鈕，後端 endpoint 保留（給 Slice 1 X 觸發 + Slice 2 Y 觸發內部呼叫）。

**改檔**：
- `thousand_sunny/templates/robin/reader.html`：刪 `<button id="syncBtn">`（line 277）+ `syncToKB()` 函式（line 697-722）+ 相關 CSS
- 後端 `/sync-annotations/{slug}` endpoint **保留**（Slice 1+2 內部呼叫）
- `tests/test_sync_annotations_router.py`：保留所有 endpoint test（後端契約不變）
- 既有 PR #368 P0 hotfix code（tool_use forced JSON）保留 — 改進的是 endpoint 的 LLM contract

**驗收**：
- [ ] reader 開檔，header 不見「同步到 KB」按鈕
- [ ] (X)+(Y) 自動觸發機制仍正常 work（Slice 1+2 acceptance 全綠）
- [ ] `POST /sync-annotations/{slug}` endpoint 直接 curl 仍能呼叫（給 internal 用）

**估時**：~30min

---

### Slice 4 — Reader header 加「Ingest 進 KB」按鈕

**目標**：reader 看完直接觸發 ingest，不必回 inbox 找檔。跟 inbox 既有 `/start` flow 共用後端。

**改檔**：
- `thousand_sunny/templates/robin/reader.html`：header 加 `<button id="ingestBtn">`（取代砍掉的 syncBtn 位置）+ `triggerIngest()` 函式
- `thousand_sunny/routers/robin.py`：`/start` endpoint 已存（POST），reader 端 fetch 觸發；ingest 結束 redirect 回 reader 顯示 ingested 狀態
- 「已 ingest」狀態 UX：button 文字變「已進 KB ✓」disable + 自動跳載入 KB/Wiki/Sources 路徑 reader（slug 變、URL 變）

**Sub-decision（plan 凍結）**：

按 Ingest 進 KB 後 reader 行為 = **redirect 到 KB/Wiki/Sources 路徑的 reader**（slug + URL 都變、annotation 持久化在 `KB/Annotations/{slug}.md` 不丟）。理由：
- ingest 後 source 物理位置變（Inbox → KB/Wiki/Sources），URL 應反映
- 修修 reload 後仍能繼續閱讀同篇，annotation 都在
- (X) auto-push 在 ingest 結束已 trigger（Slice 1），redirect 後立即看到 concept page 已更新

**驗收**：
- [ ] inbox 一篇 + reader 開 → header 看到「Ingest 進 KB」按鈕
- [ ] 按 → 看見 spinner → ingest 完成 → 自動 redirect 到 `KB/Wiki/Sources/{slug}.md` 的 reader
- [ ] redirect 後的 reader 內容 = ingest 後的 source page（無 annotation 嵌入，符合 ADR-018）
- [ ] annotation 列表保留（從 `KB/Annotations/{slug}.md` 讀）
- [ ] concept page `## 個人觀點` 出現對應 callout（Slice 1 (X) auto-push）

**估時**：~2h

---

## 3. Out of scope（明確排除）

- **「我以為沒看過但已 ingest 過」dedup feature** — Q2 grill 順手 surface 的 Robin ingest 該補的 dedup 偵測，獨立 issue
- **「Quote 具體某條 annotation」全域 search** — Q4 reference scenario (iii) 偶爾場景，獨立 feature
- **既有已 sync 過的 concept page migration** — vault 已存的 cross-source aggregator block 保留、不改 schema
- **`shared/kb_writer.py:132` temperature 同 bug** — 跟本 plan 無關，獨立修
- **reader URL encode 4 處 bug** — 卡點 #1，獨立 PR

---

## 4. Risks

### R1：(Y) debounce 跟 reader UX 衝撞
**風險**：30s debounce 期間 user 跳到別篇 reader → tab still focus → push 沒 trigger → 漏 sync
**Mitigation**：blur tab + reader navigate away（route change）兩個事件都 trigger 立即 push
**驗收 cover**：Slice 2 acceptance 第 4 條 + 補一條「navigate away 觸發」test

### R2：Slice 1 (X) trigger 失敗會 fail ingest
**風險**：merger LLM 噴錯 → 整個 ingest pipeline rollback？
**Mitigation**：annotation push 失敗只 log warning，不中斷 ingest（source page + concept page 已寫成功就視為 ingest 成功）
**驗收 cover**：Slice 1 acceptance 第 2 條

### R3：(X)+(Y) 重複 trigger
**風險**：source ingest 完 (X) push、然後 user 馬上在 reader 加一條 annotation (Y) 又 trigger → 短時間內兩次 LLM call
**Mitigation**：底層 `sync_source_to_concepts` 已有 idempotency short-circuit（PR #368 加），unsynced_count==0 時不打 LLM。重複 trigger 大部分會被吞，僅有真實未 sync annotation 時才打 LLM
**驗收 cover**：PR #368 既有 test_sync_short_circuits_when_unsynced_count_zero

### R4：Slice 3 砍按鈕但 (X)+(Y) 未 ship 時誤 deploy
**風險**：Slice 3 在 Slice 1+2 之前 deploy → user 失去 manual sync 能力
**Mitigation**：本 plan §2 dependency graph 明確要求 Slice 1+2 先；GitHub PR review 階段守 gate

---

## 5. Acceptance（整體）

落地 4 slice 後，整套 annotation lifecycle 必須 pass：

- [ ] **Scenario A**：inbox 一篇有 annotation 檔 → reader 看 → 按 Reader header「Ingest 進 KB」 → 自動 redirect 到 KB/Wiki/Sources reader → concept page 已含對應 callout（Slice 4 + 1）
- [ ] **Scenario B**：source 已 ingest 過 → reader 標新 annotation → debounce 30s 後 concept page 出現對應 callout（Slice 2）
- [ ] **Scenario C**：reader 標 annotation → 切 tab → 立即 sync 不等 30s（Slice 2 blur trigger）
- [ ] **Scenario D**：reader 不見「同步到 KB」按鈕（Slice 3）
- [ ] **Scenario E**：cross-source aggregate — A 篇 annotation push 到「肌酸代謝」concept、B 篇 annotation push 到同 concept → per-source boundary 隔離正確（既有 PR #343 邏輯，靠 ADR-018 不破壞）

---

## 6. PR strategy

| Slice | PR title 建議 | Order |
|---|---|---|
| Slice 1 | `feat(robin): (X) ingest 結束 auto-push annotation to concept` | 1st |
| Slice 2 | `feat(reader): (Y) annotation save debounce auto-push` | 2nd（可跟 Slice 1 平行） |
| Slice 3 | `chore(reader): 砍除「同步到 KB」按鈕（ADR-018）` | 3rd（dep on 1+2） |
| Slice 4 | `feat(reader): Ingest 進 KB 按鈕（reader 直接觸發 source ingest）` | 4th（獨立） |

每個 slice 一個 focused PR，走既有 PR review flow（feedback_review_skill_default_for_focused_pr）。
