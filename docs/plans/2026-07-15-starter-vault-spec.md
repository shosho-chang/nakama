# G3 Starter Vault 規格 — 預裝 Templater 劃線/註記快捷鍵

> 分享會發給學員的「伴手禮」vault。目標：學員**下載 → 用 Obsidian 開 → 點一次
> 「Trust & enable」→ 立刻能用劃線/註記快捷鍵**，零手動設定（那一下信任是 Obsidian
> 的安全閘，檔案繞不過，唯一無法省的動作）。
>
> 建置法採「設定一次 → 整包快照」，**不手刻 `.obsidian` JSON**（格式易錯）。

---

## 1. Starter Vault 內容兩層

| 層 | 內容 | 誰產 |
|---|---|---|
| **vault 內容層** | KB 結構（Fleeting/Literature/Annotations/Permanent/MOCs/Raw/Wiki/index/log）、`CLAUDE.md`（權限＋五紅線）、`librarian.config.md`、5–8 張 seed 卡、2 張範例永久卡、**`Templates/highlight.md`＋`Templates/annotate.md`** | `librarian-setup` skill（純檔案，skill 寫得出來） |
| **`.obsidian/` 設定層** | Templater 本體 + 設定 + 兩個快捷鍵綁定 | **預先手動裝好 → 快照**（skill 在 live vault 裝不了 plugin 本體） |

---

## 2. 兩個 Templater 範本（英文檔名）

### `Templates/highlight.md`
選取文字後按快捷鍵，把選取換成 `==...==`：
```
==<% tp.file.selection() %>==
```

### `Templates/annotate.md`
按快捷鍵跳出輸入框，把輸入插成 `note::`（下一行）：
```
<% "\n" %>note:: <% tp.system.prompt("Note") %>
```
- `<% "\n" %>` 強制換行，讓 `note::` 落在劃線的**下一行**（對齊 ingest parser「劃線下一行的 note::」）。
- 想要多行輸入框 → prompt 改：`tp.system.prompt("Note", "", false, true)`。
- prompt 文字（"Note"）是純 UI 字串，要中文改 `"你的想法"` 即可，不影響功能。

> ⚠️ 建置時務必**實測**：劃線後按註記鍵，確認 `note::` 真的落在新的一行；若沒有，
> 微調 highlight 範本結尾或 annotate 的換行，測到對為止（這正是「設定一次→快照」要現場驗的點）。

---

## 3. 快捷鍵綁定

| 動作 | 建議鍵 | 綁在 |
|---|---|---|
| 劃線（highlight.md） | `Ctrl+Shift+H` | Templater 範本 hotkey |
| 註記（annotate.md） | `Ctrl+Shift+A` | Templater 範本 hotkey |

鍵位可改，但建置時**確認沒跟既有指令衝突**（Obsidian 設定→快捷鍵搜該組合看是否已占用）。

---

## 4. 建置步驟（設定一次 → 快照）

1. 開一個**乾淨** vault。
2. 先讓 `librarian-setup` 建好內容層（KB 結構、CLAUDE.md、seed 卡、`Templates/highlight.md`＋`annotate.md`）。
3. Community plugins → 裝並啟用 **Templater** → 設定 → **Template folder location** 設 `Templates`。
4. Templater 設定 → **Template Hotkeys** → 加入 `highlight.md`、`annotate.md` 兩個範本。
5. Obsidian 設定 → **快捷鍵** → 搜範本名 → 綁 `Ctrl+Shift+H` / `Ctrl+Shift+A`。
6. **實測**：選字→劃線鍵→變 `==...==`；游標在該行→註記鍵→跳框→輸入→下一行出 `note:: ...`。兩個都對才算數。
7. 關閉 Obsidian（讓設定寫盤穩定），把**整個 vault 資料夾（含 `.obsidian/`）打包**成 Starter Vault 模板。

---

## 5. 學員端體驗（驗收此為準）

1. 下載 Starter Vault，解壓。
2. Obsidian →「Open folder as vault」選它。
3. 跳「Trust author and enable plugins?」→ **點一次 Enable**。（唯一手動步驟，安全閘無法省。）
4. 立刻：選字 `Ctrl+Shift+H` 劃線；`Ctrl+Shift+A` 跳框輸入註記。KB 結構、seed 卡也都在。

---

## 6. 注意事項

- **Restricted Mode 那一下無法自動化** —— 是 Obsidian 對社群 plugin 的安全閘。步驟卡（G5）要明確畫出這一步。
- **Templater 是 MIT 授權**，隨 Starter Vault 一起散布 plugin 檔沒問題。
- **快照前清掉個人痕跡**：`.obsidian/workspace.json`（記錄你打開過哪些檔）等狀態檔可刪，避免把建置者的 UI 狀態帶給學員。
- **檔名一律英文**（`highlight.md`/`annotate.md`）—— Templater 的 hotkey 指令 id 會含檔名路徑，英文避免跨平台/JSON 編碼風險。
- Starter Vault 若要進 git 管理，`.obsidian/workspace*.json`、`.obsidian/cache` 這類狀態檔建議 gitignore，只版控結構性設定（community-plugins.json / hotkeys.json / plugins/*/data.json / plugins/*/main.js）。

---

## 7. 驗收清單

- [ ] 乾淨機器解壓 Starter Vault → Obsidian 開 → 一鍵 Enable → 兩個快捷鍵立刻可用。
- [ ] `Ctrl+Shift+H`：選字變 `==選字==`。
- [ ] `Ctrl+Shift+A`：跳輸入框 → 輸入 → **下一行**出現 `note:: 輸入`。
- [ ] KB 結構、CLAUDE.md、seed 卡、範例永久卡都在。
- [ ] 產出的 `==...==` + `note::` 能被 `/ingest` 正確解析（跑一遍 article-web profile 驗證）。
