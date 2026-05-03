---
name: textbook ingest 2026-05-03 — ch1 重 ingest + ch4 完成（Wiley Biochemistry）
description: 2026-05-03 session 推進 ch1 (重 ingest 新算法 ch3-style) + ch4 (用 cached vision 跳過 vision call)；ch5-ch11 batch 待修修 GO；踩到 vault E: vs F: 教訓
type: project
---

2026-05-03 桌機 session 啟動 textbook ingest 軸線 PR D 後續工作（ingest Wiley *Biochemistry for Sport and Exercise Metabolism*）。

## 完成項目

| 動作 | 路徑 | 結果 |
|------|------|------|
| ch9 → ch4 attachments rename | `E:\Shosho LifeOS\Attachments\Books\biochemistry-sport-exercise-2024\ch4\` | 28 figs + 4 tables 重命名 fig-9-N → fig-4-N / tab-9-N → tab-4-N |
| ch1.md 重 ingest | `E:\Shosho LifeOS\KB\Wiki\Sources\Books\biochemistry-sport-exercise-2024\ch1.md` | 覆寫；舊版 493 行（中文敘事 PR C 風格）→ 新版 702 行（ch3-style 結構化英文 + bullet-定義 + 5 mermaid + 7 verbatim quotes + 11/11 PR C concept page wikilinks）；schema_version=2 / 13 figs 全 vision-described / ingested_at 2026-05-03 |
| ch4.md 新建 | `E:\Shosho LifeOS\KB\Wiki\Sources\Books\biochemistry-sport-exercise-2024\ch4.md` | 1053 行 / 28 figs / 4 tables / 6 mermaid / Proteins 完整章；用 `/tmp/2026-04-27-ch4-ingest-cache.json` 的 28 個 vision description 跳過 vision call；ingested_at 2026-05-03 |
| Book Entity 更新 | `E:\Shosho LifeOS\KB\Wiki\Entities\Books\biochemistry-sport-exercise-2024.md` | chapters_ingested: 1 → 4；狀態表 ch1-ch4 標 ✓ ingested |

## 待修修 review 後 GO 才動的 Phase 4（ch5-ch11 batch）

| Human ch | Walker nav | 標題 | Fig 數 | Table 數 | Walker LOC |
|----------|-----------|-------|------|--------|-----------|
| 5 | 10 | Carbohydrates | 38 | 2 | 622 |
| 6 | 11 | Lipids | 26 | 0 | 363 |
| 7 | 12 | Metabolic Regulation | 19 | 2 | 597 |
| 8 | 13 | Techniques | 17 | 3 | 303 |
| 9 | 14 | HIE | 26 | 2 | 1224 |
| 10 | 15 | Endurance | 44 | 0 | 1370 |
| 11 | 16 | HIIT | 36 | 3 | 1442 |
| **合計** | — | — | **206 figs** | **12 tables** | **5921 lines walker** |

**預估**（Opus 4.7 全程，per 修修指示「最高品質優先」）：
- Vision describe 206 figs（Opus 4.7 multimodal Read in-session）：~80-150 min
- Compose 7 章 ch3-style：~3-5 hr
- Concept extract + 4-action dispatch：~1 hr
- **Total wall time**: 5-8 hr
- **$ cost**: Max 200 quota（無額外 API 費）

下個 session pick up 時：
1. 再次確認寫到 **E:\Shosho LifeOS\**（不是 F:！見下方教訓）
2. 對 `E:\Shosho LifeOS\Attachments\Books\biochemistry-sport-exercise-2024\` 的 `ch10`-`ch16` 做 walker nav → 人類 ch reindex（ch10→ch5 / ch11→ch6 / ... / ch16→ch11）+ 內部檔案 fig-N-X → fig-(N-5)-X / tab-N-X → tab-(N-5)-X
3. 每章從 `/tmp/textbook-ingest-bse/chapters/ch{nav}.md` 讀 walker output（**注意**：/tmp 在 git-bash 解到 `C:\Users\Shosho\AppData\Local\Temp\`，Python Windows native 不認 /tmp 路徑，要用 `r'C:\Users\Shosho\AppData\Local\Temp\textbook-ingest-bse\chapters\chN.md'`）
4. 每章 vision describe 走 in-session Opus multimodal Read（一張一張或並行）
5. 用 ch4.md 那種 Python 組裝 script pattern（見 `C:\Users\Shosho\AppData\Local\Temp\write_ch4.py`）批量產 ch5-ch11
6. 中途暫停節奏：ch5 + ch6 完成後再 pause 一次給修修看（不要一口氣 7 章衝完）
7. 完工後 Book Entity status: complete + chapters_ingested: 11

## 教訓 1：vault 在 E: 不是 F:（最大坑）

整 session 我寫進 **stale F:\Shosho LifeOS\** 而不是 active **E:\Shosho LifeOS\**，等修修發現才復原（cp F: → E: + 重做 E: 上 attachments rename）。memory 之前只記了 repo 從 F: 搬到 E:（`project_disk_layout_e_primary.md`），vault 那塊一直寫 F:。

**已修 memory 三處**：
- `reference_vault_paths_mac.md`（這次 session 改寫）— E: active + F: stale + obsidian.json `"open":true` 查證方法 + 事件作教訓
- `project_disk_layout_e_primary.md` How to apply 加 vault 路徑也是 E:
- `MEMORY.md` L31 同步改寫

**下次防雷 SOP**：寫 vault 之前 verify Obsidian active vault：
```bash
cat /c/Users/Shosho/AppData/Roaming/obsidian/obsidian.json | python -m json.tool 2>/dev/null | grep -B1 '"open": true'
```

## 教訓 2：cached vision 是巨大省時

ch1 reuse 既有 frontmatter 13 figs description（PR C Opus in-session 跑出來的）+ ch4 reuse 上次 session pre-stage 的 `/tmp/2026-04-27-ch4-ingest-cache.json` 28 figs description → 兩章都跳過 vision call → 整輪 wall time ~30 min（vs 預估 30-60 min/章）。

→ ch5-ch11 沒有 cache，得 fresh vision describe，wall time / token usage 才是真實基準。

## 教訓 3：Python Windows native 不認 /tmp

```python
# ❌ Windows native python 不認
open('/tmp/file.json')   # FileNotFoundError

# ✅ 用 cygpath 轉成 Windows 絕對路徑
open(r'C:\Users\Shosho\AppData\Local\Temp\file.json', encoding='utf-8')
```

而且 default cp1252 codec 解 UTF-8 會 charmap 炸 → 永遠加 `encoding='utf-8'`。

## Reference

- 上 session pre-stage cache + handoff doc：`docs/plans/2026-04-27-ch2-pr-d-handoff.md`
- ADR：`docs/decisions/ADR-011-textbook-ingest-v2.md`
- Skill：`.claude/skills/textbook-ingest/SKILL.md`
- 上條 ingest memory：`project_ingest_v2_step3_in_flight_2026_04_26.md`（ch1/ch2 PR C 細節）
- vault path memory：`reference_vault_paths_mac.md`（這次更新）
- 桌機 disk layout：`project_disk_layout_e_primary.md`（這次更新）
