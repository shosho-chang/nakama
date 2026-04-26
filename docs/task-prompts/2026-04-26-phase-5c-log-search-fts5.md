# Phase 5C — 結構化 log search UI（SQLite + FTS5）

**Framework:** P9 六要素（CLAUDE.md §工作方法論）
**Status:** 草稿，**待修修凍結後動手**
**Source plan:** [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) §Phase 5
**Pickup memo:** [`memory/claude/project_quality_uplift_next_2026_04_27.md`](../../memory/claude/project_quality_uplift_next_2026_04_27.md)
**Reference impl:** [`shared/doc_index.py`](../../shared/doc_index.py) + `/bridge/docs` route — 整套 FTS5 pattern 已在 repo 內 production-tested，5C 大幅 reuse

---

## §0. 為什麼 P9 / 為什麼凍結

5C 跨 5+ 檔案、含 schema migration、有 hot-path（log handler 可能 throw）— 不是單檔 P7。Plan 估 2 天，實際範圍要先框死避免 scope creep（可能 expand 成 grafana-style 全家桶）。

凍結點 = 把「IN scope / OUT of scope」白紙黑字寫死，避免實作中糾結。

---

## §1. 目標（一句話）

讓修修在 `/bridge/logs` 用全文 + level + agent + 時間區間搜 nakama 結構化 log，從「為什麼這個 cron 失敗」找到根因到 < 30 秒，取代「ssh 上 VPS 看 journalctl + grep」的當前流程。

## §2. 範圍（精確檔案路徑）

| 檔案 | 動作 | 理由 |
|---|---|---|
| `shared/log_index.py` | **新建** | FTS5 index + write API + search API；mirror `shared/doc_index.py` 的 class shape |
| `shared/log.py` | **改** | `JSONFormatter` 旁路 `SQLiteLogHandler` 同步 insert（對齊 `nakama.*` logger）；保留 stdout 不動 |
| `thousand_sunny/routers/bridge.py` | **改** | 加 `@page_router.get("/logs", ...)` route + auth gate + query param 解析；mirror `/docs` route 的 cookie + redirect pattern |
| `thousand_sunny/templates/bridge/logs.html` | **新建** | search box + filter form + result table + snippet highlight + pagination；mirror `docs.html` 視覺風格 |
| `thousand_sunny/templates/bridge/index.html` | **改** | landing 頁加一個 logs 卡 / link |
| `scripts/cleanup_logs.py` | **新建** | retention cron entry：刪 ts < now() - 30d 的 row + VACUUM |
| `tests/shared/test_log_index.py` | **新建** | 至少 6 個 unit test：insert / search / level filter / logger filter / time range / FTS5 syntax soft-fail / retention cleanup |
| `tests/test_log_handler.py` | **新建** | SQLiteLogHandler smoke：emit 不 raise + extra={} JSON-coerce + 失敗 fallback 不掛掉 logger |

預估 line delta：~600 添加 / ~30 改動。

## §3. 輸入（依賴）

- ✅ `shared/log.py` 既有 JSONFormatter / `_STANDARD_LOG_FIELDS` 集合 — 不重做，handler 借用
- ✅ `shared/doc_index.py` FTS5 pattern — `_MARK_OPEN/_MARK_CLOSE` sentinel + `tokenize='porter unicode61'` + snippet escape 直接 copy
- ✅ `thousand_sunny/routers/bridge.py` 既有 `_get_doc_index()` lazy singleton + `check_auth()` cookie guard pattern — 直接 mirror
- ✅ Bridge UI mutation pattern（`reference_bridge_ui_mutation_pattern.md`） — cookie auth + form post + 303；本任務基本是 GET 唯讀，但 retention `/api/logs/cleanup` POST 走這 pattern
- ⚠️ DB 落點 — 用獨立 `data/logs.db`（仿 `doc_index.db` 模式），**不**進 `state.db`，理由：(a) log volume 高、避免 VACUUM 連坐；(b) backup 策略不同（log 30d、state.db 永久）；(c) 出問題可獨立 wipe-rebuild

## §4. 輸出（交付物）

### 4.1 Schema（`data/logs.db`）

```sql
CREATE TABLE logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                  -- ISO8601 UTC, second precision
    ts_unix REAL NOT NULL,             -- 用於 range query / index
    level TEXT NOT NULL,               -- INFO/WARNING/ERROR/CRITICAL/DEBUG
    logger TEXT NOT NULL,              -- 'nakama.X.Y' dotted name
    msg TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}'  -- JSON.dumps(extra={}) of caller's structured fields
);
CREATE INDEX idx_logs_ts_unix ON logs(ts_unix DESC);
CREATE INDEX idx_logs_level_ts ON logs(level, ts_unix DESC);
CREATE INDEX idx_logs_logger_ts ON logs(logger, ts_unix DESC);

-- contentless FTS5 索引在 msg + extra_json 上，rowid 對齊 logs.id
CREATE VIRTUAL TABLE logs_fts USING fts5(
    msg, extra_json,
    content='logs', content_rowid='id',
    tokenize='porter unicode61'
);
-- triggers 自動同步
CREATE TRIGGER logs_ai AFTER INSERT ON logs BEGIN
    INSERT INTO logs_fts(rowid, msg, extra_json) VALUES (new.id, new.msg, new.extra_json);
END;
CREATE TRIGGER logs_ad AFTER DELETE ON logs BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, msg, extra_json) VALUES ('delete', old.id, old.msg, old.extra_json);
END;
```

WAL mode 開啟（`PRAGMA journal_mode=WAL`）讓 handler 寫和 reader 查不互鎖。

### 4.2 `shared/log_index.py` 公開 API

```python
class LogIndex:
    @classmethod
    def from_default_path(cls) -> "LogIndex": ...   # data/logs.db
    @classmethod
    def from_path(cls, db_path: Path) -> "LogIndex": ...

    def insert(self, *, ts: datetime, level: str, logger: str, msg: str, extra: dict) -> None: ...
    def search(
        self, q: str, *,
        level: str | None = None,
        logger_prefix: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LogHit]: ...
    def stats(self) -> LogStats: ...   # total / by_level / by_logger / oldest / newest
    def cleanup(self, *, older_than: timedelta) -> int: ...   # returns rows deleted
```

`LogHit` 含 `id, ts, level, logger, msg, snippet (highlighted, sanitized), extra`

### 4.3 `SQLiteLogHandler`（在 `shared/log.py`）

- 繼承 `logging.Handler`
- `emit(record)` 取 `record.created` / `levelname` / `name` / `getMessage()` + 收 extra fields（同 JSONFormatter 邏輯）
- 失敗 fallback：不 raise（會 break logger）— logging 模組 default behavior 是 print 到 stderr
- 在 `get_logger()` 第一次初始化時 attach（與 stdout handler 並行）；env `NAKAMA_LOG_DB_DISABLE=1` 可關（CI / 單元測試用）

### 4.4 `/bridge/logs` route

URL: `GET /bridge/logs?q=&level=&logger=&since=&until=&limit=50&offset=0`

- query string parsing + auth cookie redirect（mirror `/bridge/docs`）
- limit cap 200
- ts query param 接 `2026-04-26T00:00:00Z` 或 `2026-04-26`（auto-coerce）或 `1h ago` / `24h ago` shortcut
- 結果 render `logs.html` template

### 4.5 `logs.html` template UI

- search input（q）+ 4 個 filter（level dropdown / logger autocomplete-disabled text input / since / until）+ submit
- 結果 table：ts (relative + tooltip ISO) / level chip (色：ERROR 紅 WARNING 橘 INFO 灰) / logger / msg snippet / extra preview (collapsed)
- pagination（prev/next via offset）
- empty state 訊息（"no matches" / "嘗試: q=error level=ERROR"）
- 視覺對齊現有 docs.html / health.html 的 design tokens

### 4.6 Retention

- `scripts/cleanup_logs.py` — argparse `--older-than-days` default 30；call `LogIndex.cleanup()` + VACUUM
- crontab entry `0 4 * * * cd /home/nakama && /usr/bin/python3 scripts/cleanup_logs.py >> /var/log/nakama/cleanup-logs.log 2>&1`
- 寫進 `shared.heartbeat.CRON_SCHEDULES`（registry pattern from 5B-1）

### 4.7 Tests

- `tests/shared/test_log_index.py` 6+ tests（insert / FTS / filter combos / range / soft-fail / cleanup）
- `tests/test_log_handler.py` 3 tests（emit / extra preserve / fallback no-raise）
- 全 suite 跑過、0 fail
- ruff check + format clean

## §5. 驗收（Definition of Done）

| # | 條件 | 驗證方式 |
|:---:|---|---|
| 1 | `from shared.log import get_logger; logger = get_logger("test"); logger.warning("hello", extra={"x": 1})` 後 db 有對應 row | `tests/test_log_handler.py` + manual `sqlite3 data/logs.db "select * from logs"` |
| 2 | `/bridge/logs?q=hello` cookie auth 後返回 200 + render hit | Mac dev server `uvicorn` + browser 視覺檢查 |
| 3 | `level=ERROR` filter 只回 level='ERROR' | unit + manual |
| 4 | `since=24h ago` 只回 ts > now()-24h | unit |
| 5 | FTS5 syntax bad input（`"unbalanced"`）回 empty list、不 500 | unit |
| 6 | `python3 scripts/cleanup_logs.py --older-than-days 30` 後 db 行數 < 之前 | unit + manual |
| 7 | full suite `pytest` 0 fail | CI |
| 8 | VPS deploy 後 cron tick 有寫進 db（5 分鐘 franky-health 跑完應有 ~5 row 含 `nakama.gateway` / `nakama.franky.*`） | ssh sqlite3 query |
| 9 | `/bridge/index` landing 頁有 logs 卡 link 到 `/bridge/logs` | 視覺 |
| 10 | `NAKAMA_LOG_DB_DISABLE=1` 時 logger 完全跳過 db handler（CI 環境用） | unit + grep CI workflow |

## §6. 邊界（明確不能碰）

**不可碰**：
- ❌ `state.db` schema — log 進獨立 `data/logs.db`，避免互相污染
- ❌ stdout JSONFormatter 行為 — 既有 `journalctl` / cron log 流程不可破
- ❌ 既有 `shared/doc_index.py` — 5C 是 reference impl，**不**修改它
- ❌ logger hot-path 加 latency > 5ms — SQLite WAL insert 應 <1ms，但要測
- ❌ 加任何 aggregation / time-bucket / chart — **那是 5B-3 的活**
- ❌ 加 SSE live tail — Phase 7+ 才考慮
- ❌ Cross-process log dedup — 兩個 service 都寫同 db 沒問題（WAL handles）
- ❌ 改 `/bridge/health` / `/bridge/cost` / `/bridge/docs` — 加 link 到 index 即可
- ❌ 覆蓋現有 .env 變數 — 新加的 `NAKAMA_LOG_DB_DISABLE` 進 .env.example

**可選 / 留下次**：
- DEBUG level 預設過濾掉（log volume 太大），看是否要加 toggle
- log size 監控 dashboard — 進 5B-3 一起做
- export to CSV / JSON download — 可後續 issue

## §7. 推薦執行序（同一 PR 內 commit 切法）

1. `shared/log_index.py` + `tests/shared/test_log_index.py`（純 db API，無 hot-path 依賴 — 先單獨綠）
2. `SQLiteLogHandler` 進 `shared/log.py` + `tests/test_log_handler.py`
3. `bridge.py` route + `logs.html` template + index landing 加 link
4. `scripts/cleanup_logs.py` + crontab entry doc
5. .env.example 補 `NAKAMA_LOG_DB_DISABLE=` 註解獨立行
6. 全 suite 過、ruff 過、PR 開、ultrareview / 自我 review

## §8. 風險

- **Hot-path latency**：每 log call 一次 SQLite insert。WAL mode 應 <1ms，但 log burst（e.g. franky-news cron 一次 100 條）可能 stall。**緩解**：handler 內部用 `queue.Queue` + 背景 thread flush；但這加 complexity，先做同步版量測，超出再加 buffer。
- **磁碟空間**：30 天 retention 估每天 1-5 MB，30d ~150MB 上限，可接受。但要監控 — 加進 5B-3 anomaly daemon range。
- **Schema drift**：未來若加新 extra 欄位，schema 不變（extra_json 是 free-form JSON），但 query UI 不知道有哪些 extra key — 做個 stats 頁顯示 top extra keys 即可（next iteration）。
- **CI noise**：CI run pytest 會 emit log 到 db — 用 `NAKAMA_LOG_DB_DISABLE=1` autouse fixture 預防。

## §9. 待決定（凍結前 user 拍板）

| # | 題目 | 預設 | 替代 |
|:---:|---|---|---|
| Q1 | DB 落點（`data/logs.db` 獨立 vs `state.db` 內加 table） | **獨立** `data/logs.db` | state.db 內 — 但 backup / VACUUM 互相影響 |
| Q2 | retention 預設 days | **30** | 7 / 14 / 60 |
| Q3 | DEBUG level 是否進 db | **過濾掉**（log_index.py default `min_level=INFO`） | 全部進，UI 預設藏 |
| Q4 | search UI 是否需要 logger autocomplete（dropdown of seen logger names） | **不做**（autocomplete-disabled text input） | 做 — 多 ~50 行 backend stats |
| Q5 | extra_json 顯示樣式 | **collapsed `<details>`** | 永遠展開 |
| Q6 | 是否寫 ADR | **不寫**（plan + task prompt 已足夠，5C 沒架構爭議） | 寫 ADR-013 — 但 over-engineer |
| Q7 | 是否上 ultrareview | **是**（多檔 + hot-path + auth + DB schema = 高 leverage） | 跳過 — 但 5C 比 5B-2 / 5D 大 |

修修凍結這 7 題後我直接動手；不夠決定就在 chat 問我。

## §10. 完工後 follow-up

- 更新 [`project_quality_uplift_next_2026_04_27.md`](../../memory/claude/project_quality_uplift_next_2026_04_27.md)（5C → ✅，下一個 5B-3）
- 寫個 short feedback memo `feedback_log_search_fts5_pattern.md` 記 reuse `doc_index.py` 的細節（給未來新 FTS5 場景借鑒）
