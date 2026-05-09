# 2026-05-08 Textbook ingest v3 — 三 root cause patch + BSE 3 章驗證

**Stakes**：修修最後一次機會，BSE 3 章 Obsidian 人眼驗證任一條失敗 → 寫 Codex handoff。

**Worktree**：`E:\nakama-stage4` @ `docs/kb-stub-crisis-memory` (HEAD 2bd5073)
**主 repo `E:\nakama`** 在 `impl/N454-brook-synthesize-store`，**禁碰**。
**Venv**：`E:/nakama/.venv/Scripts/python.exe` 共用。
**Env**：`set -a && source E:/nakama/.env && set +a` 進 worktree shell。

## Step 0：commit + clean

1. `git add scripts/verify_staging.py` → commit「fix(verify_staging): match walker payload by chapter_title not filename idx」
2. PowerShell SendToRecycleBin：
   - `KB/Wiki.staging/Sources/Books/biochemistry-for-sport-and-exercise-maclaren/` 整目錄
   - `KB/Wiki.staging/Sources/Books/sport-nutrition-jeukendrup-4e/` 整目錄
   - `KB/Wiki.staging/Concepts/` 整目錄裡 .md（566 個）
3. **保留**所有 `E:\nakama-stage4\docs\runs\` 下 logs / reports

## Patch 1：walker strip EPUB internal links

**問題**：EPUB raw 含 `[Chapter 12](chapter12.xhtml)` markdown link，walker copy-paste 進 `payload.verbatim_body`。Obsidian 點下去 auto-stub 空白 `chapter12.xhtml.md`。

**位置候選**：
- A. `shared/source_ingest.py` walker `walk_book_to_chapters` 內，產出 `verbatim_body` 前 strip
- B. `scripts/run_s8_preflight.py` `_assemble_body` 在圖片 transform 之前加一步

**選 A** — root cause 是 verbatim_body 已含污染，越早洗越乾淨。其他 caller（concept dispatch 等）都吃 walker output，洗在 walker 一次到位。

**Regex pattern**（保留 link text、丟 href）：
```python
# [text](path.xhtml) or [text](path.xhtml#anchor) or [text](#anchor)
re.sub(r'\[([^\]]+)\]\([^\)]*\.xhtml(?:#[^\)]*)?\)', r'\1', text)
re.sub(r'\[([^\]]+)\]\(#[^\)]+\)', r'\1', text)
# bibliography refs like [Smith 2020](#c10-bib-0048) — also strip
```

**Test**：`tests/shared/test_source_ingest_strip_epub_links.py`
- input 含 `([chapter 12](chapter12.xhtml))` → output 含 `(chapter 12)` 純文字
- input 含 `(see [Smith 2020](#c10-bib-0048))` → output `(see Smith 2020)`

**注意**：`walker_verbatim_body` 改變後，verbatim_match_pct 仍 PASS（因 walker 跟 normalize 用同一個被 strip 的 verbatim_body）。

## Patch 2：concept slug 規則對齊

**問題**：LLM emit `[[NADPH oxidase]]`（含空格），`shared/concept_dispatch.py` slug validator 拒絕含空格名 → concept page 不寫進 `KB/Wiki.staging/Concepts/` → wikilink 紅。

**先讀**：`shared/concept_dispatch.py` 找 slug validator regex / 拒絕 list，看現規則。

**決策**：選 (a) 接受空格 — Obsidian 原生支援含空格 filename，不用改 LLM prompt 也不用 normalize。改 slug validator regex：
- 允許：letters, digits, spaces, hyphens, parens, `+`, `'`
- 禁止：`/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`（Windows path 不合法字元）+ 開頭/結尾空格

**注意**：concept page filename 含空格 — 確認 `kb_writer` 寫檔不踩雷（Windows 檔名空格 OK，但路徑 quote 要對）。

**Test**：`tests/shared/test_concept_slug_normalization.py`
- `NADPH oxidase` → 過、寫成 `Concepts/NADPH oxidase.md`
- `Na+-K+ Pump` → 過
- `glycogen phosphorylase` → 過
- `bad/slug` → 拒
- `bad:slug` → 拒

## Patch 3：metadata blocks 移到章節末

**問題**：`_assemble_body` 在每個 H2 後 append `### Section concept map` + `### Wikilinks introduced` → 跟 textbook H2/H3 結構交錯亂視覺。

**修改位置**：`scripts/run_s8_preflight.py` `_assemble_body`（line 245-307）

**新行為**：
- Body = walker verbatim + 圖片 transform（純 textbook，無任何插入）
- 章末 append（單一 appendix 區）：
  ```
  
  ---
  
  ## Section Concept Maps
  
  ### {anchor 1}
  
  {concept_map_md_1}
  
  ### {anchor 2}
  
  {concept_map_md_2}
  
  ## Wikilinks Introduced
  
  - [[term1]]
  - [[term2]]
  ...
  ```

**同步改**：`normalize_for_verbatim_compare`（line 703-724）的 strip regex 從「per-section interleave」改成「`---\n\n## Section Concept Maps` 之後全砍」：
```python
body = re.sub(r'\n\n---\n\n## Section Concept Maps\n.*$', '', page_body, flags=re.DOTALL)
```

**Test**：`tests/scripts/test_assemble_body.py` 既有 13 個 test 改寫 expected output，確認 verbatim 100% PASS 仍 hold。

## Step 4：BSE 3 章 re-ingest

完整流程（per chapter）：
```bash
cd /e/nakama-stage4 && set -a && source /e/nakama/.env && set +a && \
  /e/nakama/.venv/Scripts/python.exe -m scripts.run_s8_preflight \
  --vault-root "E:/Shosho LifeOS" \
  --raw-path "E:/Shosho LifeOS/KB/Raw/Books/biochemistry-for-sport-and-exercise-maclaren.md" \
  --book-id biochemistry-for-sport-and-exercise-maclaren \
  --chapter-index N
```

跑 N=1 / 3 / 6（短/中/長代表）。

## Step 5：Obsidian 人眼驗證 checklist（修修親手）

每章 4 條全 PASS 才算通過：
1. ✅ inline body 不含 markdown link 形式 `(xxx.xhtml)` — 點任何 inline link 不 auto-stub
2. ✅ 章末 `## Wikilinks Introduced` 列表所有 wikilinks 點下去都跳到 concept page、無紅 link、無 auto-stub
3. ✅ Body 視覺乾淨：H1 → H2 textbook section → H3 textbook subsection → ... → 章末 appendix（concept maps + wikilinks）— 中間無 metadata interleave
4. ✅ Figures 顯示（vault root + Attachments path 對得到）

## Step 6：分支

- 3 章全綠 → ship 流程 / 全量 28 章
- 任一失敗 → Task #17 寫 Codex handoff doc，停手

## Cost / wall 預估

- Step 0：5min, $0
- Patch 1+2+3 + tests：60-90min, $0
- BSE 3 章 re-ingest：~45min, ~$3
- Obsidian 驗證：修修 15min
- **Total wall：~2hr, ~$3**

## 不要做的事

- 不要 batch 跑 28 章
- 不要碰 SN（複雜度高、cross-ref 多，BSE 過了再評估 SN）
- 不要 commit 任何 staging output
- 不要動主 repo `E:\nakama`
- 不要在 patch 沒 test 通過前 re-ingest

## 起手 sequence（post-compact）

1. 讀本檔
2. `git status` 確認 worktree 乾淨（除 verify_staging.py uncommitted）
3. 跑 Step 0 → Patch 1 → Patch 2 → Patch 3 → Step 4 → 等修修 Step 5
