---
name: 中文 SRT 強制斷行要走詞邊界（jieba），不純 char-level 切
description: SRT sub-cue ≤N 字硬拆時要在詞邊界斷，不然「然後 / 怎麼 / 因為」雙字詞會被切到兩個 cue
type: feedback
created: 2026-04-30
---

**規則：中文 SRT `_force_break(text, max_chars)` 走 jieba 分詞 greedy 累加，不要在第 max_chars 字硬切。**

**Why:** 2026-04-30 PR #271 swap WhisperX 跑 76min 訪談 SRT，38 處常見雙字詞被切到 cue 邊界——cue 67/68「然後」變「然」|「後」、cue 68/69「怎麼」變「怎」|「麼」，外加「因為」「我們」「什麼」「所以」「覺得」「困難」「希望」等。377 個 cue 文字長度剛好 = 20（占全部 18%）= force_break 觸發點。原 `_force_break` 只防英文 word boundary（`re.match(r"[a-zA-Z]", text[cut])`），中文純 char-level 硬切。

**How to apply:**

### 實作

```python
def _force_break(text: str, max_chars: int) -> list[str]:
    import jieba
    tokens = list(jieba.cut(text, cut_all=False))
    chunks: list[str] = []
    buf = ""
    for tok in tokens:
        if not tok.strip():
            buf += tok
            continue
        if len(buf) + len(tok) <= max_chars:
            buf += tok
        else:
            if buf.strip():
                chunks.append(buf.strip())
            if len(tok) > max_chars:  # 超長 token（URL 等）獨立成 chunk
                chunks.append(tok)
                buf = ""
            else:
                buf = tok
    if buf.strip():
        chunks.append(buf.strip())
    return chunks
```

### Dep

`jieba>=0.42`（19MB 純 Python，無 GPU 依賴）。`requirements.txt` + `pyproject.toml` 雙寫（per [feedback_dep_manifest_sync.md](feedback_dep_manifest_sync.md)）。

### 適用範圍

- nakama `shared/transcriber.py` `_force_break`（PR #274 已落地）
- 任何中文 / CJK SRT 工具（chunked subtitle 都會踩）
- 不適用純英文（既有英文 word boundary 保護就夠）

### 驗證

`scripts/analyze_srt_cuts.py` 可掃任一 SRT，找四類 issue：詞被切到 cue 邊界 / 空白 cue / 硬拆 = max 字 / 異常 gap。SRT 改動後 regression test：「詞被切」行從 38 → 0、長度 = 20 的 cue 仍在但不切詞中。

### 對應 test

`test_force_break_chinese_word_boundary` 蓋 8 個常見雙字（然後 / 因為 / 我們 / 怎麼 ...）；`test_force_break_short_text` / `test_force_break_long_english_token` 蓋邊界。
