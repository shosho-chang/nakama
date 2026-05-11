---
name: 收工 — 2026-05-04 凌晨 5 PR ship + Usopp #270 closure
description: 5 條工程小修序列 dispatch + 全 squash merged (PR #331 memory + #332 Nami round 3 + #333 Franky per-prefix + #334 A6 mock spec + #335 keyword-research zh-channels)；Usopp #270 closed (acceptance #2/#3 改下次 publish 觀察、SEO 中控台 5 天 0 publish 真議題不開 issue)；GH branch protection strict mode 序列 merge cycle 教訓
type: project
created: 2026-05-04
---

修修 5/3 晚問代辦 → 篩選出主線 #3 (Nami round 3) + 4 條工程小修 → 修修指示「全部先做一做」→ 序列 dispatch 全 5 PR + 全 squash merged。同 session 順手 close Usopp #270 + 揭開 SEO 中控台 5 天 0 publish 真議題。

## 1. Usopp #270 closure（前段）

修修原本以為 PR #320 sandcastle 修法 ship 後三天觀察 0 traceback = 收工。我 ssh VPS 實查發現：

- daemon active idle 10 小時 0 traceback ✅
- **但 `publish_jobs` 表 0 row ever** — Usopp 從沒在 production 真 publish 過
- `approval_queue` 全表 2 row（4/27 + 4/29 brook），都 pending 從未進 approved

換言之 acceptance #3 「3 天 0 traceback」是 **vacuous green** — daemon 沒事做沒撞鎖自然 0 traceback；acceptance #2 「cron 時段 e2e publish」永遠驗不到（要 approval pipeline 上游有 approved row + 撞 cron 時段的天時地利，5 天從沒發生）。

Issue closed，acceptance #2/#3 改「下次真實 publish 時順便觀察」。**真議題不開 follow-up**：SEO 中控台從 4/29 ship 後 0 publish ever 是 known state（修修尚未排時間跑 SEO workflow），不是 bug。

## 2. 5 PR 序列 dispatch + 全 squash merged

| PR | 內容 | LOC | Squash | 收 backlog |
|---|---|---|---|---|
| [#331](https://github.com/shosho-chang/nakama/pull/331) | memory hygiene（CF token nag-suppress + sandbox prod-read guard 補充） | 49 | `39abe4f` | session start cleanup |
| [#332](https://github.com/shosho-chang/nakama/pull/332) | Nami persona round 3 — ask_zoro escape hatch + social heat + 數字 source | 102 | `1996335` | 主線 #3 |
| [#333](https://github.com/shosho-chang/nakama/pull/333) | Franky verify per-prefix isolation + migration 008 | 432 | `496a7ea` | xcloud fleet stale silent gap |
| [#334](https://github.com/shosho-chang/nakama/pull/334) | A6 follow-up — spec=Anthropic on llm_review mocks | 53 | `d4a3914` | PR #192 deferred |
| [#335](https://github.com/shosho-chang/nakama/pull/335) | keyword-research zh-channel biasing | 277 | `7782dca` | GH #33 Item 4+5 |

**合理 deferred 維持 deferred**：
- **B3** firecrawl mock spec — firecrawl 4.23 `.scrape()` 是 instance dynamic attr，`MagicMock(spec=FirecrawlApp)` 會 block 真 method（實測 `hasattr(MagicMock(spec=Firecrawl), 'scrape')` 回 False）。等 SDK 把 scrape 掛回 class 或加 per-method spec list 再做
- **B4** firecrawl Location country wiring — firecrawl 4.23 沒 export Location class，需 SDK 升級
- **Issue #145** Bridge edit/requeue audit gap — 需「真有 forensics 需求」才有意義；SEO 中控台 5 天 0 publish 表示沒人會找 audit log

## 3. Nami Bug 2 變形 round 3 修法

PR #329 round 2（Taiwan voice + epistemic labels + ask_zoro tool）後修修 Slack DM 多輪測試又抓到 Bug 2 變形重來。具體 hallucination：

> 「Zoro 今天偵察線掛掉，我直接從 Reddit 和台灣媒體抓到一些近況」
> 「Reddit r/Biohackers 現在燒的」「獲得大量討論」「討論熱度穩定」

PR #329 三段修法只 cover **個別維度**，沒擋三個 escape hatch：

1. **ask_zoro 失敗 / 不調用時的禁演**（新 sub-section in 「能力邊界」）
   - 點名 Zoro 是 same-process direct import，**不會「掛」**
   - 列具體禁忌句型（「偵察線掛掉所以我自己抓」）
   - 三類 fallback 正確處理：error / 不調用 / 都做不到
2. **Social heat 描述禁忌詞**（從 inline 警告升獨立列表，>12 詞）
   - 燒的 / 廣傳 / 引發熱議 / 聲量 / 討論熱度穩定 / ...
   - 媒體報導密度 ≠ 社群熱度
3. **數字 / 統計數據特別規則**（事實/推測/不知道之外加段）
   - 三條退路：附 source / 標 [推測] / 降級寫法
   - 點名「vague 引用 + 具體數字」是編造數字典型 pattern

教訓：identity anchor + 反向 sentinel 無法窮舉所有 escape hatch；需要對**每個觀察到的 hallucination pattern** 添加新規則迭代修。

## 4. GH branch protection strict mode 序列 merge cycle 痛點

修修升 Pro + 設 branch protection：
- `required_status_checks: ["lint-and-test", "lint-pr-title"]`
- `strict: true`（base 必須 up-to-date）
- `enforce_admins: true`（連 admin 也擋）
- `required_linear_history: true`（squash/rebase only）
- **`Auto merge: disabled`**（repo level setting，`gh pr merge --auto` 不能用）

序列 merge 痛點：
- 一條 merge 後 main advance → 其他 PR 變 outdated
- 每條都要 `gh pr update-branch <num>` → wait CI re-run → squash merge
- 5 PR 中 4 條打不過第一次 squash，要 cycle 兩三次
- `--admin` flag 沒用（enforce_admins=true）
- Auto-merge disabled 不能 batch 設定

詳見 [feedback_branch_protection_strict_serial_merge.md](feedback_branch_protection_strict_serial_merge.md)。

## 5. ssh VPS 兩道 guard 教訓

修修「授權給你看 setting 都解了」之後我 ssh 實仍被擋。實際上 deny rule 已清（PR #320 上午 commit）但有第二道 sandbox prod-read guard 跟第三道 self-modification guard：

| Guard | 擋什麼 | Yield 條件 |
|---|---|---|
| `.claude/settings.json` deny rule | conversation explicit auth | 修修砍 deny |
| Sandbox prod-read guard | 讀 production data | settings.json allow rule（Bash(ssh nakama-vps *)）— 修修手動加 |
| Self-modification guard | agent 自己 Edit settings.json 加 allow | conversation 內 explicit auth 也不行；user 必須手動 paste |

詳見 [feedback_settings_deny_rule_blocks_explicit_auth.md](feedback_settings_deny_rule_blocks_explicit_auth.md) 補充段。

## 6. 修修 deploy 待辦

| PR | Deploy 步驟 |
|---|---|
| #332 Nami round 3 | `ssh nakama-vps "cd /home/nakama && git pull && systemctl restart nakama-gateway"` |
| #333 Franky per-prefix | `ssh nakama-vps "cd /home/nakama && git pull"` + 編輯 `.env` 加 `FRANKY_R2_PREFIXES=shosho/,fleet/` |
| #335 keyword-research zh | next 跑 `/keyword-research <topic>` 自動生效，無 deploy |

驗證（下次自然會撞）：
- Slack DM Nami「最近社群討論什麼健康議題」— 看 Bug 2 變形是否真修
- /keyword-research zh topic — reddit_zh 不再撈 r/moneyfengcn / twitter_zh 不再 zh-CN-dominated
- 5/5 早上 Franky cron — 看 `FRANKY_R2_PREFIXES` 是否生效跑兩 prefix verify

## Reference

- [feedback_branch_protection_strict_serial_merge.md](feedback_branch_protection_strict_serial_merge.md)（new）
- [feedback_settings_deny_rule_blocks_explicit_auth.md](feedback_settings_deny_rule_blocks_explicit_auth.md)（updated）
- [feedback_mock_use_spec.md](feedback_mock_use_spec.md)（updated — SDK class spec vs instance dynamic attr）
- [project_session_2026_05_03_evening_nami_polish.md](project_session_2026_05_03_evening_nami_polish.md) — round 2 上一輪
