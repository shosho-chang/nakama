---
name: Brook compose_and_enqueue production pipeline merged (PR #78)
description: 2026-04-23 Phase 1 Wave 2 Brook Mac lane 主線 merged；topic → DraftV1 → approval_queue 路徑就位，等 Usopp Slice B/C 做整合
type: project
tags: [brook, phase-1, wave-2, adr-005a, adr-006, pr-78]
originSessionId: 23d6fe90-ddb9-4038-946e-a916801421f8
---
## PR #78 合併紀錄

Feature branch `feature/brook-compose` → main（`70fed3b`），squash merge。
初版 `c00f23d` + code review 修補 `407f9bf`。13 檔 / +1450 / -41。

857 passed / 1 skipped；53 新 tests（11 tag_filter + 16 style_profile_loader + 9 compliance_scan + 17 compose_pipeline）。

## 交付範圍

- `agents/brook/compose.py` — 新增 `compose_and_enqueue()` production 路徑；**對話式 chat flow（`start_conversation`/`send_message`/`export_draft`）完全沒動**
- `agents/brook/style_profile_loader.py` — yaml + md loader，`detect_category()` ASCII 關鍵字 word boundary（CJK 走 substring）
- `agents/brook/compliance_scan.py` — regex seed vocab，**有明確 TODO banner 說不可 cron / 不可對外 until Slice B**
- `shared/tag_filter.py` — 白/黑名單 + strict（預設 False lenient，等 497-tag 匯入後切 true）
- `config/style-profiles/{book-review,people,science}.yaml` + `config/tag-whitelist.yaml` — 種子

## 整合介面（已凍結）

Brook 產 `DraftV1` → wrap `PublishWpPostV1` → `approval_queue.enqueue(source_agent="brook")`，`initial_status="pending"`。Usopp 走 `claim_approved_drafts(source_agent="brook")` 拿。

`reviewer_compliance_ack` Brook enqueue 時永遠 False；HITL 在 Bridge approve 時才翻 True；Usopp claim 若 compliance_flags 命中但 ack=False，自動 mark_failed。

## 已知限制 / 開 cron 前必修

1. **Compliance regex seed 漏洞大** — 治好 / 治療 XX 病 / 停藥 / 肝癌/肺癌/乳癌 / 99.9% 全不抓。等桌機 Slice B 的 `shared/compliance/medical_claim_vocab.py`
2. **無 prompt caching** — 22-25KB profile md 每 compose 整份重送，單次多 $0.025-0.03；未來加 `cache_control=ephemeral`
3. **`_extract_json_object` 用 find/rfind** — fails closed 成 ComposeOutputParseError，但遇到 LLM 回應含 example JSON 會錯撈區塊
4. **Few-shot 未輪替** — 目前整份 md 丟 LLM，未按 `_extraction-notes.md §3.3` 做 A/B/C 輪替
5. **Cost 未聚合到 enqueue** — `cost_usd_compose=None`；shared/state.record_api_call 有 token 但沒 rolled up

## Upstream 狀態（桌機 lane）

`git fetch` 2026-04-23 看到 `origin/feature/usopp-slice-b` 已推，桌機正做 Slice B：publisher + seopress_writer + litespeed_purge + medical_claim_vocab + migrations/002。整合測試（Brook enqueue → Usopp claim → WP publish）可望下一輪到位。

## 下一步 backlog（修修決定順序）

- `shared/gutenberg_validator.py`（ADR-005a §4，round-trip + whitelist）
- `/bridge/drafts` UI（`thousand_sunny/routers/drafts.py` + HTML）
- Review follow-ups 小 PR：prompt caching / `_extract_json_object` 穩健化 / dedupe `_new_operation_id`
