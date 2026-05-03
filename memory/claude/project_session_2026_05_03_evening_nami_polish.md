---
name: 收工 — 2026-05-03 晚 Nami persona polish + ask_zoro delegation MVP
description: PR #329 squash merged f0e64dd（Nami Taiwan voice anchor + capability boundaries + epistemic labels + ask_zoro tool）+ PR #330 cron cleanup dead nami stub；R2 + xCloud fleet backup hygiene closure；Inter-agent delegation Option A pattern 凍結
type: project
created: 2026-05-03
---

修修 5/3 晚接續下午 PR #325 收工 + 桌機 textbook ingest 兩 session（PR #328）後，第三段 session 把零散 backlog 全清 + 一條真 bug 修 + 一條架構 pattern 凍結。

## 1. 真 bug 修 — Slack DM 多輪測試揪到 2 點

修修 Slack DM Nami 5 輪對話測試（從打招呼 → 行程 → 熱議 → 找研究 → 連結要求），thread 續問機制 ✅ 沒問題，但揪到兩個品質 bug：

### Bug 1：簡體中文 leak（「柳葉刀」）

LLM training data bias，台灣繁體中文應是「刺胳針」。Nami 自己第二輪 self-correct 用「刺胳針子刊」了，但第一輪 leak 是 trained behavior，每次都會犯。

**修修反問**：「列表整個列出來」是不是好辦法？大陸用語/中國用語/臺灣用語怎麼列都很難窮舉。**正確** — 解法是 positive identity anchor + 反向 sentinel keyword + fallback rule，不是窮舉表。詳見 [feedback_identity_anchor_over_enumeration.md](feedback_identity_anchor_over_enumeration.md)。

### Bug 2：Hallucinated「社群熱議」

第一輪 Nami 說「這個數字在社群上傳很快」「最近很多媒體在推」「最近被綁在...一起討論」。但 Nami 沒有 Reddit/Threads/X/PTT 任何 social listening tool，他只能 web_search 拿到新聞報導。修修追問「給連結」才坦承「我之前說『社群上傳很快』是基於搜尋結果的媒體報導量做的推斷，說法不夠精確」。

**根因**：system prompt L423「禁忌」段沒 guard「不要假裝有沒有的能力」+ 沒 epistemic discipline（事實/推測/不知道分類）。

## 2. PR #329 — Nami persona 三段改動 + ask_zoro tool

| Commit | What |
|---|---|
| `4ec7362` | chore(memory): R2 + xCloud fleet backup hygiene closure |
| `b9fd20b` | feat(nami): Taiwan voice anchor + capability boundaries + epistemic labels |
| `49baadd` | feat(nami): ask_zoro tool — inter-agent delegation MVP (Option A) |

Squash merged 為 `f0e64dd`，VPS deploy 完成（修修 ssh restart `nakama-gateway`）。

**Test results**：74/74 gateway handlers test green（7 new ask_zoro tests + 67 既有）/ ruff clean。

### Prompt 改動（解 Bug 1+2）

1. **語言規範**段：positive identity anchor「科學人/Hello醫師/商周台灣專業作家」+ 10 個高頻 leak 反向 sentinel + 不確定譯名英文並列 fallback
2. **能力邊界（誠實原則）**段：明列能做的 / 透過 ask_zoro 委託的 / 真不能的，加 SOP
3. **事實/推測/不知道（必須標籤化）**段：三類強制標籤，**[推測]** 必標，不知道直說

### ask_zoro tool（解 inter-agent delegation 缺口）

修修問：「搜尋社群熱議是不是 Zoro 的工作？Nami 接到不屬於範圍的 request，是不是可以 pass 給 Zoro，再回傳結果給我？」

**100% 對** — `prompts/zoro/persona.md:23` 寫「情報來源：Google Trends、Reddit、PubMed、YouTube 健康類頻道、X/Twitter KOL」+ `agents/zoro/` 有 5 個 API client。Zoro 自己 persona L8 寫「被問偏的題會直接說『這不是我管的』」，**單向 reject 已有，雙向 forward 還沒做**。

實作 Option A（tool-based delegation, same-process import）：
- `gateway/handlers/nami.py` 加 `ask_zoro` tool definition + dispatch + `_tool_ask_zoro` method
- 3 個 capability：`trend_check` / `social_listening` / `keyword_research`
- Sync import `agents.zoro.*`，結果 LLM loop 內 paraphrase 給修修
- Nami 用自己 persona 重述，Zoro 結構化原文不外露（修修看不到 Zoro 是誰）

詳細 pattern 凍結 [reference_inter_agent_delegation_option_a.md](reference_inter_agent_delegation_option_a.md)。

## 3. PR #330 — cron / docs cleanup（dead Nami stub）

audit cron entries 時 grep 發現 `cron.conf:20` 寫 `python -m agents.nami` 但 `agents/nami/__main__.py` 只 raise NotImplementedError（Morning Brief 從未實作）。三處引用要清：
- `cron.conf`: 該行 commented-out + 留 unblock 條件
- `CLAUDE.md`: 拿掉 `python -m agents.nami` example
- `agents/nami/__main__.py`: docstring 講清 stub status

零功能改變，純 docs/config hygiene。CI 第一次 fail 因為忘跑 ruff format（一行 line length），rebase + amend + force-push 修。

## 4. 零散 backlog 清完 — 5/3 三項 hygiene

| 項 | 結果 |
|---|---|
| R2 bucket-scoped token | CF dashboard 4 token 全留（攻擊面同 / 純命名 hygiene / 修修煩躁不想砍）— 詳見 [project_nakama_backup_deployed.md](project_nakama_backup_deployed.md) §CF Token list 現況 |
| xCloud fleet 整站 tarball | 修修開啟 Files backup，daily + 30 天 retention |
| Slack thread 多輪測試 | ✅ thread 續問正常 + 揪 2 bug + PR #329 修 |

順手 flag 一條 follow-up：**Franky verify per-prefix gap**（`agents/franky/r2_backup_verify.py:42` `FRANKY_R2_PREFIX=` 留空 = 看整 bucket 最新物件，shosho fresh 物件會掩蓋 fleet stale）。要 prefix 拆分驗證，但今天不做（schema + DB migration）。

## 5. 教訓凍結

兩條新 memory file：

- [feedback_identity_anchor_over_enumeration.md](feedback_identity_anchor_over_enumeration.md) — voice drift / 風格 leak 用 positive identity anchor 而非窮舉表（修修明確 push back 列表方法）
- [reference_inter_agent_delegation_option_a.md](reference_inter_agent_delegation_option_a.md) — same-process tool-based delegation pattern（未來 Brook/Robin 同套模式）

epistemic discipline（事實/推測/不知道三類標籤）寫進 Nami prompt 不開單獨 feedback file，因為這條規則跟 Nami persona 強耦合（其他 agent 例如 Zoro 已有「不確定就寫『待查』」近似規則）。

## Reference

- PR #329 squash `f0e64dd`：feat(nami): Taiwan voice + honesty + ask_zoro inter-agent delegation
- PR #330 等 CI 中：chore: cleanup dead Nami stub
- Prompt before/after：詳見 PR #329 diff
- Inter-agent delegation 設計選項討論：本 session message log（Option A vs B vs C 對比表）
