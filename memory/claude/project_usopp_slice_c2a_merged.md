---
name: Usopp Slice C2a merged — Docker WP E2E golden path
description: PR #101 2026-04-24 squash merged；opt-in local E2E test infra + /wp-json convention fix；Slice C2b（LiteSpeed Day 1）blocks on VPS 部署
type: project
tags: [usopp, phase-1, pr-101, slice-c2a, e2e, docker]
---

## 現況（2026-04-24 session end）

PR #101 squash merged at `916b8eb` on main。Slice C2a opt-in local E2E 可跑；C2b（LiteSpeed Day 1 實測）等 VPS Usopp daemon 部署後才能動。

## 產出清單

| 項目 | 檔案 | 備註 |
|---|---|---|
| P9 task prompt | `docs/task-prompts/phase-1-usopp-slice-c2a.md` | 六要素 + 明確 OOS 清單（LiteSpeed Day 1 / CI auto-boot / 13-cat coverage 都拆給 C2b） |
| One-shot boot helper | `tests/fixtures/wp_staging/run.sh` | `compose up --wait` → `seed.sh` → 解析 app password → 寫 `.env.test` |
| pytest marker | `pyproject.toml` | `live_wp: requires PYTEST_WP_BASE_URL + WP_SHOSHO_*` |
| Skip guard + fixtures | `tests/e2e/conftest.py` | `pytest_collection_modifyitems` 自動 skip、`live_wp_client` fixture 先 ping `/wp-json/wp/v2/`、`test_draft_factory` 產 DraftV1（`nutrition-science` 預設 cat） |
| 黃金路徑 test | `tests/e2e/test_phase1_publish_flow.py` | enqueue → approve → claim → `Publisher.publish()` → assert terminal + `nakama_draft_id` meta round-trip |
| .gitignore | `.env.test` 加入 | run.sh 產出的 creds 不進 git |
| README 更新 | `agents/usopp/README.md` | 加 E2E 如何跑、狀態改 C2a |
| BASE_URL convention fix | `.env.example` + `run.sh` | 去掉 `/wp-json` 後綴（見 `feedback_wp_base_url_convention.md`） |

## 兩次 commit（squashed 為 `916b8eb`）

| SHA | 內容 |
|---|---|
| `5791912` | 初版 Slice C2a：P9 prompt + run.sh + conftest + test + marker + README |
| `f8d5692` | code-review follow-up：WP BASE_URL `/wp-json` 修正 + task prompt `delete_post` spec drift 移除 + run.sh idempotency 敘述修正 + 簡體「实测」→「實測」 |

## Code-review 結果（5 Sonnet + 6 Haiku scorer）

| 候選 | Score | 判定 |
|---|---|---|
| `WP_SHOSHO_BASE_URL` `/wp-json` double-path 使所有 live E2E 404 | **85** | 必修（pre-existing convention bug，PR #101 是第一個 live consumer） |
| `run.sh` app password regex 被 spaces 截斷 | 5 | 誤判：WP `create_new_application_password` 回 raw 24-char 無空白 |
| `claim_approved_drafts` 回傳值丟棄 | 15 | 誤判：`test_publisher.py._enqueue_approved` 同模式 33 tests 綠 |
| `PublishComplianceGateV1()` 預設觸發 revert | 5 | 誤判：所有欄位預設 `False` |
| task prompt 寫 `wp.delete_post()` 但方法不存在 | 75 | 門檻外但順手修（spec drift） |
| `run.sh` idempotency 描述不準 | 75 | 門檻外但順手修（敘述不準） |

## 本機驗證 DX

```bash
bash tests/fixtures/wp_staging/run.sh       # boot + seed + .env.test
set -a && source .env.test && set +a
pytest -m live_wp tests/e2e/                 # 1 test pass
docker compose -f tests/fixtures/wp_staging/docker-compose.yml down -v
```

`PYTEST_WP_BASE_URL` 未設時 `pytest tests/` 基線 1045 passed / 2 skipped（1 新 `live_wp` + 1 pre-existing）。

## 相關記憶

- [feedback_wp_base_url_convention.md](feedback_wp_base_url_convention.md) — `/wp-json` convention 正典（本次發現）
- [project_usopp_slice_c1_merged.md](project_usopp_slice_c1_merged.md) — Slice C1 產出（daemon）
- [project_usopp_slice_b_pr77.md](project_usopp_slice_b_pr77.md) — Slice B 產出（publisher 核心）

## 下一步（記給自己）

1. **VPS 部署 Usopp daemon**（修修手動）— 照 `docs/runbooks/deploy-usopp-vps.md`；完成後才能做 C2b
2. **Slice C2b**（獨立 PR，VPS ready 後）：
   - 三種 LiteSpeed purge method（REST / admin_ajax / noop）在 shosho.tw 實測
   - `docs/runbooks/litespeed-purge.md` 決策表定稿
   - `.env` 的 `LITESPEED_PURGE_METHOD=noop` 改為實測勝出的值
   - 更新 `tests/e2e/test_phase1_publish_flow.py` 的 `cache_purged` 斷言
3. **可選 DX 改進**（C2c 或之後）：GitHub Actions CI 自動 boot compose + 跑 live_wp；目前 opt-in local 足夠
