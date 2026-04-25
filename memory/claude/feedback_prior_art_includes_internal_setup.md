---
name: Prior-art audit 必須含既有 runbooks / env keys / GCP setup
description: 新 ADR 設計前 grep repo 內既有 setup runbooks 與 env keys，避免重複建 service account / GCP project / API account
type: feedback
originSessionId: 4dd2cba3-3c38-4eac-8234-be92b02209a3
---
新 ADR / 新 skill 設計開工前，**prior-art audit 不只看外部工具與 prior research**，還必須掃 repo 內：

1. `docs/runbooks/` 下所有 setup-*.md / *-credentials.md — 看有沒有同類 service 的既有 setup 流程
2. `.env.example` 既有 env key 清單 — 看 `GCP_*` / `GOOGLE_*` / `XXX_API_KEY` 是否已有可重用 convention
3. `grep` 既有 service account / project name 的 reference（e.g. `nakama-franky` / `nakama-monitoring`）
4. 既有 `shared/*_client.py` modules — 看是否已有同 API 的 wrapper 可擴充

**Why**：2026-04-25 ADR-009 Slice A 踩到此坑 — 我寫 `shared/gsc_client.py` + `docs/runbooks/gsc-oauth-setup.md` 時沒查到 ADR-007 Franky 早已有 `nakama-franky@nakama-monitoring.iam.gserviceaccount.com` GSC sa 設好（含 sc-domain:shosho.tw + sc-domain:fleet.shosho.tw 雙 property 授權），結果讓修修在 Mac 多建一個 `nakama-seo` GCP project + 重新跑一遍 GSC add user 流程。修修當場糾正：「拒絕重工 + 拒絕把東西變胖變複雜」是最高指導原則。

**How to apply**：
- ADR drafting / new skill design 階段加一條 explicit checkpoint：「grep repo 既有 setup runbooks 與 env keys 找 overlap」
- Prior-art audit doc 要含「Internal prior art」段落，列出所有 reuse / extend 的既有 component
- Service account / GCP project / API account 類資源：**默認 reuse 既有，新建是 exception 要明文論證**
- env key 命名：reuse 既有 prefix（`GCP_SERVICE_ACCOUNT_JSON` 已存在 → 不要新增 `GSC_SERVICE_ACCOUNT_JSON_PATH`）
- 開 PR 前 final check：grep 同 PR 引入的 env key / file path / 模組名是否已存在重複版本
