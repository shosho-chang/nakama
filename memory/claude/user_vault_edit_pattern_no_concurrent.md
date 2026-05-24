---
name: user-vault-edit-pattern-no-concurrent
description: 修修不會在多裝置 Obsidian 同時編同份 vault 檔案；conflict file 防護是 over-engineering，detection banner 足夠
metadata:
  type: user
---

修修的 vault 編輯模式（2026-05-24 ADR-030 #696 收尾時 explicit 確認）：

- **Digest** (`KB/Wiki/Digests/{PubMed,AI}/`)：從 Bridge 看，不在 Obsidian 編。Agent (Robin/Franky) cron 寫，他不碰
- **Annotation** (`KB/Annotations/`)：Reader (agent) 寫，他不雙裝置同步編
- **Project / Daily / KB/Wiki/Concepts/Sources**：他會編，但一次只在一台裝置開 Obsidian 編

**結論**：真要撞 Syncthing conflict file 需要「兩裝置同時對同檔做不同修改」，對他的模式幾乎不可能發生。

**設計含義**：

- Syncthing send-only / receive-only 拆 folder 的 prevention 方案（ADR-030 #696 原設計）對他**過度工程化**。
- PR #705 的 detection banner（`/bridge/digests` 看到 `*.sync-conflict-*.md` 跳紅警告）已足夠 — 屬 cheap insurance，零維護成本
- 未來討論 vault concurrency / multi-writer 場景時，先 anchor 回他的實際模式（單裝置編輯 + agent-only-write paths），不要假設 worst-case multi-device race

**Related**：

- [[user_vault_access_pattern]] — 他主要 Obsidian 看 Daily + Projects，KB/Agent 走 Web UI
- Issue #696 / PR #705 — banner shipped；runbook `docs/runbooks/syncthing-folder-types.md` 保留但他選不執行
