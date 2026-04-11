# Nakama — AI Agent 團隊

Health & Wellness / Longevity 內容創作者的 AI Agent 系統。部署於 VPS，產出同步至 Obsidian LifeOS。
詳細架構與 Agent 列表見 `ARCHITECTURE.md`、Agent 職責變更見 `docs/decisions/ADR-001-agent-role-assignments.md`。

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

# 記憶維護
python -m shared.memory_maintenance stats     # 查看記憶統計
python -m shared.memory_maintenance expire    # 清理過期記憶
python -m shared.memory_maintenance archive   # 歸檔舊低信心記憶
```

---

## Claude 記憶系統（跨平台）

**覆寫預設行為**：不使用平台特定的 `~/.claude/projects/…/memory/` 路徑。
所有記憶統一存放於 repo 內的 `memory/claude/`，透過 git 跨平台共用。

- 讀取記憶：從 `memory/claude/MEMORY.md` 讀取索引，再讀取對應檔案
- 寫入記憶：寫入 `memory/claude/` 下的對應 `.md` 檔，並更新 `memory/claude/MEMORY.md`
- 格式規範同原本記憶系統（frontmatter: name, description, type）
