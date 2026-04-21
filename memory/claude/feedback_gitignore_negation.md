---
name: gitignore 負面規則要搭配 wildcard
description: .gitignore 父目錄寫 `data/` 無法用 `!data/file` 再 include 特定檔，要用 `data/*` + negation
type: feedback
---

# .gitignore 子檔再 include 的正確寫法

**規則**：父目錄直接寫 `data/` 會排除整個 dir，就**無法**用 `!data/scimago.csv` 把裡面某檔重新 include。

**Why**：git 規範明文 "It is not possible to re-include a file if a parent directory of that file is excluded."（踩到過，PR #66 最初版 Scimago CSV 沒 track 上）。

**How to apply**：
```
# ❌ 壞：整個 data/ 被排除，`!` 不生效
data/
!data/scimago_journals.csv

# ✅ 好：排除 data/ 內容但 dir 本身還可 traverse，`!` 才能重新 include
data/*
!data/scimago_journals.csv
```

驗證：`git check-ignore -v <path>` 看實際匹配的規則。
