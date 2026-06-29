---
name: draft-article
description: >
  從一本已讀完／已 ingest 的書或文章，讀使用者自己的畫線與註解，套用使用者的風格側寫，
  compose 一篇 5–6k 字以上、接地的「原子文章草稿」（draft，不是定稿）。只要使用者想把
  「讀過的東西＋自己的畫線/註解」變成一篇長文初稿，就用這個 skill。觸發語包括
  /draft-article、「把《X》寫成一篇文章」「幫我起一篇 <書名> 的草稿」「用我的風格寫 <書/主題>」
  「這本書的讀書心得幫我寫初稿」「draft an article from my notes on X」。即使使用者沒說「草稿」
  兩字、只說「寫一篇關於我讀的 X」也要觸發。不要用在：替既有草稿潤稿/定稿（這只出初稿）、
  社群貼文（未來的 draft-post）、把素材收進 KB（那是 /ingest）、或重抽風格側寫（那是 style
  抽取，不是寫稿）。
argument-hint: [書名 或 slug]
---

# draft-article — 用你的聲音，把一本書寫成原子文章草稿

把一個**已經 ingest 進 KB 的 source**（書／文章），用使用者自己的風格側寫，寫成一篇
長文初稿。輸出是 **draft（初稿）**，不負責定稿、潤稿或發布 —— 使用者拿去做大改。

**核心原則：書＝論據，使用者的 `note::`＝論點。** 原書觀點 vs 個人詮釋比例約 **55:45**。
跟一般「書摘」最大的差別：每個書中觀點後面，幾乎都要接上使用者自己的真實經歷、反駁、
或在地化延伸。沒有個人錨點的段落 = 死掉的書摘。

這個 skill 的差異化價值不只是「會寫」，而是**「交稿前會自動檢查像不像使用者本人」**
（§10 self-lint gate）。寫完一定跑 lint，硬傷重寫再交。

---

## 流程

### 0. 解析輸入 → slug

使用者給的是書名或 slug。在 vault 找對應的 Literature Note：

```bash
VAULT=$(python -c "from shared.config import get_vault_path; print(get_vault_path())")
ls "$VAULT/KB/Literature/" | grep -i "<關鍵字>"
```

- 找到唯一一個 → 用它。
- 找到多個 / 找不到 → 列出候選問使用者，別猜。
- Literature Note 不存在 → 告訴使用者「這本還沒 ingest，要先 /ingest」，停。

### 1. 載入素材（KB，唯讀）

讀三份，角色不同：

| 檔案 | 角色 |
|---|---|
| `KB/Literature/<slug>.md` | **主骨**：逐章「畫線（`>` 引用）＋使用者 `note::`」。`note::` 是論點來源。 |
| `KB/Annotations/<slug>.md` | 完整畫線/註解 store，查證用 |
| `KB/Raw/...<slug>...` | 全書／全文原文，查證特定數據、補沒畫到但需要的脈絡 |

> ⚠️ **Literature Note 結尾的 `## 🔗 KB 相關（AI 撈，FTS5）` 區段常常整片是「（無 KB 命中）」
> —— 那是死區雜訊，compose 時完全跳過，不要當素材。**

### 2. 判類別 + 載入風格側寫（聲音聖經）

```bash
python -c "from shared.style_profile_loader import detect_category; print(detect_category('<書名>', '<前 2000 字素材>'))"
```

- 回傳類別（book-review / science / people）→ 用它。
- 回 None（tie / 無命中）→ 用 source 的 `source_kind` 推：book → `book-review`；不確定就問。

**然後務必完整讀 `agents/brook/style-profiles/<category>.md`** —— 這是該類別的聲音聖經
（聲音指紋、§3a 結構決策、開場分型、語氣、§8 NEVER-DO、§10 硬數字 checklist、few-shot）。
**compose 前一定先讀，照著它寫。** 字數/emoji 邊界在 `config/style-profiles/<category>.yaml`。

### 3.（選配）掃 KB Wiki 撈補充的肉

當主骨需要連結到其他概念時，掃 KB 撈相關 Concepts / Entities / 其他 Sources 當補充材料：

```bash
grep -rl "<關鍵概念>" "$VAULT/KB/Wiki/Concepts" "$VAULT/KB/Wiki/Entities"
```

撈到的東西是「織進去的連結組織」，不是主體。**只能正向引用（KB → 草稿）**，且這一步是錦上添花，
素材不足時跳過即可，不要硬塞。

### 4. compose 草稿

照 `<category>.md` 的聲音聖經寫。重點紀律（違反任一 = 不像使用者）：

- **每個書中觀點 → 接使用者的真實 `note::`**（他的解讀／反駁／親身經歷／在地化）。
- **個人錨點只能取自使用者真實的 `note::` 行，或側寫 §10 列出的已存檔人設**
  （該檔列的科技業經歷、創作者身分等）。**嚴禁虛構任何個人經歷、數字、事件。**
  寧可少一個錨點，也不要編一個。
- **結構照側寫 §3a**：list-driven 的書（有現成編號）→ 沿用編號，但 H2 **絕不超過 10**，
  必要時重建成 4–8 根支柱；概念散布式 → 自建 3–5 根「最打中使用者」的支柱。
- 開場用側寫的開場分型（blockquote 前言 + 懸念鉤），**不要** AI default 大哉問。
- 收尾要有行動出口（執行 / 訂電子報 / 購書連結），**不要**罐頭式「希望對你有幫助」。

### 5. 寫到 Output（非 KB）

草稿是**輸出層**，落在 agent 產出區，**絕不寫進 KB**：

```
$VAULT/AgentOutputs/brook/drafts/<slug>-draft-<YYYY-MM-DD>.md
```

frontmatter 標：

```yaml
---
title: "<草稿標題>"
source: "[[KB/Literature/<slug>]]"
style_profile: <category>@<version>
draft_status: draft
generated_by: draft-article
---
```

（`AgentOutputs/brook/drafts/` 不存在就建。日期用 `date +%F`。）

### 6. 跑 §10 self-lint —— 硬傷重寫再交

```bash
python .claude/skills/draft-article/scripts/lint_draft.py \
  "$VAULT/AgentOutputs/brook/drafts/<檔名>" --category <category> --repo-root .
```

退出碼 1（有 FAIL）= 不可交稿。常見硬傷與修法：

- **正文 emoji FAIL** → 把正文段落的 emoji 拿掉（只有購書/電子報 CTA 段可留）。
- **台味助詞 <8 FAIL** → 文章太「乾淨/正式」，在反問、收束、對話處補「欸/吼/啦/喔/耶」。
- **字數越界 / H2 >10** → 依側寫重整。

修完**重跑 lint**，直到沒有 FAIL。WARN 項（如招牌短收束句）是聲音微調，交給使用者判斷，不強制。

### 7. 交付

把草稿路徑 + 完整 §10 lint 表 + 一句話自評（哪些 §8 紀律做到了、還有什麼弱點）交給使用者。
**這是初稿**，明說「拿去做大改」。

---

## 硬邊界（絕不踰越）

- **Provenance 單向**：只能 KB → 草稿。**絕不**回寫 `KB/`（Literature / Raw / Annotations / Wiki）。
  草稿不是知識，不回流知識庫（防 citation laundering）。
- **不碰** `Journals/`。
- **不重抽風格**：只「載入」已存檔的側寫，不重新分析使用者文章（那是另一件事）。
- **不定稿、不發布**：只出 draft。

---

## Next Step

草稿交付後，視情況提示（用 AskUserQuestion，opt-in，別自動跑）：

```
草稿已落在 AgentOutputs/brook/drafts/。接下來想做什麼？
A) 我自己拿去大改（這本來就是初稿）
B)（未來）draft-post — 從這篇切出社群貼文
C) 不用了，這樣就夠
```
