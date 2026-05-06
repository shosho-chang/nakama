---
name: Multi-agent panel review/brainstorm 工作流 — 待用 skill-creator 凍結成 skill
description: 2026-05-06 ADR-020 audit 過程實證 multi-model panel 比單 Claude 高 signal — Codex (GPT-5) 抓到 3 件 Claude 漏看的 implementation drift；待跑完 ADR-020 後用 skill-creator 把流程歸納成 multi-agent-panel skill（觸發 trigger + 各家強項 mapping + 5 步驟）
type: project
created: 2026-05-06
---

## Trigger / use cases

修修明確要的 multi-agent panel 流程 — 5/6 ADR-020 audit 過程驗證有效，待之後用 skill-creator 凍結成 skill。

適用情境（從觀察到實證的）:

1. **Architecture brainstorm** — 各 model 各提 1 個方向，互相 critique
2. **Strategic decision audit**（如 ADR-020）— 拿不同立場切入既有 Claude 分析
3. **Code review** — 各 model 用不同 lens（correctness / security / API design / test gap / refactor opportunity）
4. **Edge-case /破壞測試 generation** — Claude conservative + GPT-5 aggressive + Gemini factual edge
5. **Spec / contract drift detection** — 多 ADR 跨文件 audit，多 model 的 fact-grounding bias 不同

## 各家 model 強項 cheat sheet（5/6 實證 + 既有經驗）

| Model | 強項 | 弱項 |
|---|---|---|
| Claude (主線 agent / Anthropic) | nuance / 長 context coherence / 寫作 voice / 多語言細膩 / refusal pattern | 過度 confident / 自己分析的 bias 不容易自抓 / 過度 cautious |
| GPT-5 / Codex (OpenAI) | code grounding / 數字驗證 / push-back posture / 法律分析務實 / spec drift detection | 中文不細膩 / verbose / 「I would」first-person 過多 / 過度保守某些 risk（如 copyright）|
| Gemini (Google) | multimodal / 數學 / multilingual / 長 context fact recall | reasoning chain 不如 Claude/GPT / 風格刻板 |
| xAI Grok（按需）| 「啦啦隊」立場 — 看到「方向其實 work 別過度悲觀」的點 | 不適合單跑 audit；triangulate 中當第三 voice 有用 |

## 5 步驟流程草稿（ADR-020 過程實證）

```
Step 1: Claude 主線寫 draft（plan / ADR / code design）
Step 2: dispatch Codex (GPT-5) audit — push-back posture，要它「不要 rubber-stamp Claude 的分析」
Step 3: dispatch Gemini audit — 從不同 lens（multimodal / fact recall / 不同切入點）
Step 4: 主線整合三方 audit → 標出 agreement / disagreement
Step 5: 修修拍板（哪些 push-back 採納 / 哪些打折扣 / 整合 v2 草稿）
```

## 5/6 ADR-020 audit 實證 — Codex 抓到 Claude 漏看的 3 件事

1. `shared/kb_writer.py:591-786` 實作其實在 — Claude 講「沒寫 body 這個 step」，更精確是「step 存在但 ADR-016 並行 pipeline bypass」
2. `concept-extract.md` 自己 stale — output `create`/`update` 兩 action vs ADR-011 規範的 4-action（沉默 contract drift #2）
3. ch5 vault 用 `![[tab.md]]` transclusion — 違反 `chapter-summary.md:174-181` 規定的 inline markdown（spec/impl drift）

這 3 件 Claude 都沒抓到，因為 Claude 自己寫的分析 + 自己讀檔，confirmation bias 強。GPT-5 的 fact-grounded + push-back posture 是天然解。

## Prompt 語言策略

dispatch 給外部 LLM (Codex/Gemini) prompt **用英文**:
- 訓練資料英文佔比高，instruction 解析精確
- file path / ADR id / code symbol 是英文，混語言干擾
- token 效率（中文 token 數常 2x 英文）

對外（跟修修）繁中 per CLAUDE.md。對內（dispatch LLM）英文。

## 跟既有 memory 的關係

- [project_multi_model_panel_methodology.md](project_multi_model_panel_methodology.md) — 既有「三家模型 triangulate（Gemini 吹哨 / Claude 仲裁 / Grok 啦啦隊）」方法論。本筆記是其**5-step workflow 具體化** + 5/6 實證更新
- [project_skills_development.md](project_skills_development.md) — Skills 開發體系；本流程之後走這個 pipeline 凍結成 skill
- [feedback_subagent_prompt_must_inline_principles.md](feedback_subagent_prompt_must_inline_principles.md) — dispatch 外部 LLM 必 inline 適用最高指導原則（品質 > 速度 > 成本 等）；本流程適用

## TODO: skill-creator 凍結成 skill

ADR-020 凍結後（流程跑完一次完整實證），用 skill-creator 把這個流程做成 `multi-agent-panel` skill:

- skill 觸發 trigger（修修說「panel review」/「找其他 model 看一下」/「要 second opinion」）
- 各 model 強項 cheat sheet（持續更新，每次 panel 跑完後 update）
- 5 步驟 workflow + prompt 範本（含「不要 rubber-stamp」push-back posture 寫法）
- 結果整合 template（agreement / disagreement / 採納度）
- 跨 model dispatch 機制（已有 codex:rescue + 待加 Gemini 的 dispatch path）

## References

- ADR-020 audit session 2026-05-06（這次實證 case study）
- Codex audit task task-motib9le-isaj8u（result: 6-section 1500+ word 報告）
- Codex 用 ChatGPT subscription auth → OpenAI Platform usage dashboard 看不到 token；ChatGPT side 有自己 quota
