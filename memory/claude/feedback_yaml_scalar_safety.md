---
name: 寫入 YAML frontmatter 時 scalar 含使用者資料必 quote + sanitize 換行
description: f-string 模板 emit YAML 時，dynamic scalar（user input / rule_id / message）含 `:` `!` `/` 空格或換行會 break parser；既要 quote 也要 escape `"` `\\` 跟 collapse `\n` `\r`
type: feedback
tags: [yaml, frontmatter, sanitize]
originSessionId: 6b73e7a2-3392-495f-a19d-fa11b332a73c
---
寫 YAML frontmatter 模板時，動態 scalar（rule_id / title / category / 任何來自 caller 的字串）必須：

1. 用 double-quoted scalar (`field: "<value>"`)
2. Escape `"` → `\"`、`\\` → `\\\\`
3. Collapse `\n` `\r` → 空格（多行 scalar 雖然 YAML 允許，但 Obsidian / 部分 frontmatter parser 會誤讀）
4. Filename / list-item 用途的 scalar 額外走 slugify（保留 frontmatter 可讀完整值，slug 給 path/tag）

**Why:** 2026-04-26 PR #187 incident_archive self-review 抓到 3 個 footgun：
- `trigger:` raw embed unquoted `rule_id="alert/foo bar!"` → YAML parser 看 `:` 誤判 mapping、`!` 誤判 tag indicator
- `tags:` list item 用 `rule_id.split("-",1)[0]` 沒 slugify → `/` 空格漏進 list scalar
- `title:` 只 escape `"` 沒 strip `\n` → multi-line scalar 跨行
合併 helper `_yaml_safe()` 統一處理 `\\` → `"` → `\n` → `\r`。

**How to apply:**
- 寫 frontmatter 模板的任何 f-string field 含 caller-provided 字串就必 quote + sanitize
- 補測試用 `yaml.safe_load()` 真實 round-trip 驗證（rule_id/title 含 `:` `/` `!` `"` 換行混合 case）— 純子串斷言可能 string match 過但 YAML parse fail
- shared/incident_archive.py `_yaml_safe()` 是現成範例

**Out of scope（避免 over-engineering）:**
- 完整 YAML emitter library（PyYAML.dump）— 對受控模板太重，本 helper 夠用
- Unicode escape（`\\u00XX`）— frontmatter 沒這需求
