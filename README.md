# Nakama — AI Agent 團隊

為內容創作者（YouTuber + Podcaster）設計的 AI Agent 系統，專注於**身心健康（Health & Wellness）與長壽科學（Longevity）**。

部署於 VPS，定時自動執行，產出 Markdown 筆記並同步至 Obsidian LifeOS vault。

靈感來自《海賊王》——每位 Agent 都是 Owner 的夥伴。

---

## Agents

| 代號 | 角色 | 功能 | 排程 | 狀態 |
|------|------|------|------|------|
| [Robin](agents/robin/) | 考古學家 | Knowledge Base：攝入來源、產出摘要、維護 Wiki | 手動（Web UI） | ✅ 完成 |
| [Nami](agents/nami/) | 航海士 | Secretary：整合當日產出、產出 Morning Brief、頻道數據追蹤 | 每天 07:00 | 🚧 待開發 |
| [Zoro](agents/zoro/) | 劍士 | Scout：追蹤 Twitter KOL、PubMed 論文、Google Trends、GitHub | 每天 06:00 | 🚧 待開發 |
| [Usopp](agents/usopp/) | 狙擊手 | Publisher：發布至 WordPress/YouTube/社群媒體；電子報管理（Fluent CRM） | Owner 核准後觸發 | 🚧 待開發 |
| [Sanji](agents/sanji/) | 廚師 | Community Manager：Fluent Community 社群營運、成員互動、活動策劃 | 每小時 | 🚧 待開發 |
| [Franky](agents/franky/) | 船匠 | System Maintenance：套件更新、CVE 掃描、API key 驗證、健康檢查 | 每週一 | 🚧 待開發 |
| [Brook](agents/brook/) | 音樂家 | Composer：將文章/影片腳本重組為各平台格式（Blog/YouTube/IG/Newsletter） | 手動觸發 | 🚧 待開發 |

---

## 架構

```
nakama/                         ← 本 repo（部署於 VPS）
  agents/
    base.py                     ← 所有 agent 的抽象基底類別
    robin/                      ← Knowledge Base agent
    nami/                       ← Secretary agent
    zoro/                       ← Scout agent
    usopp/                      ← Publisher agent
    sanji/                      ← Community Manager agent
    franky/                     ← System Maintenance agent
    brook/                      ← Composer agent
  shared/                       ← 共用模組（API client、DB、記憶、事件、Obsidian writer）
  prompts/                      ← 集中式 prompt 管理（shared partials + agent-specific）
  memory/                       ← 跨 session 記憶（shared.md + 各 agent.md）
  config/                       ← 衍生組態
  docs/
    decisions/                  ← Architecture Decision Records (ADRs)
  tests/                        ← 測試

/home/nakama/LifeOS/            ← Obsidian vault（Syncthing 雙向同步）
  Inbox/kb/                     ← Robin 的收件匣
  AgentBriefs/                  ← Nami 的每日報告
  KB/                           ← 知識庫（Robin 的主要工作區）

/home/agents/state.db           ← SQLite 狀態追蹤
```

詳細架構說明見 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 先決條件

- Python 3.11+
- 一個 Obsidian vault（路徑設定於 `config.yaml`）
- Anthropic API key

---

## 快速開始（本機開發）

```bash
# 安裝依賴
pip install -r requirements.txt

# 設定環境變數
cp .env.example .env  # 填入 API keys

# 手動執行單一 agent
python -m agents.robin
```

---

## 部署（VPS）

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 設定環境變數
cp .env.example .env  # 填入所有 API keys

# 3. 設定 config.yaml（vault 路徑、db 路徑）

# 4. 啟動 Robin Web UI（systemd）
sudo cp nakama-web.service /etc/systemd/system/
sudo systemctl enable nakama-web
sudo systemctl start nakama-web

# 5. 設定排程（crontab）
crontab cron.conf
```

Robin Web UI 預設監聽 `http://localhost:8000`，需設定 reverse proxy（Nginx/Caddy）對外開放。

---

## 設定

| 檔案 | 用途 |
|------|------|
| `config.yaml` | 統一組態（vault 路徑、db 路徑、agent 設定、排程） |
| `.env` | 敏感資訊（API keys），不進 git |
| `cron.conf` | crontab 參考設定 |

所需的環境變數見 `.env.example`。

---

## 測試

```bash
python -m pytest tests/
```

---

## 文件

| 文件 | 說明 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系統架構、核心模組、設計決策 |
| [CHANGELOG.md](CHANGELOG.md) | 版本歷史 |
| [docs/decisions/](docs/decisions/) | Architecture Decision Records（ADRs） |
| [agents/robin/README.md](agents/robin/README.md) | Robin 詳細說明 |

---

## 版本

目前版本：**v0.4.0**。詳見 [CHANGELOG.md](CHANGELOG.md)。
