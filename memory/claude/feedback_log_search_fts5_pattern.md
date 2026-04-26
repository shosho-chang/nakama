---
name: SQLite FTS5 + logging.Handler 設計三點教訓
description: Phase 5C log search 沉澱：unicode61 對 CJK 是「整段一 token」、record.exc_info 不可吞、html.escape 用 sentinel swap 模式不是直接 mark
type: feedback
tags: [fts5, sqlite, logging, html-escape, cjk]
created: 2026-04-26
originSessionId: 7fe9bcfe-3f76-4b48-935a-d020e002d60d
---
Phase 5C (`shared/log_index.py` + `SQLiteLogHandler`) 完成後沉澱的三個 reusable lessons，下次做 SQLite FTS5 / 寫 logging.Handler 時用得到。

## 1. FTS5 `unicode61` tokenizer 把連續 CJK run 當 **一個 token**

`tokenize='porter unicode61'` 對 ASCII 行為完美（按空格切 token + porter stem），但 CJK 段落沒空格時會把整段 `備份完成` 當成 **單一 token**：

- ✓ 搜 `備份完成`（整段）→ match
- ✗ 搜 `備份` 或 `完成`（substring）→ 0 hit
- ✗ prefix wildcard `備*` 也不 work — `備份完成` token 整段才 index

**Why:** unicode61 沒切 CJK char boundary，把連續 letter-class char 視為一個 word。

**How to apply:**
- 對 ASCII / 混合 log msg：unicode61 即可（doc_index + log_index 都用這個）。
- 若 user-facing search 要 CJK substring：考慮 `tokenize='trigram'`（任何 3+ char substring 都 index）。但 trigram 不支援 prefix wildcard、且 2 char 以下 query 直接 0 hit。
- 真要強 CJK 要外部 jieba tokenizer。zero-dep 方案沒有完美解，**第一輪文件化限制**比過早優化好。
- 觀察用戶搜尋習慣後再決定要不要切 trigram 或加自訂 tokenizer。

## 2. 寫 `logging.Handler` 必須處理 `record.exc_info`

`logger.exception("...")` 是 production 最常用的 log 形態（捕到 except 時呼叫），對 postmortem search 是 **最重要** 的 log type。但 `record.getMessage()` 只回傳 user msg，不含 traceback — traceback 在 `record.exc_info`，要用 `Formatter.formatException()` 渲染。

**Why:** Python logging 把 `exc_info` 當 record 的獨立欄位，msg 不含。stdout `JSONFormatter` 在我 repo 裡寫成獨立 `payload["exc"]` 欄，但 SQLiteLogHandler 第一版漏寫 → traceback 沒進 db → /bridge/logs 搜不到 stack trace。

**How to apply:**
- 任何新 `logging.Handler.emit()` 都要：
  ```python
  if record.exc_info:
      extra["exc"] = logging.Formatter().formatException(record.exc_info)
  ```
- 把 traceback 進 indexed column（FTS5 contentless 可 index `extra_json`）讓搜尋有用。
- Test 用 `record.exc_info = sys.exc_info()` 模擬 `logger.exception()` + 搜「只在 traceback 內的 token」驗證 round-trip — 比搜 msg 更 robust。

## 3. FTS5 snippet HTML escape 用 sentinel swap，不是直接 `<mark>`

Naïve 寫法：`snippet(t, 0, '<mark>', '</mark>', ' … ', 15)` — FTS5 把 `<mark>` 直接塞進 snippet，但 user log msg 也可能含 `<script>`，渲染時用 `{{ snippet | safe }}` 會 XSS。

正確 pattern（從 `shared/doc_index.py` 沿用）：
```python
_MARK_OPEN = "\x01"   # ASCII control char — 不可能出現在合法輸入
_MARK_CLOSE = "\x02"

# FTS5 query：snippet(..., '\x01', '\x02', ...)
# 拿到 snippet 後：
return html.escape(raw).replace("\x01", "<mark>").replace("\x02", "</mark>")
```

**Why:** sentinel 用 ASCII control char (0x01/0x02) 不可能出現在合法 markdown / log msg。先 escape 整段（XSS 防護），再 swap sentinels 換回真 `<mark>` tag — 唯一允許的 HTML，明確控制。

**How to apply:** 任何 SQLite FTS5 + Jinja `| safe` template 的 search UI 都用這 pattern。Don't mix raw `<mark>` 進 snippet。Test 用 `<script>` literal + assert escaped (`&lt;script&gt;`) + assert `<mark>` 在期望位置驗證。

## 反模式（不要這樣做）

- ❌ `assert ... or True` — `or True` 讓 assertion 永遠 pass，不是真 test。如果 fragility 來自環境（chmod / OS 權限差異），改 monkeypatch deterministic 路徑。
- ❌ `JSON 處理 record extra={...}` 沒 `default=str` — datetime / Path 等 non-JSON 物件會 raise；給 `default=str` 兜底字串化。
- ❌ 直接讓 logger.exception 走 `getMessage()` — traceback 永遠丟。
