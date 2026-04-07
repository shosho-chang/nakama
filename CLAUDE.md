# Nakama — AI Agent 團隊

## Mission

Nakama 是一個為內容創作者（YouTuber + Podcaster）設計的 AI Agent 系統。
專注領域：身心健康（Health & Wellness）與長壽科學（Longevity）。
部署於 VPS，定時自動執行，產出 Markdown 筆記同步至 Obsidian LifeOS。

靈感來自《海賊王》——每位 Agent 都是 Owner 的夥伴。

---

## Agents

| 代號 | 角色 | 功能 | 排程 |
|------|------|------|------|
| Robin | 考古學家 | Knowledge Base：攝入來源、產出摘要、維護 Wiki | 每天 02:00 |
| Nami | 航海士 | Secretary：整合當日產出、產出 Morning Brief | 每天 07:00 |
| Zoro | 劍士 | Scout：追蹤 KOL、趨勢、PubMed、關鍵字研究 | 每天 06:00 |
| Usopp | 狙擊手 | Community Monitor：監控 WordPress 社群狀態 | 每小時 |
| Sanji | 廚師 | Producer：選題、腦力激盪標題、產出內容大綱 | 手動觸發 |
| Franky | 船匠 | Repurpose：改寫文章、SEO、社群貼文、IG Carousel | 手動觸發 |
| Brook | 音樂家 | Publish：發布 WordPress、上傳 YouTube | Owner Approve 後觸發 |

---

## Architecture

```
nakama/                         ← 本 repo（部署於 VPS）
  shared/                       ← 共用模組
  agents/{robin,nami,zoro,...}  ← 各 agent
  config/                       ← 衍生組態（style-profile 等）

/home/Shosho LifeOS/            ← Obsidian vault（Syncthing 雙向同步）
  Inbox/kb/                     ← Robin 的收件匣
  AgentBriefs/                  ← Nami 的每日報告
  KB/                           ← 知識庫（Robin 的主要工作區）

/home/agents/state.db           ← SQLite 狀態追蹤
```

---

## Vault 寫入規則

所有 agent 寫入 Obsidian vault 時，必須遵守 LifeOS 的 CLAUDE.md 規則：

- `Journals/` — 完全禁止寫入
- `KB/Raw/` — 不可改寫原文，僅可補全 frontmatter
- `KB/Wiki/` — 主要工作區，可自由建立與更新
- `KB/index.md` — 每次新增/更新 Wiki 頁面後必須同步更新
- `KB/log.md` — Append-only，不可修改歷史紀錄
- 頁面內容用繁體中文，frontmatter key 用英文，專有名詞保留原文附英文翻譯

---

## Development

```bash
# 安裝依賴
pip install -r requirements.txt

# 設定環境變數
cp .env.example .env  # 填入 API keys

# 手動執行單一 agent
python -m agents.robin
python -m agents.nami
python -m agents.zoro

# 測試
python -m pytest tests/
```

---

## Config

- `config.yaml` — 統一組態（vault 路徑、agent 設定、API 設定）
- `.env` — 敏感資訊（API keys、密碼），不進 git
- `config/style-profile.json` — Owner 寫作風格分析結果
