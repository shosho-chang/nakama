# Vault `CLAUDE.md` 權限表 patch — Centaur N520（跨 repo，手動套用）

> **為什麼這份單獨存在**：`E:\Shosho LifeOS` 不是 git repo，Nakama 的 PR **無法**
> commit vault 端的 `CLAUDE.md`（panel Codex §3/§4 + Gemini §4 一致確認）。repo-side
> canonical 已落在 `docs/VAULT-LAYOUT.md` §3「Centaur Permanent layer — 演算法紅線」。
> 這份是 vault 端要手動貼上的對應 snippet，當作 **merge 後 checklist**，不是 DoD 的
> 一部分自動完成項。

## 套用步驟（merge 後一次性）

1. 開 `E:\Shosho LifeOS\CLAUDE.md`，找權限/紅線表區段。
2. 把下方 block 貼進權限表，與既有 `KB/` 規則並列。
3. 存檔。vault 與 repo `docs/VAULT-LAYOUT.md` 兩邊以 repo 為 canonical；未來漂移以
   repo 為準重新同步（已記一條 future-unify backlog，見 PR description）。

## 要貼進 vault `CLAUDE.md` 的 block

```markdown
## KB Permanent layer 權限（Centaur v0.2 §7 — 與 repo docs/VAULT-LAYOUT.md 同步）

| 路徑 | 權限 | 說明 |
|---|---|---|
| `KB/Permanent/` | 🔒 正文 + status human-only | 人寫永久卡。AI 唯一動作 = 記帳回填 frontmatter `source_refs`/`modified`/`aliases`，**絕不**寫正文/status/其他 key。建檔走 Web UI human surface。 |
| `KB/Fleeting/` | 🟡 人 + Nami 寫 | 即時捕捉。AI 只翻 `status: open→processed` + 善後（送回收桶，不用 rm）。 |
| `KB/Literature/` | 🤖 render | ingest 當下 render 的人讀文獻筆記快照。 |
| `KB/MOCs/` | 🟡 marker convention | 人寫分組標題 +「為什麼放這」；AI 維護 `%%agent-robin-unfiled%%` section + 孤兒標記。建 MOC 永遠人決定。 |

### 五條演算法紅線（canonical 在 repo docs/VAULT-LAYOUT.md §3）

1. AI 絕不寫 `KB/Permanent/` 正文與 status；唯一入口 `update_permanent_bookkeeping()` 白名單 key。
2. 每個事實宣稱附 citation，溯源回 `KB/Raw/` / `KB/Annotations/` 錨點。
3. Concept 可寫可 merge，但不冒充永久卡（`author` 欄必填）。
4. ingest 不建 MOC — MOC 等人的擠壓點。
5. Concept/Output 終端證據只能是 Sources/Raw/Annotations，不得以另一個 Concept/Output 作事實來源。
```
