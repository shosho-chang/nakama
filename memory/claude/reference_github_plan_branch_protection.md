---
name: GitHub free + private repo 不 enforce branch protection — 要 Pro $4/月 解鎖
description: GH UI 在 free + private repo 看似可設 branch protection rule 但實際不 enforce；API 直接回 403。Pro 個人 plan 解鎖；Team plan 解鎖 Rulesets
type: reference
created: 2026-04-27
originSessionId: f4633349-8e87-4dec-921d-fcd19990d805
---
GitHub branch protection 在 plan 之間的支援表（2026-04-27 實測 nakama repo）：

| Plan | Public repo | Private repo |
|---|---|---|
| Free | ✅ Classic Branch Protection 可用 | ❌ **UI 看似可設、API 拒絕** |
| **Pro** ($4/個人/月) | ✅ | ✅ Classic Branch Protection 真實 enforce |
| Team ($4/user/月，需 org) | ✅ | ✅ + Rulesets + Audit log |

**Free private 的 UI lemon**：
- Settings → Branches → Add classic branch protection rule **看似** 可設
- 設完 push 直接過、無警告
- `gh api repos/{owner}/{repo}/branches/main/protection` 真實回應 403：
  ```
  "message":"Upgrade to GitHub Pro or make this repository public to enable this feature."
  ```

**升 Pro 後完整 Classic Branch Protection 設定**（修修 nakama 範本）：
- ☑ Require a pull request before merging（Require approvals **不勾** → 0 approvals OK for solo workflow）
- ☑ Require status checks to pass before merging
  - ☑ Require branches to be up to date before merging
  - Required: `lint-and-test` + `lint-pr-title`
- ☑ Require linear history
- ☐ Allow force pushes（不勾）
- ☐ Allow deletions（不勾）
- ☑ **Do not allow bypassing the above settings**（不勾的話 admin 仍可 bypass）

**驗證真實 enforce**：
```bash
git commit --allow-empty -m "test"
git push origin main
# 期待: GH006: Protected branch update failed
# 不要看到: Bypassed rule violations
```

**Team plan 才解鎖**：Rulesets（新 UI）+ Audit log + multi-collaborator workflow + Required reviewers from team。對 solo dev 用不到。

**踩過的相關坑**：
- 升 Pro 流程不要走 GH UI 推薦的「建 organization」路徑 — 那是 Team 升級流程；Pro 走 https://github.com/settings/billing/plans
- `enforce_admins: false` 即使設了其他 rule 你 admin 也直接 bypass — 必須勾「Do not allow bypassing the above settings」
- `required_status_checks.contexts: []` 是個 silent gap — 勾了「Require status checks」但沒指定 check name = 任何狀態都 pass
- runbook 寫的 UI 字面可能跟 GH 當下版本對不上（Classic vs Rulesets）— 走 https://github.com/{owner}/{repo}/settings/branch_protection_rules/new 直達 Classic UI
