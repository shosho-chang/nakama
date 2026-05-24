# Runbook：Syncthing folder type 設定 — 預防 vault conflict file

ADR-030 follow-up #696。Bridge digest viewer 之所以可能 silently 忽略 `*.sync-conflict-*.md`，根因是「兩台 device 同時對 agent 寫入的檔案做修改」。

最簡單的解法：**讓 agent 寫入的 path 在 device 端設成 receive-only**，這樣即便 desktop Obsidian 不小心改到，那筆改動會被下次同步覆蓋掉，**永遠不會產 conflict file**。

---

## 目標 folder type 配置

| Path | VPS | Mac | Windows | 理由 |
|---|---|---|---|---|
| `KB/Wiki/Digests/PubMed/` | **Send Only** | **Receive Only** | **Receive Only** | Robin cron 寫；human 不該編 |
| `KB/Wiki/Digests/AI/` | **Send Only** | **Receive Only** | **Receive Only** | Franky cron 寫；human 不該編 |
| `KB/Annotations/` | **Send Only** | **Receive Only** | **Receive Only** | Reader 寫；human 用 Bridge 操作 |
| `Projects/` | Send & Receive | Send & Receive | Send & Receive | human 主編，雙向 |
| `Daily/` | Send & Receive | Send & Receive | Send & Receive | human 主編，雙向 |
| `KB/Wiki/Concepts/` | Send & Receive | Send & Receive | Send & Receive | 偶爾雙邊都寫 |
| `KB/Wiki/Sources/` | Send & Receive | Send & Receive | Send & Receive | 偶爾雙邊都寫 |
| `KB/Wiki/Entities/` | Send & Receive | Send & Receive | Send & Receive | 偶爾雙邊都寫 |

**核心原則**：agent-only-write 的 path 設成單向；human-also-write 的 path 留雙向。

---

## Syncthing 設定步驟

Syncthing 的 folder type 是**每台 device 各自設定**（不是 cluster-wide）。所以三台都要做。

### Step 1 — 拆出 agent-only 子資料夾為獨立 folder

Syncthing folder type 是 per-folder 不是 per-path。若整個 vault 共用一個 folder，你不能對其中的 `KB/Wiki/Digests/` 單獨設 receive-only。

**做法**：把 `KB/Wiki/Digests/` 跟 `KB/Annotations/` 從 vault folder **拆出去**，各自成為獨立 Syncthing folder。

```
原本：
  Folder "Shosho LifeOS"
    ├─ Projects/
    ├─ Daily/
    ├─ KB/Wiki/...
    └─ KB/Wiki/Digests/  ← 想單獨控制這層

改成：
  Folder "Shosho LifeOS"        — Send & Receive，排除 Digests + Annotations
  Folder "LifeOS Digests"       — Send Only on VPS / Receive Only on Mac+Win
  Folder "LifeOS Annotations"   — Send Only on VPS / Receive Only on Mac+Win
```

實作：

1. 在三台 Syncthing 都加新 folder「LifeOS Digests」對應 path `<vault root>/KB/Wiki/Digests/`，folder type 依下表。
2. 在三台 Syncthing 都加新 folder「LifeOS Annotations」對應 path `<vault root>/KB/Annotations/`。
3. 對原本「Shosho LifeOS」folder 在 **Ignore Patterns** 加：
   ```
   KB/Wiki/Digests
   KB/Annotations
   ```
   讓原 folder 不再同步這兩層（避免重複同步）。

### Step 2 — 每台 device 設 folder type

**VPS（Linux, ssh nakama-vps）**：

1. SSH 進 VPS：`ssh nakama-vps`
2. 開 Syncthing Web GUI（一般在 `http://127.0.0.1:8384`，若是 remote 要設 tunnel；亦可直接編 `~/.config/syncthing/config.xml`）
3. 對「LifeOS Digests」folder → Edit → Advanced → Folder Type → **Send Only**
4. 對「LifeOS Annotations」folder → 同上

**Mac**：

1. 開 Syncthing Web GUI（一般 `http://127.0.0.1:8384`）
2. 對「LifeOS Digests」folder → Edit → Advanced → Folder Type → **Receive Only**
3. 對「LifeOS Annotations」folder → 同上

**Windows**：

1. 開 Syncthing Web GUI / SyncTrayzor
2. 對「LifeOS Digests」folder → Edit → Advanced → Folder Type → **Receive Only**
3. 對「LifeOS Annotations」folder → 同上

### Step 3 — 驗證

從 VPS 跑：

```bash
ssh nakama-vps "echo 'test write' > '/home/nakama/Shosho LifeOS/KB/Wiki/Digests/PubMed/2099-01-01.md'"
```

10 秒後在 Mac / Windows 確認 `2099-01-01.md` 出現。

反向測試 — 在 Mac Obsidian 對 `KB/Wiki/Digests/PubMed/2099-01-01.md` 加幾個字並儲存：

- Mac Syncthing 應該在 GUI 上顯示「Local Changes」warning
- 10 秒後從 VPS 同步來的版本覆蓋掉 Mac 上的本地修改
- VPS 上的檔案內容不變

驗完記得刪 `2099-01-01.md`（VPS 端刪）。

---

## 行為改變需要適應

這個設定上線後：

- ❌ **不要**在 Mac/Win 的 Obsidian 直接編 digest 或 annotation 的內容
  - 即使編了，本地改動會在下次同步消失
- ✅ 想對某篇 digest 加自己的思考？
  - 開新 Concept 頁（`KB/Wiki/Concepts/...`，雙向同步），用 `[[pubmed-XXXXX]]` wikilink 回去
  - 這也是 [`feedback_kb_concept_aggregator_principle`](../../memory/claude/feedback_kb_concept_aggregator_principle.md) 的原則
- ✅ 想刪過期 digest？走 VPS 端刪（或寫 cron retention 規則）

---

## Detection 仍要做（治標）

即使 prevention 上線，下面這些 corner case 還是會產 conflict file：
- VPS 跟 Mac 同時跑 agent（罕見但理論可能）
- Syncthing 本身 bug
- Manual file ops 走 SCP / rsync / 拷貝

Bridge `/bridge/digests` landing 已有 banner（PR #710）會通報。看到就進 vault 手動 resolve（保留你要的版本，刪 conflict file）。

---

## 何時不用做這個設定

- 你只有單一 device（沒有 multi-device sync）
- 你完全不用 Syncthing
- 你接受偶爾 manual resolve conflict（這就是不做這份 runbook 的成本）

---

## Reference

- Syncthing folder type docs: https://docs.syncthing.net/users/foldertypes.html
- Syncthing conflict files: https://docs.syncthing.net/users/syncing.html#conflicting-changes
- ADR-030 D2 / D3 / Gemini panel audit
- Issue #696
