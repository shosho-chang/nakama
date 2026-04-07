# Nakama — AI Agent 團隊

為內容創作者（YouTuber + Podcaster）設計的 AI Agent 系統，專注於**身心健康（Health & Wellness）與長壽科學（Longevity）**。

部署於 VPS，定時自動執行，產出 Markdown 筆記並同步至 Obsidian LifeOS vault。

靈感來自《海賊王》——每位 Agent 都是 Owner 的夥伴。

---

## Agents

| 代號 | 角色 | 功能 | 排程 | 狀態 |
|------|------|------|------|------|
| [Robin](agents/robin/) | 考古學家 | Knowledge Base：攝入來源、產出摘要、維護 Wiki | 每天 02:00 | ✅ 完成 |
| [Nami](agents/nami/) | 航海士 | Secretary：整合當日產出、產出 Morning Brief | 每天 07:00 | 🚧 待開發 |
| [Zoro](agents/zoro/) | 劍士 | Scout：追蹤 KOL、趨勢、PubMed、關鍵字研究 | 每天 06:00 | 🚧 待開發 |
| [Usopp](agents/usopp/) | 狙擊手 | Community Monitor：監控 WordPress 社群狀態 | 每小時 | 🚧 待開發 |
| [Sanji](agents/sanji/) | 廚師 | Producer：選題、腦力激盪標題、產出內容大綱 | 手動觸發 | 🚧 待開發 |
| [Franky](agents/franky/) | 船匠 | Repurpose：改寫文章、SEO、社群貼文、IG Carousel | 手動觸發 | 🚧 待開發 |
| [Brook](agents/brook/) | 音樂家 | Publish：發布 WordPress、上傳 YouTube | Owner 核准後觸發 | 🚧 待開發 |

---

## 架構

```
nakama/                         ← 本 repo（部署於 VPS）
  agents/
    base.py                     ← 所有 agent 的抽象基底類別
    robin/                      ← Knowledge Base agent
    nami/                       ← Secretary agent
    zoro/                       ← Scout agent
    usopp/                      ← Community Monitor agent
    sanji/                      ← Producer agent
    franky/                     ← Repurpose agent
    brook/                      ← Publish agent
  shared/                       ← 共用模組（API client、DB、Obsidian writer）
  config/                       ← 衍生組態（style-profile 等）
  tests/                        ← 測試

/home/Shosho LifeOS/            ← Obsidian vault（Syncthing 雙向同步）
  Inbox/kb/                     ← Robin 的收件匣
  AgentBriefs/                  ← Nami 的每日報告
  KB/                           ← 知識庫（Robin 的主要工作區）

/home/agents/state.db           ← SQLite 狀態追蹤
```

---

## 快速開始

```bash
# 安裝依賴
pip install -r requirements.txt

# 設定環境變數
cp .env.example .env  # 填入 API keys

# 手動執行單一 agent
python -m agents.robin
```

## 設定

- `config.yaml` — 統一組態（vault 路徑、agent 設定、排程）
- `.env` — 敏感資訊（API keys），不進 git
- `cron.conf` — crontab 參考設定

## 測試

```bash
python -m pytest tests/
```
