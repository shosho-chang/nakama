---
name: YAML 未引號的純數字會被 parse 成 int，對 str 迭代會 AttributeError
description: yaml.safe_load 對 `- 168` / `- 2024` 這種條目回 int，下游 `.lower()` / `str in` 會 AttributeError；條目若要當字串用，yaml 寫 `"168"` 或 load 後強制 coerce
type: feedback
tags: [yaml, type-coercion]
originSessionId: 23d6fe90-ddb9-4038-946e-a916801421f8
---
`yaml.safe_load` 看到未引號的純數字會回 int，不是 str：

```yaml
detect_keywords:
  - 168       # → int 168
  - "168"     # → str "168"
  - 意識      # → str
```

**Why:** PR #78 的 `config/style-profiles/science.yaml` 寫 `- 168`（斷食 168）和 `- 2024` 類的數字關鍵字，測試跑到 `kw.lower() in haystack` 噴 `AttributeError: 'int' object has no attribute 'lower'`。

**How to apply:**
- 若 yaml list 有可能混合數字和文字、下游要 str 介面（regex / .lower() / format）→ loader 必須 coerce：`tuple(str(k) for k in (data.get("foo") or []))`
- 或者 yaml 寫 `"168"` 強制 quote（較不穩定，人會忘）
- 凡是 user-editable 設定檔的 list，loader 都要有 type coerce 層，不要相信 yaml 的「自然型別推論」
- 陷阱同樣吃 bool：`- yes` / `- on` / `- off` / `- no` 在舊 yaml spec 裡是 bool（safe_load 應該不吃但值得知道）
