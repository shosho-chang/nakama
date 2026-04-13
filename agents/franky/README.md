# Franky — 船匠（System Maintenance Agent）

維持整個 Nakama 系統環境的健康，監測套件更新、安全漏洞與系統狀態，定期向 Owner 回報。

**排程：** 每週一次（週一）  
**狀態：** 🚧 待開發

---

## 功能

### 1. 套件更新監測
- 掃描 `requirements.txt` 中所有套件的最新版本
- 比對目前安裝版本，列出可更新項目
- 標記有 breaking changes 的重大更新（需人工判斷）

### 2. GitHub 專案監控
- 讀取 `config/franky-watch-*.yaml` 中定義的 watch list
- 查詢各 repo 的 issue 狀態、新 release、open PR
- 彙整報告交給 Nami 放入 Morning Brief（或條件滿足時發 email）

### 3. 系統健康檢查
- 確認 cron jobs 正常執行（查詢 SQLite state.db）
- 確認 Syncthing 同步狀態
- 磁碟空間、VPS 記憶體使用情況
- 確認 API key 未過期（Claude、WordPress、YouTube 等）

### 4. 安全掃描
- 檢查已知有安全漏洞的套件版本（比對 CVE 資料庫或 PyPI advisory）
- 發現高危漏洞立即發 email 通知

## 輸出

- 每週系統健康報告 → 寫入 `AgentBriefs/` 供 Nami 彙整
- 緊急安全問題 → 直接發 email

## 執行

```bash
python -m agents.franky
```
