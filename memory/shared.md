# Shared 跨 Agent 記憶

記錄所有 agent 都應該知道的事情。

## 關於使用者

- 使用者：Shosho，健康與長壽內容創作者，同時經營自由艦隊訂閱社群
- 主要語言：繁體中文（回應與輸出一律用繁中）
- 主要關注領域：longevity、biohacking、身心健康、healthspan
- 社群平台：Fluent Community（自由艦隊）、WordPress、YouTube

## 工作流程要點

- Robin 的 ingest 必須等使用者在 Web UI 審核通過後才執行（不走 cron）
- Vault 透過 Syncthing 在 VPS 與 Local 之間同步
- 所有 Wiki 頁面用繁體中文，frontmatter key 用英文
