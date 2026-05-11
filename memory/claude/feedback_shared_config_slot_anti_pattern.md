---
name: 多 consumer 共用 config slot anti-pattern — 規劃時就 split 命名
description: dataclass config 多個 consumer 用同 slot（如 attachments_abs_dir 給 fetch_fulltext + image_downloader 共用）會 last-writer-wins; Slice 1 設計犯這錯被 Slice 2/4 同時撞上。命名前綴前綴 fulltext_/image_ 等 consumer-specific prefix 預防。
type: feedback
created: 2026-05-04
---

`URLDispatcherConfig` Slice 1 設計 `attachments_abs_dir: Path | None` 一個 slot 給「fetch_fulltext PDF 路徑」用，docstring 也宣稱 Slice 2 wires 它。但 Slice 4 image fetch 又用了同一 slot 存「image attachments per-slug 路徑」。Production caller 只能設一個值 → last-writer-wins → 一個 consumer 拿錯路徑。

Slice 2 PR #363 修方向：split slot to consumer-specific prefix
- `fulltext_attachments_abs_dir` + `fulltext_vault_relative_prefix`（Slice 2 fetch_fulltext use）
- `image_attachments_abs_dir` + `image_vault_relative_prefix`（Slice 4 image_downloader use）

**Why**：dataclass field 的「path」「dir」字尾常 generic，多 consumer 來時不易看出 collision risk。Slice 1 docstring 寫「Slice 2 use」+ Slice 4 implementer 沒 cross-reference docstring 就 reuse → 兩 weeks 後同 path 衝突。

**How to apply**（dataclass / TypedDict / Pydantic config 設計）：

1. **每個 consumer 自帶 prefix**：`fetch_X_path`、`image_X_path`、不寫 generic `attachments_dir`
2. **多個 consumer fn slots 同時存在時**（`fetch_fulltext_fn` + `image_downloader_fn`），路徑 slot **必須**對應前綴
3. **設計 review checklist**：「這個 dataclass 預期幾個 consumer？每個 consumer 是否有自己的 path slot 或 closure-bound？」
4. **避免共用 slot 的 trade-off**：closure-bound 可以避免 slot 但讓 dispatcher contract 不一致；prefix 命名是首選

**Cost of fix**: 多檔 rename + tests 更新（Slice 2 PR #363 改 2 src + 3 test files ~30 references），可避免下次 slice extension 重踩。

**Trigger**：dataclass 多個 `_fn` field 共享 path/dir 字尾的 generic slot 時，提醒 split。
