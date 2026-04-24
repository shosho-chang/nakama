# Usopp Publisher — Slice C2a（Docker WP staging + E2E golden path）

P9 六要素 task prompt。凍結於 2026-04-24；LiteSpeed Day 1 實測（原始 Slice C2 的 item 3/4）拆到 Slice C2b，等 VPS 部署後才能做。

---

## 1. 目標

在本機 Docker WP staging 上跑通一次 end-to-end publish flow（draft → SEOPress → WP REST publish → cache_purge 狀態機終點），驗證 Slice C1 daemon + Slice B publisher 的現實可行性，並把這個能力沉澱成**可重複執行的 `pytest -m live_wp` 流程**。

不在此 slice 內：用 Docker staging 取代所有 mock 單元測試（test_publisher.py 33 個 mock-based test 保留不動）。

---

## 2. 範圍

| 路徑 | 動作 |
|---|---|
| `tests/fixtures/wp_staging/docker-compose.yml` | 只讀（Slice B 已在） |
| `tests/fixtures/wp_staging/seed.sh` | 只讀（Slice B 已在） |
| `tests/fixtures/wp_staging/run.sh` | **新增** — one-shot 啟動 compose + seed + 抽出 app password 寫到 `.env.test` |
| `tests/e2e/__init__.py` | **新增** — empty |
| `tests/e2e/conftest.py` | **新增** — `live_wp` marker skip guard + WP client fixture + DraftV1 factory |
| `tests/e2e/test_phase1_publish_flow.py` | **新增** — 黃金路徑 E2E 測試 |
| `pyproject.toml` | 新增 `live_wp` 到 `[tool.pytest.ini_options].markers` |
| `agents/usopp/README.md` | 新增 "E2E test" 段落（how to run locally） |

**不碰**：
- `agents/usopp/publisher.py` 本身（Slice B 已完成）
- `shared/litespeed_purge.py`（LITESPEED_PURGE_METHOD 值變更屬於 C2b）
- `.github/workflows/`（CI 整合屬於 C2b 或之後；此 slice 是 opt-in local）

---

## 3. 輸入

- Slice B merged 產物：`agents/usopp/publisher.py`、`shared/litespeed_purge.py`、`shared/seopress_writer.py`、`shared/wordpress_client.py`
- Slice B merged 的 Docker 設施：`tests/fixtures/wp_staging/docker-compose.yml` + `seed.sh` + `functions-snippet.php`
- 既有 mock 測試範本：`tests/agents/usopp/test_publisher.py` 的 `_make_draft()` / `_make_request()` / `_enqueue_approved()` helper
- Schema contract：`shared/schemas/publishing.py` 的 `DraftV1`、`PublishRequestV1`、`PublishResultV1`
- Staging 帳號約定：`admin / nakama_test_pw`；REST user 為 `nakama_publisher`（app password 由 seed.sh 動態產生）

---

## 4. 輸出

1. **`run.sh`** — 一鍵在 host 上跑：
   - `docker compose up -d --wait`（等 healthcheck）
   - `docker compose exec wp bash /seed.sh` + tee 到 log
   - 從 log 解析 app password → 寫進 repo root 的 `.env.test`（gitignored）
   - 印出 `pytest -m live_wp --env-file=.env.test tests/e2e/` 指令
2. **`tests/e2e/conftest.py`** — 提供：
   - `pytest_collection_modifyitems` 讓 `@pytest.mark.live_wp` 在 `PYTEST_WP_BASE_URL` 未設時自動 skip（清楚訊息）
   - `live_wp_client` fixture：從 env 組一個 `WordPressClient`，ping `/wp-json/wp/v2/` 確認活著
   - `test_draft_factory` fixture：回一個 callable，建 `DraftV1` 時 primary_category 預設成 `nutrition-science`（seed.sh 有建）+ unique slug（`pytest_{uuid4().hex[:8]}`）
3. **`tests/e2e/test_phase1_publish_flow.py`** — 最少一個 test（可多個）：
   - `test_publish_flow_reaches_terminal_state` — 建 draft → enqueue + approve → 叫 `Publisher.publish()` → 斷言 `result.status == "published"`、`result.post_id is not None`、`result.seo_status in {"written", "fallback_meta"}`、`result.cache_purged is False`（noop method）
   - 收尾：`wp.delete_post(result.post_id)` tear down（不留污染）
4. **`pyproject.toml` marker**：
   ```toml
   "live_wp: run against local Docker WP staging (requires PYTEST_WP_BASE_URL)",
   ```
5. **`agents/usopp/README.md`** — 新增段落：
   ```markdown
   ## E2E test（本機 Docker WP staging）

   ### 前置
   - Docker Desktop 跑起來
   - repo root 有 `.env`（或 `.env.test`）

   ### 跑測試
       bash tests/fixtures/wp_staging/run.sh
       pytest -m live_wp tests/e2e/

   詳見 `docs/task-prompts/phase-1-usopp-slice-c2a.md`。
   ```

---

## 5. 驗收（Definition of Done）

- [ ] `bash tests/fixtures/wp_staging/run.sh` 成功啟動 compose、seed 完成、產出 `.env.test` 含四個 `WP_SHOSHO_*` keys
- [ ] `pytest -m live_wp tests/e2e/` 在 Docker 已啟動時 至少 1 個 test pass
- [ ] `pytest tests/e2e/` 在 Docker **未**啟動時所有 live_wp test **skip**（不 fail，訊息清楚）
- [ ] `pytest tests/ --ignore=tests/e2e` 基線 1035 passed / 1 skipped 不退步
- [ ] `ruff check` + `ruff format --check` 綠
- [ ] 修修可以在乾淨 clone 上照 README 跑起來

---

## 6. 邊界（明確不做）

- ❌ **LiteSpeed Day 1 实测** — 在 Docker WP 跑不出真實 LSCache 行為（沒有 LSWS），等 Slice C2b 在 VPS 做
- ❌ **`LITESPEED_PURGE_METHOD` 值變更** — 仍留 `noop`，C2b 實測後才改
- ❌ **CI 自動 boot Docker compose** — 本 slice 是 opt-in local；GitHub Actions 整合屬於 C2b 或之後的 DX 專案
- ❌ **seed.sh 擴充 13 categories** — 黃金路徑用 seed 現有的 `nutrition-science` 即可；完整 13 category mapping 的 E2E 覆蓋是 C2b
- ❌ **WP 版本升到 6.9.4** — compose 既有註解已說明 6.4.3 是 Docker Hub 最新，等官方 image 再動
- ❌ **更動 `agents/usopp/publisher.py`** — 如果 E2E 跑出 bug，flag 到本文檔的「發現 issue」區，**不要**在本 slice 內悄悄改 publisher

---

## 7. 發現 issue（執行過程補寫）

*（執行者在 E2E 過程中若發現 Slice B 的 bug，補在這裡，不改 publisher。）*
