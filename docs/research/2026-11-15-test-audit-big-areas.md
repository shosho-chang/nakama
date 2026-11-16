# 測試審計：測試量最大的四區（2026-11-15）

範圍：`tests/shared`、`tests/brook/script_video/test_finished_cut_*.py`、`tests/test_bridge_packaging.py`、`tests/agents/franky/jobs/test_rental_watch.py`；`tests/scripts` 只做了 A 類（入口）與計時，沒有逐檔看 B 類。
基準：`origin/main` = `5a0c73e2`。全程唯讀，沒有改任何測試或程式。

**執行狀態（2026-11-16）**：修修核准「建議刪除」的 69 個，已在分支 `chore/test-audit-big-areas`（基於 `772e7368`）刪除；本報告的行號是刪除前的行號。「待裁決」105 個與「建議改寫」都還沒動。

前提（修修 2026-11-13／11-14 已裁決，本報告照辦）：功能一律保留、只刪測試、不改 production；租屋 watch 是現行功能。（11-13 的「影片線先不動」已失效：修修 2026-11-14 起親自從頭重新規劃照稿影片線。）

**與尚未合併的 PR 的關係**（2026-11-15 查證 `origin/feat/finished-cut-host-agent-only`、`origin/chore/remove-old-storyboard-line`）：
- PR #1396 刪了 `scripts/podcast_highlight_visual_orchestrator.py` 與其測試，並改了 `test_finished_cut_codex_semantic.py`（刪 4 個 Windows codex 解析測試）、`test_finished_cut_cli.py`、`test_finished_cut_composition.py`、`test_finished_cut_agent_handoff.py`。本報告引用這幾檔的**行號以 main 為準**；§4 已逐條註明 #1396 版本的行號。§4.1 引用的替代測試在 #1396 版本全部還在（逐一 `git grep` 確認）。
- PR #1397 刪了舊照稿分鏡流程的 19 個測試檔，與本報告四區不重疊，只改到 `tests/shared/test_hyperframes_cli.py`（§3.4 那組由 ×3 變 ×2）。

## 0. 結論先講

| | 測試數 |
|---|---:|
| **建議刪除**（每條都有指名、已讀過的替代測試） | **69** |
| 待修修裁決（A 類：被測程式沒有正式入口，但屬於你沒說停用的功能線／或沒有替代保護的守門測試） | 105 |
| 建議改寫（多半只刪一兩行斷言，測試數不變） | 約 75 條 |
| 看起來多餘、但在擋真實發生過的 bug → **保留** | 68 |

**省時間：幾乎沒有。** 69 個建議刪除的測試在本機序列總共 **1.24 秒**；連 105 個待裁決的也刪掉，再多 1.2 秒。這幾區本來就跑得很快（見 §1）。這次刪除的價值是「之後重寫時少改幾十個綁死內部名稱的測試」，不是縮短 11 分鐘。

真正花時間的在別處（§1），其中最大一塊不用刪任何測試就能拿回來：`tests/test_bridge_packaging.py` 167 個測試序列 38 秒，原因是 `client` fixture（`tests/test_bridge_packaging.py:201-216`）每個測試都 `importlib.reload(thousand_sunny.app)`。

## 1. 計時（本機，序列，`pytest --junitxml`）

指令：`pytest tests/shared tests/brook/script_video/test_finished_cut_*.py tests/test_bridge_packaging.py tests/agents/franky/jobs/test_rental_watch.py tests/scripts`
結果：3,859 個測試，牆鐘 148 秒；4 failed（見 §7）、16 skipped。

| 區 | 測試數（展開後） | 序列耗時 | 平均每個 |
|---|---:|---:|---:|
| `tests/shared` | 2,236 | 30.9 s | 0.014 s |
| Finished Cut（`test_finished_cut_*`） | 634 | 10.5 s | 0.017 s |
| `tests/test_bridge_packaging.py` | 167 | **38.3 s** | 0.23 s（中位數 0.247） |
| `tests/agents/franky/jobs/test_rental_watch.py` | 151 | 2.3 s | 0.015 s |
| `tests/scripts` | 671 | 56.8 s | — |

`tests/scripts` 的 56.8 秒裡，`test_podcast_highlight_visual_orchestrator.py` 一個檔就 28.5 秒——**已由 PR #1396 刪除**（連同被測腳本），合併後這 28.5 秒自然消失，本報告不再列。

CI 是 `pytest -n auto`（`.github/workflows/ci.yml:75`），序列秒數會被 worker 分攤，實際牆鐘省得更少。

## 2. 方法

- **A 類（沒有入口）**：在最新 main 重跑 2026-11-13 審計的靜態可達性腳本，入口擴大到 `.claude/skills`、`.agents/skills`、使用者層 `~/.claude/skills`、`docs/runbooks`、`config/`、`cron.conf`、`*.service`、`*.ps1`、`CLAUDE.md`、`pyproject.toml`、Bridge app、gateway、各 agent `__main__`。可達性結果再逐支 `git grep` 確認（§3.1 附證據）。
- **B 類（綁死寫法）**：5 個唯讀子代理分區逐檔讀，每條候選要附「刪掉後哪個別的測試會抓到」並親自讀過那支替代測試；用 `git log -L`／`-S` 查測試是哪個 commit 加的、是否在擋真 bug。我再抽查並交叉核對（例如 Finished Cut 兩半互相引用的替代測試沒有同時被建議刪）。
- **降級規則**：子代理自己說「沒把握」、或替代保護有前提／不保證的，我一律降成「保留」，不列入 69。共降級 8 條（§6）。
- 原始逐檔結論（每個檔都有一行，含「已檢查，無 B 類」）放在 side-session-notes `test-audit-big-areas/out_*.md`，本報告是彙整。

### 限制
- **repo 是淺複本**（`git rev-parse --is-shallow-repository` = true，最早 commit `0756fd74` 2026-05-10）。很多測試的來源卡在這個邊界，「擋過真 bug？」只能填「無證據」，不等於確定沒有。
- 沒讀 VPS 實際 crontab、Windows 排程、Sandcastle。
- `tests/scripts` 的 B 類沒有看。

---

## 3. `tests/shared`（2,236 個，30.9 s）

### 3.1 A 類：被測程式沒有正式入口 → **待修修裁決**（101 個）

grep 指令：`git grep -nE "<模組名>" -- '*.py' '*.md' '*.yaml' '*.html' '*.sh' '*.ps1' cron.conf ':!tests' ':!docs' ':!memory'`，另掃 `~/.claude/skills`。

| 測試檔 | 測試數 | 被測模組 | 入口證據 | 為什麼要你裁決 |
|---|---:|---|---|---|
| `tests/shared/schemas/test_ingest_result.py` | 23 | `shared/schemas/ingest_result.py` | 零 import；只在 `shared/schemas/reading_source.py:209` 的 docstring 出現；docstring 說給 `agents.robin.url_dispatcher` 用，該檔不存在 | PR #1389 以「Robin 的 URL ingest 結果 schema」保留。無任何程式用它 |
| `tests/shared/test_concept_dispatch.py` | 23 | `shared/concept_dispatch.py` | 零 import | 兩份 ADR 打架：ADR-042:39「必須保留」vs ADR-052:48「零非測試 caller 的孤兒模組—刪除候選」。且這檔會跑到 critical-path gate 的 `shared/kb_writer.py`（門檻 90%），刪前要量 coverage |
| `tests/shared/test_gutenberg_validator.py` | 22 | `shared/gutenberg_validator.py` | 零 import | ADR-005a:10、ADR-027:206 寫「保留，供 repurpose blog 復用」——刻意保留、尚未接線 |
| `tests/shared/test_tag_filter.py` | 11 | `shared/tag_filter.py` | 零 import；只在 `config/style-profiles/book-review.yaml:13` 註解提到 | 原呼叫者 `agents/brook/compose.py` 已被 ADR-027 刪；ADR-027:208 寫「repurpose 也要 tag filter」 |
| `tests/shared/test_brook_synthesize_store.py` | 11 | `shared/brook_synthesize_store.py` | 只被 `agents/brook/synthesize/*` import；synthesize 的入口只剩 `python -m agents.brook.synthesize`（只寫在 `thousand_sunny/templates/bridge/inventory.html:87`） | ADR-068:6 已退役 synthesize 的 review 頁面，但 CLI 本身沒說停 |
| `tests/shared/test_output_writer.py` | 5 | `shared/output_writer.py` | 零 import | `docs/VAULT-LAYOUT.md:236` 寫它是 N524 write-back 的落點，「query workflow 另開 task」——預留 |
| `tests/shared/test_closed_pool.py` | 4 | `shared/repurpose/closed_pool.py` | 零 import；只在 `agents/brook/line1b_extractor.py:32,77` 的 docstring 提到，而 `line1b_extractor` 本身也沒有入口 | Brook repurpose／Line 1 |
| `tests/shared/test_annotation_v3.py` 的 `test_migration_script_upgrades_v1_and_skips_v3`、`test_migration_script_dry_run_does_not_write` | 2 | `scripts/migrate_annotations_v3.py`（一次性遷移） | 只在 `shared/annotation_store.py:238` 註解提到 | 一次性腳本；PR #1389 因「所在檔是現役 Reader 測試」沒動 |

一句話：這 101 個對應的都是「程式還在、沒人呼叫、但你沒說停」的東西（Brook synthesize、repurpose／WP 發文工具層、Robin 預留介面、教科書時代的 concept_dispatch）。照你「功能都留著」的原則，我**不建議**刪；除非你明說這幾條線不會回來。

### 3.2 B 類：建議刪除（33 個）

| 測試（檔案:行號） | 擋什麼 | 替代保護（已讀） | 擋過真 bug？ | 可省 |
|---|---|---|---|---:|
| `seo_audit/test_metadata.py:340` `test_check_metadata_returns_9_checks` | 規則剛好 9 條、固定順序 | 同檔每條規則測試用 `next()` 找自己的 rule_id，少一條就 StopIteration（`test_m1_missing`…`test_o4_warn_missing`） | 無證據（淺複本邊界） | 1 |
| `seo_audit/test_performance.py:118` `test_returns_3_checks` | 同上 P1–P3 | `test_p1_skip_when_missing`、`test_p2_skip_no_crux`、`test_p3_skip_missing`、`test_full_pass_response` | 無證據 | 1 |
| `seo_audit/test_headings.py:89`、`test_structure.py:154`、`test_images.py:220`、`test_schema_markup.py:227` 各一個 `test_returns_N_checks` | 同上 | 各檔 `_by()` 用 `next()`（如 `test_headings.py:15`），每條規則都有測試 | 無證據 | 4 |
| `test_youtube_transcript.py:210` `test_module_surface` | `hasattr` 7 個名稱 | 同檔 :17-25 模組層 import 這 7 個名稱，少一個整檔 collection 失敗 | 無 | 1 |
| `seo_audit/test_llm_review.py:60` `test_llm_client_mock_spec_blocks_nonexistent_methods` | 測 `MagicMock(spec=Anthropic)` 本身，完全沒呼叫 production | 不需要；`shared/seo_audit/llm_review.py:29` 已改走 `shared.llm.ask` | 「Regression for A6」是測試寫法守則，不是這支程式的 bug | 1 |
| `test_kb_hybrid_search.py:280` `test_make_conn_creates_wikilinks_table` | 建表 | 同檔 :215 `test_make_conn_creates_required_tables` 一模一樣的斷言 | 無 | 1 |
| `test_compliance.py:199,208` 兩個「詞庫非空」 | 內部 dict 形狀 | 5 個類別各有走 `scan_text` 的偵測測試（`TestScanText::test_therapeutic_term_hits_medical_claim` 等） | 無 | 2 |
| `test_provenance_linter.py:38` `test_enforced_red_lines_are_2_and_5` | 常數 `== (2, 5)` | 全 repo 沒有程式讀 `ENFORCED_RED_LINES`；紅線 5 行為由 `test_concept_citing_another_concept_in_sources_rejected` 守 | 無 | 1 |
| `test_agent_sdk_l1.py:633` `test_concurrency_limit_constant_is_two` | 常數 `== 2` | `test_run_text_waits_then_times_out_when_machine_is_full`、`test_top_level_call_on_full_machine_would_block`；ADR-070:122 寫這是可調參數 | 無 | 1 |
| `test_digest_indexer.py:147` `test_canonical_types` | `DIGEST_TYPES == ("pubmed","ai")` | `TestLatestPerType::test_empty_vault`（`shared/digest_indexer.py:96` 迭代此常數） | 無 | 1 |
| `test_litespeed_purge.py:36,48` | 兩條都只斷言 noop 回 False | `test_default_returns_false_without_calling_client` 走同一路徑（`shared/litespeed_purge.py:68`） | 無 | 2 |
| `test_book_storage.py:263,287` | `PRAGMA table_info` 欄位集合、PK | `test_insert_then_get_book_roundtrip`（INSERT 列出全部欄位，少一欄 OperationalError）、`test_insert_duplicate_book_id_replaces_or_raises` | 無 | 2 |
| `test_state_db_book_progress.py:21,31,57` | 表存在、欄位、FK | `tests/thousand_sunny/test_books_progress_api.py::test_get_progress_returns_persisted_row`；FK 由同檔 `test_book_progress_fk_blocks_orphan_insert` | 無 | 3 |
| `test_annotation_store.py:388,398,408` | 三個 unsynced_count 只換 item 型別 | `test_unsynced_count_never_synced_returns_all`（混合型別）；實作與型別無關（`shared/annotation_store.py:219-220`） | 無 | 3 |
| `test_annotation_store.py:44,53` | Highlight／Annotation round-trip | 兩者是 V1 的別名（`shared/annotation_store.py:38-39`），`test_schemas_annotations.py::test_highlight_v1_round_trip`、`::test_annotation_v1_round_trip` | 無 | 2 |
| `test_schemas_annotations.py:82` | 舊名稱能從 store import | `test_annotation_store.py:21-27` 模組層 import，失敗整檔 collection error | 無 | 1 |
| `test_annotation_v3.py:113` `test_comment_v2_alias_still_importable` | `CommentV2` 可用 | `test_schemas_annotations.py::test_comment_v2_carries_chapter_ref_and_body`（斷言更多） | 無 | 1 |
| `schemas/test_packaging.py:148` `test_title_v1_rank_5_requires_panel_note` | rank 5 同一分支 | `test_title_v1_rank_4_requires_panel_note`（同一個 `rank >= 4`，`shared/schemas/packaging.py:54`） | 無 | 1 |
| `test_llm_l1_dispatch.py:44` `test_cutover_set_is_gateway_only_after_s1a` | 常數 frozenset | `test_gateway_group_routes_to_l1_without_fixture_override` ＋ `test_non_cutover_group_keeps_legacy_path[cron/bridge/desktop]`（都不 monkeypatch 常數） | 無 | 1 |
| `test_llm_l1_dispatch.py:104` | patch 私有 `_ask_l1` 後 `assert_not_called` | `test_non_cutover_group_keeps_legacy_path[desktop]` | 無 | 1 |
| `test_kb_indexer.py:127,135` | 私有 `_split_h2_chunks` 的 dict 欄位 | `test_index_vault_frontmatter_title_stored` | 無 | 2 |

### 3.3 B 類：建議改寫（不減測試數，摘要）

`test_discard_service.py:96`（Windows 刪檔走回收桶，**資料保護，不可刪**；只拿掉逐字比對 PowerShell 指令開頭）、`test_llm_router.py:56,78,89,95`（**把真實模型字串寫死**，每換一次模型要改 4 個測試 → 改成從 `MODEL_REGISTRY` 取期望值）、`test_digest_ask.py:159,229`（同上，寫死 `claude-sonnet-4-6`）、`test_kb_indexer.py:93-118`、`test_doc_index.py:154-182`（測私有函式 → 改跑公開 `index_vault`／`search`）、`test_fleet_cover.py` 7 個（私有 `_layout()`）、`test_pagespeed_client.py`、`test_fca_events_client.py`、`test_gsc_client.py:45`、`test_agent_sdk.py:378,394`、`test_zoom_client.py:171`、`test_literature_writer.py:228`、`test_stable_file_hash.py:6`、`test_book_raw.py:95,109`、`seo_enrich/test_cannibalization.py:314`、`seo_enrich/test_striking_distance.py:112,125`。逐條理由見 notes 的 `out_shared_a.md`／`out_shared_b.md`。

### 3.4 保留：在擋真 bug（6 個）

| 測試 | 擋的 bug |
|---|---|
| `test_literature_writer.py:238` `test_bq_multiline_quote_all_in_one_blockquote` | 28a83e71 fix #891：修修回報「多段引文只有第一段進 blockquote」 |
| `test_literature_writer.py:244` `test_note_block_multiparagraph_joined_with_br` | 同 #891：「多段筆記第二段跑出去」 |
| `test_literature_writer.py:404` `test_is_own_source_path_segment_match` | f9e8eb47 fix #884：Literature KB 相關沒濾掉來源自身頁 |
| `test_hyperframes_cli.py:44`（×3；PR #1397 合併後 ×2，因 `hyperframes_worker.py` 被刪） | 讀原始碼比對，但擋 2026-11-06 上游發新版、npx 停在安裝提示、封面出圖卡 10 分鐘（3132a1d0 #1366） |

另外 B 類樣態、但**沒有替代保護的安全／政策守門**，保留：`test_permanent_layer_tripwire.py:109`（AI 不寫 `KB/Permanent/`）、`test_llm_context.py:263`（×7，各入口宣告計費 lane）、`test_llm_l1_dispatch.py:249`（不准繞過 lane 分派）、`test_reading_source_lister.py:241`、`test_agent_sdk_l1.py:541`（F17 逾時要用 anyio）、`test_reading_source_registry.py:178`（×11 語言對照表）。

---

## 4. Finished Cut（634 個，10.5 s）

Finished Cut 的模組全部有入口（可達性分析零死碼），沒有整檔 A 類。

### 4.1 建議刪除（33 個）

**(a) 測的是「只有測試在用的替身」— 10 個**：`test_finished_cut_production.py` 測 `_engine.py:943-1163` 的 `InMemoryProductionSystem` 與模組層級 `advance`／`request_revision`／`inspect_current`。`grep -rn InMemoryProductionSystem --include=*.py` 除定義外只有這支測試；`_engine.py:943` docstring 自稱「Fake system adapter」，`:1088`「for the legacy in-memory test adapter only」。正式路徑是 `FinishedCutProduction`（`_composition.py:30`）。

| 測試（`test_finished_cut_production.py`） | 正式路徑上的替代（已讀或已實跑） |
|---|---|
| :113 `test_authoritative_approved_cut_starts_current_director_request` | `test_finished_cut_composition.py::test_status_is_typed_read_only_and_survives_restart` |
| :126 `test_system_semantic_adapter_receives_only_the_current_typed_request` | `test_finished_cut_dispatch_recovery.py::test_failed_first_dispatch_requires_one_explicit_recovery_after_restart` |
| :140 `test_unknown_origin_command_is_rejected_before_run_creation` | `test_finished_cut_composition.py::test_production_rejects_legacy_or_unknown_command_before_run_creation` |
| :147 `test_old_static_response_cannot_authorize_current_run` | `test_finished_cut_store.py::test_forged_state_and_static_response_dropbox_are_never_authority` |
| :164 `test_current_director_proposal_is_accepted_at_most_once` | `test_finished_cut_store.py::test_current_proposal_is_accepted_once_and_survives_restart` |
| :254 `test_forged_state_view_cannot_authorize_a_materialization_plan` | `test_finished_cut_store.py::test_forged_state_and_static_response_dropbox_are_never_authority` |
| :277 `test_wrong_parent_dp_proposal_is_rejected_without_full_stage_rerun` | `test_finished_cut_store.py::test_wrong_parent_and_replayed_request_cannot_accept_a_stage` |
| :313 `test_dp_rejects_well_formed_asset_reference_outside_current_catalog` | `test_finished_cut_store.py::test_persisted_current_catalog_rejects_a_forged_opaque_reference` |
| :428 `test_targeted_retry_changes_only_one_event_in_the_typed_plan` | `test_finished_cut_store.py::test_targeted_revision_survives_restart_and_changes_only_one_event` |
| :493 `test_inspect_and_revision_use_only_exact_current_release` | `test_finished_cut_composition.py::test_targeted_revision_can_only_name_an_event_of_exact_current` ＋ `test_finished_cut_store.py::test_inspect_current_returns_only_public_immutable_finished_cut_view` |

刪掉後 `InMemoryProductionSystem` 與三個模組層函式會變成沒人呼叫的程式；依「不改 production」先不動。上表引用的替代測試都**不在**本報告的刪除名單上（已交叉核對）。

**(b) 綁死寫法 — 23 個**

| 測試 | 擋什麼 | 替代保護（已讀） | 可省 |
|---|---|---|---:|
| `test_finished_cut_layout_versions.py:58` `test_renderer_recipe_reads_the_contract_layout_version`（×4） | 私有 `_RECIPES` 跟 `layout_identity()` 自比（`_long_visual_renderer.py:84-122` 本來就是用它算的） | `test_finished_cut_hyperframes_renderer.py::test_every_generated_long_visual_role_uses_the_resolve_media_contract`（版本不合 `_long_visual_renderer.py:153-158` 會 raise；子代理已實跑） | 4 |
| `layout_versions.py:91,96,174,178` 四個 `..._is_the_vocabulary_not_a_copy` 類 | 斷言私有別名 `X._FOO is FOO`；改成直接 import 就紅 | 各自的行為測試：`test_finished_cut_resolve_fusion.py::test_each_typed_lane_has_one_fixed_video_track`、`test_finished_cut_visual_assets.py::test_all_current_generated_browser_components_publish_final_assets`、`::test_an_oversized_asset_backed_broll_builds_and_is_left_to_policy`、`test_finished_cut_policy.py::test_true_nine_minute_l2_density_with_sections_and_landscape_stock_passes` | 4 |
| `test_finished_cut_reason_codes.py:165` `test_each_collapsed_family_is_actually_used`（×4） | AST 掃原始碼找字面值 | `test_finished_cut_materialization.py::test_editorial_master_base_drift_fails_before_asset_or_timeline_mutation`（實際產出 3 種 code）＋ `test_finished_cut_codex_semantic.py::test_director_rejects_materializer_metadata_before_codex_dispatch` | 4 |
| `test_finished_cut_brand_badge.py:350` `test_the_vocabulary_and_the_derivation_agree_on_track_five` | 兩個常數互比（`_projection.py:261` 推導，恆等） | `test_every_overlay_becomes_one_track_five_placement[1/3/5]`（:243-244 斷言實際 track 5） | 1 |
| `test_finished_cut_derived_assets.py:62` `test_build_request_carries_the_neutral_catalog_context` | 建 dataclass 後斷言欄位等於傳入值 | `test_finished_cut_store.py::test_core_projection_keeps_chapter_hero_and_support_distinct_after_restart` | 1 |
| `test_finished_cut_plan_record.py:453` | write→read 相等（重複） | 同檔 :152 `test_a_record_survives_the_envelope_unchanged`；staging 殘留時讀取本來就 raise（`_plan_record.py:385-387`） | 1 |
| `test_finished_cut_policy_grades.py:41` `test_every_grade_is_one_of_the_three` | 內部表值域 | `decide()` 只看 `"blocking"`（`_policy.py:95-108`）；`test_the_chapter_transition_rule_is_the_only_thing_decide_blocks_on`、`test_decide_actually_blocks_on_a_blocking_grade` | 1 |
| `test_finished_cut_prepare_resume.py` 的 `[memory]` 參數（5 個測試各一） | 對測試替身 `_InMemoryResolveTransactionStore` 跑同一組斷言 | 同 5 支的 `[atomic]` 展開；正式路徑一律 Atomic（`_composition.py:603-605`）。代價：in-memory 替身少了一致性檢查（它只被 `test_finished_cut_materialization.py` 用） | 5 |
| `test_finished_cut_record_codec.py:449` | 段落總和不符時建構子不 raise | `test_finished_cut_policy_grades.py::test_a_precondition_stops_validate_before_the_rules_that_index_it[source_range_sum_mismatch-context0]` | 1 |
| `test_finished_cut_store.py:600` `test_production_run_persists_exact_editorial_context_as_run_authority` | 經私有 `production._store` 讀回 | 同檔 :617 `test_restart_uses_persisted_run_context_without_reresolving_same_cut`（第二行程 resolver 是空的仍讀到 context，只有確實落盤才成立） | 1 |

### 4.2 待修修裁決（4 個）

`test_finished_cut_reason_codes.py:99`（23 個 code 清單精確相等）、`:105`（24 個退役字串不得出現在原始碼含註解）、`:145`（`reason_code=` 不得用 f-string）、`test_finished_cut_layout_versions.py:182`（設計假設 tripwire）。它們不擋任何程式錯誤（`test_finished_cut_reason_codes.py:11-12` docstring 自承「它擋的不是某一個 bug」），所以也沒有「替代保護」可言。維護成本有實證：#1258 修品牌 badge 時要回頭改那份 23 個 code 的清單。子代理傾向刪；照規則我不直接列刪除。

### 4.3 建議改寫（摘要）

**PR #1396 對照**：`test_finished_cut_codex_semantic.py:144`（Windows codex 解析）已被 #1396 刪除，這條作廢。其餘行號在 #1396 版本：codex_semantic `:995` → `:903`、`:1201` → `:1066`、`:1244` → `:1106`；cli `:456` → `:457`；composition `:309` → `:311`、`:680` → `:682`；下面「prompt 逐字句」那幾處在 #1396 版本約往前移 90 行（例如 `master_cue_ids must be unique` 在 `:403`）。改寫時以 #1396 合併後的檔案為準。

把三支「讀整份原始碼字串、確認沒 import 舊模組」的測試（`test_finished_cut_cli.py:456`、`test_finished_cut_long_visual_renderer.py:260`、`test_finished_cut_resolve_fusion.py:1266`）合併成一支只看 `ast.Import` 的守門（省 2）；`test_finished_cut_production.py:194` 拿掉 `__all__` 32 個名稱精確相等；`test_finished_cut_codex_semantic.py:1201`（釘死上限 32）、`:1244`（讀原始碼＋AST）、`:995`（`__slots__`）、`:144`；`test_finished_cut_resolve_davinci.py:574`；`test_finished_cut_store.py:237-249`；`test_finished_cut_worker_packet.py:607`；`test_finished_cut_assets.py:134-141`（`fields()` 集合）；`test_finished_cut_composition.py:309`、`:680`；`test_finished_cut_materialization_fusion.py:292,295`（內部 port 呼叫次數）；`test_finished_cut_policy.py:951` 後半段。

**重寫 brook-director 時一定會撞到、但屬對外契約所以沒列 B 的（提醒）**：`test_finished_cut_codex_semantic.py` 多處斷言 prompt 含特定英文句（L352-354、L479-492、L644、L702-704、L742、L945-949；prompt 會落地成 `prompt.md`）；`test_finished_cut_worker_packet.py:313,347` 用 `==` 釘整份 `format_policy`。另 `test_finished_cut_store.py:124` 的 helper `_run_authority` 透過私有 `production._store` 讀內部狀態，全檔 29 處用它——重構時改這一個 helper 就能鬆綁。

### 4.4 保留：在擋真 bug（44 個）

| 測試 | 擋的 bug |
|---|---|
| `test_finished_cut_live_dp_catalog.py` 整檔（10） | 20260721 punch-L03：補買 8 支素材後 retry 的 catalog 仍是空的；value-L02：素材庫 52 支但第一次派發 catalog 是空的（4cd980eb）。直接測私有 helper，沒有公開 API 替代 |
| `test_finished_cut_source_range_conform.py` 整檔（13） | 20260721 value-L02 來源格數換算被誤擋 |
| `test_finished_cut_timeline_duration_drift.py` 整檔（9） | 20260721 punch-L03：59 段浮點累積成 1.05 格被誤擋 |
| `test_finished_cut_layout_versions.py:139` `test_no_active_implementation_is_half_declared`（8） | 7d3f4cb8 #1258「ADR-066 遷移時把品牌 badge 整類弄丟了」 |
| `test_finished_cut_policy_grades.py:34,45` | 5bdc499b：兩份名單各自漂走、唯一該擋的章節卡規則被放行；`_policy.py:71` 註解明寫靠這支 |
| `test_finished_cut_dispatch_recovery.py:396` `test_stage_packet_is_written_as_pure_ascii` | 20260805 林之晨：cp1252 主機上 episode_id 亂碼，DP 連錯到重試額度用完 |
| `test_finished_cut_long_visual_renderer.py:213` | 2026-09-16 章節卡字級改了但 recipe identity 沒變，改動沒上到螢幕 |

---

## 5. `test_bridge_packaging.py`（167 個，38.3 s）與 `test_rental_watch.py`（151 個，2.3 s）

兩檔都沒有 A 類（packaging router 由 Bridge app 掛載；租屋 watch 是 `cron.conf` 排程中的現行功能）。

### 5.1 建議刪除（3 個）

| 測試 | 擋什麼 | 替代保護（已讀） | 可省 |
|---|---|---|---:|
| `test_bridge_packaging.py:425` `test_approve_long_highlight_is_not_vetoed_by_a_missing_composition_receipt` | 名稱說測「receipt 缺漏」，但 `vault` fixture（:196-197）已寫好 receipt，**實際沒測到** | 同檔 :1873 `test_human_can_approve_long_package_without_composition_receipt` 真的 `unlink` receipt 後 approve（我親自讀過兩支） | 1 |
| `test_bridge_packaging.py:1997` `test_valid_long_composition_can_be_approved` | 只斷言 303 | :1639 `test_packaging_approval_hands_selected_title_and_thumbnail_to_publish`：同 fixture、同請求，另斷言 location 與參數，是嚴格超集 | 1 |
| `test_rental_watch.py:544` `test_default_config_file_loads` | 釘你在 `config/rental-watch.yaml` 的條件數值；`git log -L` 顯示 5e30c651、59f1710d、cdd5747d 三次修改都只是跟著你改條件改數字 | :1560 `test_repo_commute_table_is_present_and_sane`（呼叫預設路徑 `load_criteria()`，設定檔壞掉會紅）＋ `tests/thousand_sunny/test_bridge_rental.py::test_criteria_panel_renders_current_values`。**代價**：之後「條件被誤改」（例如誤刪大安區）不會有測試擋 | 1 |

### 5.2 建議改寫（摘要）

packaging 10 條只拿掉 class 名稱／CSS 值／JS 函式名稱那幾行（:1348、:1111、:1384、:1403、:2143、:1230、:1455），以及 :1474、:2677 改成 JSON 解析 `video/compositions/thumbnail_full/index.html:171` 的 `data-composition-variables`，不再連 6 個空白一起比對；:2982 是最典型的綁死寫法（釘函式名、參數順序、`indexOf` 出現次數），但它刻意守「判定規則只留一份」（f1185181 #1269），repo 沒有 JS 測試環境，刪了就完全沒防線——等有 JS harness 再改。租屋：`_floor_sig` 4 例併進 `test_same_unit_rules`（淨省 1）、:986 改走公開函式、刪 :284 一行恆真斷言。

**不刪測試、但能拿回最多時間的一件事**：`client` fixture（:201-216）每個測試 reload 整個 `thousand_sunny.app`，111 個測試用它；另有 `router_client`（:219-233）只掛 packaging router。把大部分測試改用輕量 app 或 module 層級共用，預估可把 38 秒壓到數秒（**未實測**，要動手時先量）。

### 5.3 保留：在擋真 bug（18 個，都在 packaging）

| 測試 | 擋的 bug |
|---|---|
| :2505、:2522、:2534（CSS 權重與 composition 一致性，讀 CSS 原始碼） | 6cb6692e（2026-08-29）：選中的分頁看不出選中（修修回報「點下去都沒有顯示我目前正在處於哪一個選項」）；預覽凍住而成品沒有、所見不等於所得 |
| :2559、:2565、:2573、:2585 `_watcher_covering` ×4 | e700437b：「排隊中」和「沒人在聽」長得一樣；2026-08-29 跑著的 watcher 綁在別的 cut |
| :2793、:2822、:3315 `_render_failure_sentence` ×3 | 79bd560e（修修看到 TimeoutExpired traceback）、13602ac6（蘇予昕 punch-L02 連續五次逾時，修修照提示按了五次） |
| :3344、:3356、:3371 `_timeout_streak` ×3 | 13602ac6 |
| :1873、:1936、:1955（×2）、:1980 human-override 變體 ×5 | 7ba87be6「make human approval authoritative」 |

租屋的 parametrize 重複（`test_parse_tax` 20 例等，理論上可併省約 9 例）**建議不動**：都是 cdd5747d 收的真實 591／好房網刊登文字樣本，純輸入→輸出、不綁寫法、跑起來接近 0 秒。

---

## 6. 子代理建議刪、我降級為保留的 8 條

| 測試 | 降級原因 |
|---|---|
| `tests/shared/test_schema_roundtrip.py:266`（×10） | 同檔 roundtrip 測試抓不到「`model_dump_json` 回傳 bytes」，替代保護是「pydantic 保證」而不是測試 |
| `tests/shared/test_state_db_book_progress.py:48`（PK） | 替代測試靠 SQLite 回傳順序，不保證會紅 |
| `tests/shared/test_gsc_client.py:51` `test_service_lazy_init` | 替代保護有前提（`test_from_env_happy` 不能加 patch） |
| `test_finished_cut_brand_badge.py:330` | 在 #1258 修 bug 的 commit 加入；若有人繞過 `worker_selectable` 旗標直接改私有集合，schema 測試抓不到 |
| `test_finished_cut_production_composition.py:270` | 替代測試在本機與 CI 都被 skip（`@requires_local_hyperframes`），無法實證；本來就不花 CI 時間 |
| `test_finished_cut_record_codec.py:316` | `1` vs `1.0` 這類 `==` 相等但字面不同的值沒有逐欄確認 |
| `test_finished_cut_layout_versions.py:163` | photo／non_editorial_clip 與 `_FINAL_ASSET_KIND`（`_engine.py:1909`）在 engine 路徑的覆蓋沒逐一確認 |
| `test_finished_cut_production.py:399` | 替代測試是否斷言 revision feedback 文字沒有確認 |

---

## 7. 順帶發現（與審查無關，未處理）

1. 在**未改動的 main** 上本機跑出 3 個不在已知清單的失敗：`tests/shared/test_r2_client.py::test_from_nakama_backup_env_reuses_base_credentials`、`::test_from_nakama_backup_env_override_credentials`（斷言值變成一串真實格式的 key，而非測試設定的 `'ak'`）、`tests/test_bridge_packaging.py::test_list_empty_vault`（回應變成登入頁）。看起來是讀到本機環境變數／`.env`，原因沒查；CI 是否也失敗沒確認。
2. `tests/scripts` A 類（只有可達性，B 類未審）：`test_prune_old_memories.py`（14，VPS 排程那行被註解掉，PR #1389 已決定保留）、`test_run_repurpose.py`（30，Line 1 repurpose，入口只在 `thousand_sunny/templates/bridge/repurpose_list.html:90`「Once shipped」說明）、`test_publish_register_external_short.py`（7，部分測無入口的外部 Short ingress）——都屬於未裁決功能線，不列建議。

## 8. 總數

| 區 | 建議刪除 | 待裁決 | 擋真 bug 保留 | 刪除省下（本機序列） |
|---|---:|---:|---:|---:|
| `tests/shared` | 33 | 101 | 6 | 0.27 s |
| Finished Cut | 33 | 4 | 44 | 0.62 s |
| packaging ＋ 租屋 | 3 | 0 | 18 | 0.35 s |
| **合計** | **69** | **105** | **68** | **1.24 s**（待裁決全刪再 +1.19 s） |

秒數取自本次 junit 實測，依測試名逐條比對（指令與名單在 notes 的 `sel.py`）。
