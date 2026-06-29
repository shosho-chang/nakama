---
name: draft-article
description: >
  從一本已讀完／已 ingest 的書或文章，讀使用者自己的畫線與註解，產出一篇**理性、低修飾的
  「原子文章骨架草稿」**——把書的論點與使用者的論據乾淨陳述出來，交給使用者自己加聲音、情緒
  與起承轉合。只要使用者想把「讀過的東西＋自己的畫線/註解」變成一篇可以接著編輯的長文骨架，
  就用這個 skill。觸發語包括 /draft-article、「把《X》寫成一篇文章」「幫我起一篇 <書名> 的草稿」
  「整理一下我讀的 <書> 的重點寫成介紹」「draft an article from my notes on X」。即使使用者沒說
  「草稿」兩字、只說「幫我把我讀的 X 整理成一篇」也要觸發。不要用在：替既有草稿潤稿/定稿/加聲音
  （這只出理性骨架）、社群貼文（未來的 draft-post）、把素材收進 KB（那是 /ingest）、或重抽風格
  側寫（那是 style 抽取）。
argument-hint: [書名 或 slug]
---

# draft-article — 把一本書整理成理性的原子文章骨架草稿

把一個**已經 ingest 進 KB 的 source**（書／文章），整理成一篇**理性、低修飾的長文骨架**。
輸出是 **draft（骨架初稿）**：書的論點 ＋ 使用者的論據，乾淨陳述。

**核心分工：這個 skill 只出骨架，不出聲音。**

- **書 ＝ 論據**，**使用者的 `note::` ＝ 論點**。兩者都要在，且以**理性、陳述句**寫出來。
- **聲音、情緒、口語、起承轉合的銜接，全部留給使用者自己加。** 不要替他先寫好。
- 為什麼這樣分？草稿是給人接著大改的；一份「假裝有聲音」的草稿，反而蓋掉使用者自己的語感、
  更難改。**忠實、密實、低修飾**的骨架，比硬套聲音有用。

這個 skill 的價值在於：**忠實萃取書的邏輯結構、把使用者埋在 note 裡的論點一條不漏地浮出來、
且絕不虛構**——而不是模仿文筆。

---

## 流程

### 0. 解析輸入 → slug

```bash
VAULT=$(python -c "from shared.config import get_vault_path; print(get_vault_path())")
ls "$VAULT/KB/Literature/" | grep -i "<關鍵字>"
```

- 唯一命中 → 用它。多個 / 找不到 → 列候選問使用者，別猜。
- Literature Note 不存在 → 告訴使用者「這本還沒 ingest，要先 /ingest」，停。

### 1. 載入素材（KB，唯讀）

| 檔案 | 角色 |
|---|---|
| `KB/Literature/<slug>.md` | **主骨**：逐章「畫線（`>` 引用）＋使用者 `note::`」。`note::` 是論點來源。 |
| `KB/Annotations/<slug>.md` | 完整畫線/註解 store，查證用 |
| `KB/Raw/...<slug>...` | 全書／全文原文，查證特定數據、補沒畫到但需要的脈絡 |

> ⚠️ Literature Note 結尾的 `## 🔗 KB 相關（AI 撈，FTS5）` 區段常常整片是「（無 KB 命中）」
> —— 死區雜訊，compose 時完全跳過。

### 2. 盤點兩條素材線（不載入聲音側寫）

理性模式**不套風格側寫、不讀 `agents/brook/style-profiles/`**。改成盤點：

- **書的邏輯骨架**：這本書的主要論點 / 框架 / 數據有哪些？（決定 H2 結構）
- **使用者的論據**：逐條掃 `note::`，把使用者的解讀、反駁、立場、在地化補充列出來。
  這些是草稿的差異化內容，**一條都不能漏**。

### 3.（選配）掃 KB Wiki 撈補充事實

```bash
grep -rl "<關鍵概念>" "$VAULT/KB/Wiki/Concepts" "$VAULT/KB/Wiki/Entities"
```

撈相關 Concepts / Entities 當補充事實。**只能正向引用（KB → 草稿）**，素材不足就跳過。

### 4. compose 理性骨架

依**書的邏輯**搭 H2 結構（一個主題一節，數量隨書而定，清楚優先）。每節：

1. **陳述書的論點**——理性、客觀，附必要數據。
2. **接上使用者相關的 `note::` 論據**——以陳述句寫出他的立場/反駁/補充。

紀律（違反任一 = 退回聲音模式，錯）：

- **理性、陳述句、低修飾。** 砍掉浮誇形容詞（最猛、超實用、驚人、背脊發涼…）。
- **不要台味句尾助詞**（啦/喔/吼/耶/嘛…）、**不要 emoji**、**不要驚嘆號堆疊**。
- **不要情緒鉤子 / 懸念**（「一定要看到最後喔」）、**不要起承轉合銜接語**（「我們繼續看下去」「回到開頭賣的關子」）——那是使用者的工作。
- **絕不虛構**：每個個人立場 / 數字 / 經歷都必須對得到某條真實 `note::` 或 source。沒有就不要寫。寧缺勿造。
- 不要為了篇幅灌水。骨架本來就比成品短，密實即可。

### 5. 寫到 Output（非 KB）

```
$VAULT/AgentOutputs/brook/drafts/<slug>-draft-<YYYY-MM-DD>.md
```

frontmatter：

```yaml
---
title: "《<書名>》介紹草稿（理性骨架）"
source: "[[KB/Literature/<slug>]]"
draft_mode: rational-scaffold
draft_status: draft
generated_by: draft-article
---
```

（`AgentOutputs/brook/drafts/` 不存在就建。日期用 `date +%F`。）

### 6. 跑理性骨架 lint —— 守住別讓聲音滲進來

```bash
python .claude/skills/draft-article/scripts/lint_draft.py \
  "$VAULT/AgentOutputs/brook/drafts/<檔名>"
```

退出碼 1（FAIL）= ornament 滲入（emoji、台味助詞 >8、驚嘆號過多），代表 compose 退回去模仿
聲音了 → 清掉、回到理性，重跑 lint 直到無 FAIL。lint 同時印出**語意自審 checklist**
（覆蓋度 / 不虛構 / 無情緒銜接）——這些 lint 驗不了，由你逐項確認。

### 7. 交付

交草稿路徑 + lint 結果 + 一句話：**「這是理性骨架，聲音／情緒／起承轉合請你自己接。」**
並指出哪些是書的論點、哪些是從使用者 note 浮出來的論據。

---

## 硬邊界（絕不踰越）

- **只出理性骨架，不出聲音。** 不套風格側寫、不加情緒、不寫起承轉合——那是使用者的工作。
- **Provenance 單向**：只能 KB → 草稿。**絕不**回寫 `KB/`（Literature / Raw / Annotations / Wiki）。
- **絕不虛構**個人經歷 / 數字 / 立場——每一條都要對得到真實 `note::` 或 source。
- **不碰** `Journals/`。
- **不定稿、不發布、不潤色成成品。**

---

## Next Step

草稿交付後，視情況提示（用 AskUserQuestion，opt-in，別自動跑）：

```
理性骨架已落在 AgentOutputs/brook/drafts/。接下來想做什麼？
A) 我自己加聲音、做大改（這本來就是骨架）
B)（未來）draft-post — 從這篇切出社群貼文
C) 不用了，這樣就夠
```
