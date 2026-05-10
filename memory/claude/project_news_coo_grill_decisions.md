---
name: News Coo Grill 拍板總表 2026-05-10
description: News Coo browser extension MVP grill 全決策表；對應 PRD docs/prds/2026-05-10-toast-nakama-inbox-importer.md
type: project
---

2026-05-10 grill session 結論。對應 PRD `docs/prds/2026-05-10-toast-nakama-inbox-importer.md`（Codex draft 早上版），用 News Coo 取代 PRD 中的「Toast」字眼。

## 命名 + Scope

- **Repo**: `E:\news-coo`（standalone）
- **Manifest name**: `News Coo`
- **Code identifier**: `NewsCoo`
- **Scope（嚴守）**: 瀏覽器 extraction + delivery 進 vault；**不做翻譯、bilingual format、glossary**
- **撤回**: 同日先前提的 `Den Den Mushi` 命名 + Toast fork 計畫

## 戰略（Path 2d 變體）

| 元件 | 角色 | PR 獨立性 |
|---|---|---|
| News Coo | 新 Chrome extension | 獨立 PR，先 ship |
| Robin (auto-translate trigger) | 既有 agent + 新 watcher 接 `Inbox/kb/*.md` 沒 sibling 的檔 → 跑 `translator.py` | **獨立議題，不在這次 grill / PR scope** |
| Robin Reader (`templates/robin/reader.html`) | 既有，不動 | — |
| `shared/translator.py` + `prompts/robin/translation_tw_glossary.yaml` | 既有，不動 | — |

## 拍板決策

| Q | 決策 |
|---|---|
| **寫 vault 方式** | File System Access API 直寫，pick vault root 一次拿 persistent handle |
| **Codebase 起手** | 新 repo from scratch + npm Defuddle dep；不 fork Toast、不 fork Clipper |
| **Build / test stack** | Rolldown + TypeScript strict + Vitest + happy-dom（Toast 同款）|
| **MV 版本 / permissions** | MV3, `activeTab` + `storage` + `contextMenus`，host_permissions `<all_urls>` |
| **Selection-aware clipping** | ✅ 加（反白 → 只 clip 反白範圍；frontmatter `extraction_method: selection`）|
| **Pre-clip highlights → annotations seed** | ✅ 加（簡化版，no SVG overlay；highlights 收 chrome.storage per tab，clip 時併入 frontmatter `highlights: []`）|
| **Right-click context menu** | ✅ 加（兩 entry：「Clip page」/「Clip selection」）|
| **Keyboard shortcut** | ✅ 加（default `Alt+Shift+N` quick clip；popup 走 toolbar icon）|
| **Quick mode vs Preview mode** | ✅ 兩模式並存。Default = preview（popup 開）；keyboard / context menu = quick |
| **Image download** | ✅ **必備**，不是 toggle。對齊 `shared/image_fetcher.py` 慣例：寫 `KB/Attachments/web/{slug}/img-N.{ext}`；markdown 用 vault-relative ref；20MB 上限；15s timeout；失敗保留 remote URL |
| **Per-site detector** | ✅ V1 只做 PubMed（抽 `doi`/`pmid`/`journal`）；其他 default Defuddle metadata |
| **Frontmatter shape** | 對齊 PRD §5.2/§5.3。原文檔強制欄位：`title`, `source_url`, `canonical_url`, `captured_at`, `source_type: web_document`, `stage: 1`, `lang`, `site_name`, `author`, `published`, `extraction_method`, `news_coo_version: 1`。新增（vs PRD）：`highlights: []`, `selection_only: bool`, PubMed sites 加 `doi`/`pmid` |
| **Slug 策略** | Defuddle title → slugify (lowercase + 替換非 word chars + 中文保留 + 50 char 上限)。對齊 PRD #509 規則 |
| **Dedup 行為** | News Coo 只做檔名碰撞檢查：FSA `getFileHandle({create: false})` 試讀。Preview mode：popup 顯示「已存在 5/3 clip 過」+ 三選「Open existing / Overwrite / Save as -2」。Quick mode：自動 append `-N` 不 overwrite。Canonical URL dedup（跨 slug）**deferred** 給 Robin |
| **FSA handle 持久化** | IndexedDB 存 `FileSystemDirectoryHandle`；每次 extension 啟動 verify 仍 readable，失效則 prompt re-pick |
| **Translation Reader URL** | News Coo 寫完 vault 後**不**自動開 Reader（Reader 是 Robin 的 surface，News Coo 不耦合）；popup 顯示「✓ Saved to Inbox/kb/{slug}.md」+ vault path 文字（user 自行去 Robin Reader 開）|
| **Auth/CORS for image fetch** | 從 content script `fetch()` 圖片網址（同源/CORS-allowed）；CORS 失敗的圖保留 remote URL + frontmatter 標 `images_partial: true` |
| **i18n** | en + zh-TW 兩 locale，TS const 寫死 |
| **Tests** | Defuddle wrapper / frontmatter / slug / FSA writer / image fetcher（mocked） vitest 覆蓋 |

## 從 Clipper 抄碼明細（全加 MIT attribution header）

| 檔 | 來源 | 估 LOC |
|---|---|---|
| `src/content/extract.ts` | Clipper `src/content.ts:199-296`（Defuddle wrap + shadow DOM flatten + URL normalize）| ~100 |
| 全部其他 | 自寫 | ~700-800 |

**總估**: ~800-900 LOC + ~250 LOC tests

## 與 Robin 介面（隱式 contract）

News Coo 寫的檔案 Robin 之後（獨立 PR 後）會：
1. 偵測到沒 `-bilingual.md` sibling
2. 跑 `translator.py` + glossary
3. 寫 `{slug}-bilingual.md`
4. 兩檔都 #509 認得（一個 logical Reading Source）

**News Coo 該保證**：
- Frontmatter `source_type: web_document`, `stage: 1`, `lang` 必須有
- Markdown body 是乾淨主內容（無 nav/ads/sidebar）
- Image refs 是 vault-relative（不是 remote URL）

## 不做（嚴守 scope）

- 翻譯、glossary、bilingual format
- Reader 渲染
- LLM call（任何）
- Side panel（V2）
- History log（dedup 靠 vault check）
- Stats（虛榮）
- Multi-vault support（V2）
- 其他 site detector（除 PubMed）
- YouTube transcript / video（V2）
- Firefox / Safari（V2）
- Robin auto-translate watcher（**完全獨立 PR，不在這次 scope**）

## 實作 slice 規劃（建議順序）

1. **S1 — Skeleton**：repo init + manifest + rolldown + vitest + Hello World popup
2. **S2 — Extraction**：integrate Defuddle, content script, return clean markdown + metadata（含 PubMed detector）
3. **S3 — FSA writer**：vault picker + handle persistence + write `Inbox/kb/{slug}.md` + frontmatter generator + slug + dedup
4. **S4 — Image fetcher**：對齊 `image_fetcher.py` 行為，寫 `KB/Attachments/web/{slug}/`
5. **S5 — UX surfaces**：popup preview / quick mode / context menu / keyboard shortcut
6. **S6 — Selection + highlights seed**：selection-aware clip + chrome.storage per-tab highlights collector
7. **S7 — Polish**：i18n / error states / CORS-fail fallback / tests 補齊

每個 slice 獨立可 merge，落 `news-coo` repo PR。
