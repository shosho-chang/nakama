---
name: Brook 三類風格側寫 - 交叉分析
description: 讀書心得 / 人物故事 / 科普文章三類風格的共通點、分歧點、Brook 使用守則
agent: brook
extracted_at: 2026-04-22
sample_total: 36
sample_breakdown: {book-review: 10, people: 13, science: 13}
---

# Brook 三類風格側寫交叉分析

三個 agent 並行抽取後，本檔做 cross-cutting 歸納：**什麼是修修「全類別」通用的聲音指紋**、**三類之間的分歧在哪**、**Brook 載入 profile 時該怎麼判斷**。

---

## 1. 聲音指紋（三類共通、Brook 永遠要做）

| 特徵 | 說明 | 來源類別 |
|---|---|---|
| 台灣口語句尾助詞 | 啦 / 喔 / 耶 / 嘛 / 囉 / 吧，每篇自然散佈 8-15 次 | 全三類 |
| 括號吐槽 | `（By the way 這家公司股價跌了 93%）`、`（絕對不會叫你去尋找熱情）` 式口語插播 | 全三類 |
| 極短收束句 | 「屢試不爽。」「就這樣。」用於段落結尾製造節奏停頓 | 書評 / 科普 |
| 繁體中文但術語保留英文 | BDNF、VO2 max、FIRE、mTOR、NEAT、BEM — 術語並列英文 | 科普 / 人物 |
| 數字量化 | 把虛的說法變成具體數字（「91% 風險」「每天 10 分鐘」「8 年裡見過 83 隻」）| 全三類 |
| 個人故事錨點 | 每篇都要有一段修修自己的經驗接回主題（裸辭 / 跑步 / 育兒 / 讀書 / 環島）| 全三類 |

**Brook 絕不能產出的**：
- 無來源宣稱（「研究顯示」但沒連結）
- 醫療診斷或療程建議
- 純罐頭話式 CTA（「快來訂閱」無情境）
- 缺少個人錨點的純資料堆疊

---

## 2. 三類分歧表（Brook 依 category 切換）

| 維度 | 📖 讀書心得 | 🎙️ 人物故事 | 🧪 科普 |
|---|---|---|---|
| **字數** | 5,000-12,500 | 1,000-4,000 | 4,000-7,000 |
| **emoji 使用** | **完全不用**（硬規則） | 🧠 招牌開頭；內文適度 | 頻繁（🏃💪🧠⚠️💡 章節標示） |
| **「我」vs「你」** | 「我」優勢 2-3:1（大學長姿態） | 以來賓第三人稱為主；修修少跳出 | 「你」高密度（教學姿態）+ 「我」第一人稱證言 |
| **結構彈性** | 書決定（法則式沿用 / 散文式重建 3-5 支柱 H2） | 章回小說式 H2（動詞驅動）+ 金句置中引言 | 四拍固定：迷思鉤子 → 研究 → 比喻 → 行動清單 |
| **開場鉤子** | 個人故事或反直覺聲明 | 懸念反問 / 反差身份 | 迷思破解 / 震撼數字 / 最新研究 |
| **引用方式** | 原書觀點 30% + 個人詮釋 70%，不做 bibliography | 引述來賓原句為主，podcast 摘錄感 | 「年份 + 期刊 + 超連結 + 白話結論」四件套，每千字 2-4 個 |
| **比喻類型** | 個人人生片段當錨 | 流行文化 reference（魯夫 / 永不放棄 / 火影） | 中心物件比喻（BDNF=肥料、mTOR=總承包商） |
| **結尾** | 金句 → 電子報 CTA (shosho.tw/free) → 購書聯盟連結 | **刻意留白 / 展望式**（預告片，非懶人包） | 行動 takeaway / 主要外帶 / 相關科普延伸 |
| **讀者價值主張** | 我幫你讀完了，這是我的詮釋 | 幫你認識這個人，去聽 podcast | 幫你破解迷思 / 科學知識口袋化，能馬上用 |

---

## 3. Brook compose 流程的含意

### 3.1 文章類型判斷（在 compose.py 的前置步驟）

輸入進來時先分類：

```
if topic 有 ISBN 或書名或「這本書」→ book-review
elif topic 有「訪談」「EP{n}」「podcast 來賓」→ people
elif topic 有研究主題 / 健康概念 / 如何做 → science
else → 反問修修意圖
```

### 3.2 Profile 載入

對應三份檔載為 system prompt 的 appendix：
- `agents/brook/style-profiles/book-review.md`
- `agents/brook/style-profiles/people.md`
- `agents/brook/style-profiles/science.md`

**不要三類都塞進 context** — 會稀釋訊號。只載入當前 category 的那份。

### 3.3 Few-shot 輪替

每份 profile 各有 3 個 few-shot 範本（A / B / C）。建議：

- 預設載入 **A + B**（最具代表性的兩個）
- 若 compose 失敗或風格偏移，重試時改用 **A + C** 或 **B + C**
- 避免每次都餵同樣 3 個 → Brook 只會學到那 3 篇的樣子

### 3.4 科普的 unique 處理

科普文需要**額外工具**：
- PubMed / Unpaywall fetch（Robin 已做過類似的管線）
- 研究引用 rigor check（每千字 2-4 個引用，缺就要補或降低字數）
- 中心比喻生成（給 Brook 一個 prompt sub-step：「這篇的中心物件比喻是什麼？先想好再寫」）

這意味著 Brook 的科普 composer 可能要分兩階段：**research 階段 → compose 階段**，而不是一槍出稿。

---

## 4. 未涵蓋的風格（warning）

這 36 篇樣本**不代表修修所有寫作**。已知未抽取的類別：

| 類別 | 推測特徵 | 處理 |
|---|---|---|
| 其他（7 篇） | 意見文 / 創作心路 / 平台宣言 | Brook 遇到時 → 回報修修要求人工處理 |
| 電子報 | 比部落格更口語、更短、有週期性 | 另建一套 profile（`newsletter.md`，Phase 2） |
| IG / YT 平台改寫 | 視覺導向 + 短句 | 屬於 repurpose flow，不走 Brook compose |

---

## 5. Profile 迭代機制

Style profile 不是一次定死。建議：

1. **每 10 篇 Brook 產出後**：修修抓一個 sample 批註「像 / 不像」，回饋寫進 `_iteration-log.md`
2. **每季**：用當季修修親寫的 2-3 篇當 incremental sample，追加到 profile 的 `## 近期風格漂移` 區塊
3. **重訓條件**：當 HITL 拒絕率連續 4 週 > 30%，整份 profile 重新 re-extract

---

## 6. Known limitations（模型能力邊界）

- 書評的**個人錨點**是修修人生經驗（裸辭 / 騎車環島 / 創業），Brook 沒有這些 → compose 時 **禁止虛構個人經驗**，要嘛改用「我看很多人 / 有讀者跟我說」式敘述，要嘛由修修人工補錨點
- 人物文要**認識來賓**，Brook 不知道 podcast 聊了什麼 → 必須先餵逐字稿或重點摘要
- 科普的**中心比喻**是最難的創造性 step → Brook 應該提出 2-3 個比喻候選給修修挑，而非自作主張

---

## 7. 連到 Phase 1 的具體下一步

1. `agents/brook/compose.py` 增加 `load_style_profile(category)` helper
2. 系統 prompt template 結構化為：`[identity] + [category guidance] + [few-shot A] + [few-shot B] + [user topic]`
3. Usopp 收到 draft 時，metadata 記錄 `style_profile_version`，未來 profile 更新能 replay
4. `/bridge/drafts` HITL UI 顯示該 draft 用了哪份 profile + 哪兩個 few-shot，方便 debug 偏移

---

## 8. 檔案清單

```
agents/brook/style-profiles/
├── book-review.md         # 10 篇樣本，22KB
├── people.md              # 13 篇樣本，21KB
├── science.md             # 13 篇樣本，25KB
└── _extraction-notes.md   # 本檔
```

總樣本 36 篇，抓取日期 2026-04-22。
