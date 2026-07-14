# ADR-052 卡片盒 Graph View — 催生連結的永久卡關係圖（reframe ADR-043 gate）

- 狀態：Proposed（v2）
- 日期：2026-07-14
- 決策者：修修
- 關聯：
  - **擴展 / 修訂 ADR-043**（Centaur Zettelkasten）— 明確地、經修修裁決地**在 gate 窗口內選擇先投**這個 purpose-built 低摩擦每日表面（見決策 2）；front-load N525 的 graph 子集；鎖死邊分類法；補上 hybrid 編輯與 ghost pass 機制。不 supersede ADR-043，紅線與 Friction Selection 全數沿用。
  - **落地 N525 的一部分**（`docs/plans/centaur-zettelkasten/Centaur-Zettelkasten-TaskPrompts-N520-N526.md`「Permanent browser + 卡片詳情」）——原標「後置」，本 ADR 把其中 graph 子集提前。
  - **詞彙同步** `agents/robin/CONTEXT.md` §Centaur Zettelkasten（本 ADR grilling 中已更新 `Link relationship` + 新增 `Candidate Link Ghost`；v2 再補「fleeting 非 status」澄清）。
  - 依賴 ADR-043 decision 4 的 `kb_indexer` 索引 `KB/Permanent/`（僅 scale-brake 前濾路徑需要；Slice 1 全庫 all-pairs 不需要）。

> **Panel audit trail（v1 → v2）**：本 ADR 經 3-way panel（Claude 起草 → Codex/GPT-5 + Gemini 2.5 Pro 審）。逐字 audit 存 `docs/research/2026-07-14-codex-adr052-graph-view-audit.md`、`docs/research/2026-07-14-gemini-adr052-graph-view-audit.md`。Codex = Approve-with-modifications；Gemini = Reject（主張 N=11 太早、先做最便宜的 text probe）。
>
> **修修裁決**：兩個 external 的 reject/defer 論證**共同架在一個推論上**——「5 週 0 連結＝行為訊號＝更好的 UI 修不好」。修修澄清 **0 連結是因為忙線上課程、沒時間，不是卡關、不是不想連**；且 Obsidian 內建 graph 通用、摩擦多、非為卡片盒設計，「web 版沒加值」的前提不成立；對每天都在用的個人工具，一個 purpose-built、符合用法、低摩擦的 UI 是**划算的初期投資**。故**採 Option C（建圖）**，但**吸收 panel 中與該推論無關、照樣成立的發現**：① 小-N ghost 噪音（統計事實）② decision 9 數字錯 ③ 寫入 endpoint 的紅線工程 ④ decision 2 誠實化。**v2 新增** 成熟度階梯正規化、fleeting 非 status 兩個決策。

## 脈絡

修修要在卡片盒總覽（`/kb`）把上方 4 塊改成可點、卡片攤在桌面；尤其「永久筆記」要做 Graph View：永久卡互相連結、自動分群、自由拖拉、點進去直接編輯；並問「把 MOC / Literature / Permanent / Project / Fleeting 五類整合成一個 View（總體＋個別）好不好」。

核對 codebase + live vault 後的事實：

- **總覽 4 卡目前只是計數**（[thousand_sunny/templates/kb/overview.html](../../thousand_sunny/templates/kb/overview.html)）；只有「專案」是連結（→ `/bridge/projects`），其餘三塊是 `<div>`。
- **既有 canvas 是 authoring-only**（N528，[thousand_sunny/static/kb_canvas.js](../../thousand_sunny/static/kb_canvas.js)）；其 C15 明訂「瀏覽既有卡關係圖屬 N525 之後另議」。
- **N525**（Permanent browser + 卡片詳情 + MOC + Literature view）＝這個需求，原標「後置」。
- **關係圖目前是空的**：live vault（`E:/Shosho LifeOS/KB`）有 11 張永久卡、全 `seedling`、單一主題（財富階梯）；0 條 typed edge、0 條卡對卡連結、0 個 MOC、0 張 fleeting；12 literature；8 candidate concept。`.obsidian` 在 vault 根 `E:/Shosho LifeOS/.obsidian`（KB 是其子資料夾，非 vault 根）。
- **AI 不能寫永久卡判斷內容**（ADR-043 紅線 decision 2）；只有修修親手寫。**0 連結的成因是「修修忙線上課程沒時間寫卡」，非行為卡關**（修修澄清，覆蓋 AI 的行為推論）。
- **Obsidian 內建 graph 對修修不夠用**：通用、摩擦多、非為卡片盒方法設計。
- **ADR-043 把完整 web authoring UI（Slice 2）gate 在「3 個月誠實測試」後**；本 ADR 決策時約進行 5 週。

## 決策

1. **做「催生連結」的 Graph View（`/kb/graph`），作為 purpose-built、低摩擦、站內每日表面。** 修修每天都在 Bridge 上做事，把「帶理由地連結」這種**高認知負荷**工作收進他已經在的地方、做到零摩擦，是移除 context-switch 稅、對每日工具有複利的**正當初期投資**。**圖本身（節點 + 手動連線 + 站內編輯）不依賴 ghost 品質就有價值**；ghost 是加在上面的 AI 輔助層。否決「純瀏覽 Obsidian-clone」（Obsidian 對修修不夠用、非卡片盒專用）與「先做五類整合 View」（有邊/MOC 前是空殼）。

2. **Gate 誠實化（取代 v1「圖就是 gate」）。** 不宣稱「圖即 gate、ADR-043 不變」。誠實表述：**明知在 ADR-043 gate 窗口內，仍由修修裁決先投這個表面**——因為 (a)「0 連結」是時間 confound（忙課程）非行為訊號，ADR-043「先證明習慣再建 UI」的前提在此不適用；(b) 對每日個人工具，低摩擦 purpose-built UI 是划算的前置投資。這是**有意識地放寬 ADR-043 的定序**，不是把 gate 玩掉。**成功/停損準則照留**：以「帶真實理由的人寫連結數、status 晉級（附理由）、跨週回訪」衡量，**不以頁面瀏覽 / ghost 生成數當成功**。

3. **定序**（三個 View 都是 destination）：Slice 1 = 永久卡「瀏覽＋手動連線＋站內編輯＋ghost 輔助」graph → Slice 2 = 疊文獻來源層 + 從叢集長出 MOC（ghost 隨語料多元漸強）→ Slice 3 = Project/Fleeting 外圍節點 + 取代 4 張數字卡的整合落地頁（總體＋個別同一表面不同 zoom）。**建卡即時連結提示（納入 Slice 1，Gemini/Codex 提、與建圖不衝突）**：建卡成功後即時提示「連一張既有卡，或寫一行為什麼不連」——打在脈絡最強的一刻。

4. **Ghost = 無類型的「可能相關對」偵測器，且為小-N 設計。** AI 畫虛線候選邊，**不標類型、不標方向**；修修點虛線 → 自己選 支持/反駁/延伸 + 寫理由 → 由虛變實。刻意不做 P-2 `judge_edges` 的類型＋方向判斷。**小-N 護欄（統計事實）**：11 張、單一主題時，proximity 幾乎每對都亮（55 對 → 毛球，比空圖更糟）；故 ghost **保守呈現**（每卡限量 top-1~2 / 提高門檻 / 語料太同質時不噴），**隨卡數與主題多元度變有用**。ghost 是輔助層，**不是圖的前提**。可附一行極中性「為什麼被標」線索，但不下關係判斷。

5. **邊分類法鎖死：支持 / 反駁 / 延伸（三種持久邊）。** 因果與其他細語意寫在人手寫的「理由」自由欄（因果 = 類型`延伸` + 理由「A 導致 B」），不升 enum。`類似（可合併）`不是邊——是 ghost/lint 的合併提示（原子性）。抗拒類型增生。（收斂既有 glossary 漂移：code = support/refute/extend、CONTEXT 敘述曾寫「支持/矛盾/延伸/舉例」。）

6. **編輯寫回＝ hybrid + 紅線工程。** Bridge 只做窄 human 寫入：採用 ghost（append `支持:: [[Y]] — 理由` 進既有卡正文）+ 升 status——新 human-authoring endpoint，`author: human`，只 append 正文邊行、保留 AI 記帳欄。正文重寫走 `obsidian://` deep-link。**Slice 1 不 rename**。**必備紅線工程（Codex §2）**：
   - **防 race / lost write**：append-edge 與 AI 的 `update_permanent_bookkeeping` 都是「讀全檔→改→寫全檔」，重疊會靜默互蓋。寫入前做 **version/hash 前置檢查或檔案鎖**，變動過就不硬蓋。
   - **status 是判斷型欄位**：升 status 那條路**保證只有人點得到**，無 agent/自動後門。
   - **no-agent-path 測試**：斷言沒有任何 agent/shared service import 或呼叫這兩個 endpoint（紅線可證明，非「口頭保證」）。
   - 記一筆 append-only log（誰/何時/哪張卡/哪條邊）。

7. **版面＝ force-directed、session-only。** 人寫實線邊＝強彈簧（分群自然湧現）、ghost 虛線＝弱彈簧；不另寫分群演算法、不畫硬框（有名字的分群＝MOC＝Slice 2）；可選來源背景色調。拖拉位置 session-only；未來要持久化 → sidecar `KB/.centaur/graph_layout.json`，**絕不寫進卡 frontmatter**。

8. **Ghost pass 機制。** 語料＝全 `KB/Permanent/` 正文＋精簡 metadata，一次 all-corpus call（非兩兩比），前置 token preflight。搭 5am 每日回顧 job 一起算 → 快取 sidecar `KB/.centaur/ghost_edges.json`；graph 頁只讀快取（秒開）；另給「重新探勘關聯」手動按鈕。過濾：已有人寫實線邊的對不再出 ghost；近重複標「考慮合併」。

9. **規模計畫走一個接縫函式（v2 修正數字）。** `select_candidates(delta, corpus)`：今天回傳「全部既有」（all-pairs）；撞煞車後改「增量 + 前濾」（只判 新/改動卡 × top-K）。**LLM 永遠不看全庫，LLM 那步 context 不會爆。** 修正後的 grounded 數字（**指名部署預算：Claude，200K context、可用輸入 ~150K**；不是普世模型事實）：
   - 一張 CJK 卡 ≈ 300–500 tokens（中文常 ~1.5–2 tokens/字）。單次 all-corpus call **上限約 ~300 張**（500×~400 = 200K 已超 ~150K 可用，故 v1 的「300–500」偏樂觀，取下緣）。對上 ADR-043 的 200–500 文件煞車。
   - 10k 張純標題 ≈ 20–40 萬 tokens（若整包塞才會爆 200K，故要前濾）。
   - **向量索引大小修正**：v1「10k≈10MB」把**語料文字大小**誤當**向量索引大小**。實際 10k × 1536 維 × 4 bytes(float32) ≈ **~60MB**（3072 維 ≈ ~120MB）；要壓到 ~10–20MB 需降維（256–512 維）或量化。語料文字才是 ~10MB。
   - **跨 MOC 召回**：MOC 當**硬分割**前濾會漏掉跨 MOC 連結（ghost 最高價值）；但重疊 MOC / 全域第二遍可部分救回，且**現在 0 個 MOC，本就不是 day-1 煞車手段**。到規模時＝「MOC 收窄 + permanent-only 小向量保跨域」，等撞煞車看 MOC 實況再定。
   - **CJK 跨語 alias**（卡片盒↔Zettelkasten，ADR-043 已旗標）：ghost 生成要餵 alias / 用能認 CJK 同義的路徑，否則會漏人工維護的同義連結。

10. **進入點與範圍。** 路由 `/kb/graph`；overview「永久筆記」卡改連結。CSP `/kb*` = `script-src 'self'` → 手刻極小 force sim 放 `/static/kb_graph.js`（無 CDN，比照 canvas 無-lib 慣例）。Slice 1 只接「永久筆記 → graph」；其餘三塊維持現狀。（tags 現況：永久卡 frontmatter 目前**無** `tags` 欄。）

11. **成熟度階梯正規化（v2 新增；先修再做 status-bump）。** 現有漂移：`overview.html:29` 與 `kb_review.py:804` 用 `growing`，`agents/robin/CONTEXT.md:112` 用 `budding`。**正規化 enum 為 `seedling → budding → evergreen → superseded`（對齊 glossary），修正兩處 code**；顯示 label 用「發展中」（取代現有「成長」）。**零遷移成本**（11 張全 `seedling`，中間階未被用過）。

12. **Fleeting 維持「筆記類型」，不是永久卡 status（v2 新增）。** 提議「把 fleeting 加進 status 當最底階」否決，因為它撞紅線的資料夾邊界：**fleeting 是「Nami/機器人可代寫」的（最低摩擦捕捉，`KB/Fleeting/`、時間戳命名、triage 生命週期 open/processed）；永久卡是「AI 碰不得」的（`KB/Permanent/`、概念命名、by-construction 紅線）**。合併會逼紅線從「看資料夾」退化成「看 status 才知能不能寫」的條件式例外——正是紅線刻意避開的複雜度；且命名（時間戳 vs 概念）、原子性（前原子種子 vs 一概念）皆不合。**改用兩招滿足「連續感」直覺**：(a) fleeting 開成永久卡時**保留來歷**（新卡 `source_refs` 記「源自某則 fleeting」，不再回收即忘）；(b) fleeting 在整合圖裡是**自己的一種外圍節點**、箭頭指進永久卡（Slice 3）——視覺統一、資料模型不犧牲紅線。

## 決策依據（已驗證 file:line）

- 總覽 4 卡計數、僅專案為連結：[overview.html](../../thousand_sunny/templates/kb/overview.html):25-55。
- canvas authoring-only + C15 defer browse-graph：[Centaur-卡片畫布-規格-v1.md](../plans/centaur-zettelkasten/Centaur-卡片畫布-規格-v1.md) C15。
- N525 後置：[TaskPrompts-N520-N526.md](../plans/centaur-zettelkasten/Centaur-Zettelkasten-TaskPrompts-N520-N526.md):60。
- create-only、409 if exists、無 edit 入口：[kb_review.py](../../thousand_sunny/routers/kb_review.py):330-358；`_assemble_permanent_markdown`:266；read-only peek:670。
- AI 只能記帳、白名單 `source_refs/modified/aliases`、永不寫正文/status：[permanent_layer.py](../../shared/permanent_layer.py):37/69/134-207。
- 邊分類 support/refute/extend：[schemas/daily_review.py](../../shared/schemas/daily_review.py):37、[kb_review.py](../../thousand_sunny/routers/kb_review.py):58。
- P-2 判類型+方向（graph ghost 刻意不用）：[daily_review.py](../../agents/robin/daily_review.py):665。
- fleeting 機制：daily_review `FleetingItem`（via slack/mobile/obsidian、寫入權限人+Nami）；`_process_fleeting`（[kb_review.py](../../thousand_sunny/routers/kb_review.py):541）翻 status: open→processed + 送回收桶；N526 = Nami Slack → `KB/Fleeting/{timestamp}-{slug}.md`。
- status 漂移：`overview.html:29`+`kb_review.py:804`=`growing` vs `CONTEXT.md:112`=`budding`。
- live vault：11 permanent（全 seedling）、0 edge/link/MOC/fleeting、12 literature；`.obsidian` 在 vault 根（KB 為子資料夾）。

## Considered options（否決記錄）

- **Obsidian-browse clone graph** — 否決：Obsidian graph 對修修通用、摩擦多、非卡片盒專用（修修裁決，覆蓋 v1「Obsidian 已提供」）。
- **先做五類整合 View** — 否決：有邊/MOC 前是空殼。
- **全 in-Bridge 編輯器** — 否決：重造 Obsidian 編輯器；改 hybrid。
- **純 Obsidian（graph 當 launcher）** — 否決：失去 ghost 一鍵採用差異化。
- **Typed ghost（AI 預選關係類型）** — 否決：越紅線、最易錯。
- **新增 因果 / 類似 為第四、五種持久邊** — 否決：類型增生；走理由欄 / 合併。
- **fleeting 當永久卡 status** — 否決：撞資料夾紅線（fleeting 機器人可寫 vs 永久卡 AI 不可寫）+ 命名/原子性不合（決策 12）。
- **Panel 的 defer/read-only（Gemini reject、Codex read-only Slice 1）** — 修修否決其前提：0 連結是時間 confound 非行為訊號；但**採納**其與該前提無關的技術發現（小-N ghost、數字、紅線工程、gate 誠實化）。

## Consequences / 紅線註記

- **Human 寫入 lane 從 1 變 3**：create + adopt-edge + status-bump 都是 human-authoring；AI lane 仍只有 `update_permanent_bookkeeping`。新 endpoint 須複用「保留正文/其餘欄、只動目標」邏輯 + 決策 6 的 race 防護 + no-agent-path 測試。`update_permanent_bookkeeping` 會 reserialize 整塊 frontmatter（判斷欄可能有格式 churn 即使值未變）——新寫入路徑注意最小化改動。
- **兩個新 sidecar**（`ghost_edges.json`、選配 `graph_layout.json`）落 `KB/.centaur/`，走 Syncthing 同步；非 vault 內容、不入 git。
- **在 gate 窗口內建**：是有意識的定序放寬（決策 2），成功/停損準則照留；若證明無用（跨數週仍 0 人寫連結、且非時間因素），停損退回並記錄。

## 風險與緩解

- **小-N ghost 噪音**：保守呈現 + 隨語料多元漸強（決策 4）；圖的手動連線/編輯價值不依賴 ghost。
- **紅線寫入風險**：決策 6 的 race 防護 + status 人專屬路徑 + no-agent-path 測試。
- **數字/規模**：決策 9 修正 + 接縫函式 + token preflight。
- **在 gate 內建的機會成本**：決策 2 誠實承認 + 停損準則；這是修修對「每日工具低摩擦投資」的裁決，非工程共識。
- **與 Obsidian 冗餘**：修修裁定 Obsidian graph 不夠用；差異化（ghost、站內一鍵採用、紅線、purpose-built 低摩擦）是立論基礎，須交付否則退化成更差的 Obsidian。

## 切片計畫

- **Slice 1**：`/kb/graph` 頁 + 手刻 force sim；graph UI（節點 + 手動連線 + 站內編輯 + 實線/ghost 邊、卡片節點 + LOD、session-only 拖拉）；窄 human endpoint（adopt-edge、status-bump）+ 紅線工程（決策 6）；ghost pass（all-pairs + 接縫 + token preflight + sidecar 快取 + 搭 5am + 手動 refresh + 小-N 保守呈現）；`obsidian://` 正文編輯；成熟度階梯正規化（決策 11）；overview 永久筆記卡改連結；建卡即時連結提示（決策 3）。**不含** rename、MOC、literature/project/fleeting 節點層。
- **Slice 2**：疊文獻來源層；從叢集長出 MOC；scale-brake 前濾（若撞 ~300）。
- **Slice 3**：Project/Fleeting 外圍節點（fleeting 帶來歷，決策 12）；取代 4 張數字卡的整合落地頁。
