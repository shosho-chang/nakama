# Zoro 健身教練計畫 v0.1 — 4 路 Panel Review 整合

**Status:** Panel review 完成，待整合為計畫 v2
**Date:** 2026-06-29
**Author:** Claude (Opus 4.8) — 綜合 4 個 sub-agent 對抗式 review
**Reviews of:** [`2026-06-29-zoro-coach-implementation-plan.md`](2026-06-29-zoro-coach-implementation-plan.md)
**Reviewers:** ① 運動科學/教練法 ② 軟體架構與 Nakama 整合 ③ API 可行性與風險 ④ 產品範疇與 UX

---

## 0. 總結論

| 角度 | Verdict |
|---|---|
| 運動科學 | **需修訂**（漸進引擎數學 + 併行干擾兩塊需大改） |
| 軟體架構與 Nakama 整合 | **需大改**（整合層假設大多與實際 codebase 不符） |
| API 可行性與風險 | **需修訂**（3 條 load-bearing 假設已鬆動，須 Phase 0 重驗） |
| 產品範疇與 UX | **需修訂**（HITL 顆粒度 + 輸入摩擦 + 回饋太晚） |

**共識：** 計畫的**運動科學閉環方向正確、WP1–WP3 核心（讀回 + 漸進 + 課表 guardrail）可保留先行**，分期務實、Phase 0 spike 觀念對。但**四方一致認為不能照現狀進 Phase 1**——整合假設、漸進引擎數學、併行訓練干擾、HITL 顆粒度與資料輸入摩擦，都必須在動工前修。**沒有任何一位說「可照做」。**

**最有價值的發現來自架構審查**（實讀 codebase）：計畫多項「重用既有系統」其實是幻覺，反而漏用了已 ship 的系統。

---

## 1. P0 — 動工前必修

### A. 整合層：計畫對 Nakama 既有系統的假設大多錯誤（架構）

- **「Google Calendar MCP」+ `find_free_time` 是幻覺。** Nakama 用直接 Python lib `shared/google_calendar.py`，函式叫 **`find_free_slots`**；而且 **ADR-040 / ADR-041（已 merge）已 ship 完整「週計畫 → `plan[]` → 投影成 calendar event → Nami 雙向 sync」系統**（`calendar_scheduler.py`、`weekly_writer.py`、`calendar_reconcile.py`）。→ WP7/WP8 不可重造輪子，須改接既有系統，並遵守 ADR-041「vault 是 SoT、calendar 是 downstream projection」鐵則。
- **Nakama 沒有任何 MCP client harness。** 決策 #1（Tredict MCP）與 #4（GCal MCP）都假設能 host/consume MCP，但全 repo 無 MCP client；Tredict 官方 MCP 是給 Claude/ChatGPT Desktop 用，非 headless VPS。→ 「接 MCP」是一筆未列出的大型前置工作，須從「假設」降為 **Phase 0 spike**；Tredict 落地真實選項可能是打它的底層 HTTP API。
- **「重用 ADR-006 HITL」不可行。** ADR-006 仍 `Proposed`，payload schema 寫死成發布專用（`PublishWpPostV1`，`extra="forbid"`），Garmin/行事曆寫入塞不進去（`approval_queue` 表已存在，骨架可用）。→ WP9 改為「**擴充** ADR-006 schema」，明列要新增的 Pydantic model / enum / FSM / UI 分支。
- **Zoro 角色衝突被嚴重低估（風險表標「低」）。** ADR-012 明定 `Zoro = 向外搜尋`，`agents/zoro/` 實為 keyword/SERP/社群 scout，與「對內私人健身教練」零交集。→ 升為**開工前必決**：用**新 agent**（如 Chopper 船醫，但需先確認「Chopper 社群 UI」命名未被佔用），寫新 ADR 記錄歸屬；不要叫 `zoro_coach`。

### B. 漸進負荷引擎的數學有兩個會生出錯誤建議的硬傷（運動科學）

- **E1RM 在高 reps 系統性失準，「Epley+Brzycki 取平均」會放大偏差。** ACSM 2026 肥大允許 30–100% 1RM 近力竭 → 會出現 15–30 reps 的組，E1RM 在此幾乎無意義，但計畫仍照算趨勢並用來判斷進步/觸發 deload。→ E1RM **只在 top set ≤8–10 reps 計算**；高 rep 改用 **rep-at-load PR + volume-load**。
- **deload 觸發依賴 RPE/RIR，但那是 optional 手動欄位、Garmin 根本不回傳** → 規則 99% 時間拿不到訊號，會 silent 退化成「E1RM 絕對值掉 5%」而被正常波動誤觸。→ 改用**客觀代理**（completed-rep 連續下滑、hard sets 逼近 MRV N 週）+ **時間性 fallback deload（每 4–6 週強制減量）**；RPE 降為「有則加權」。

### C. 併行訓練干擾被當一行風險帶過，但這是夏季同練重訓+耐力的核心問題（運動科學）

- 已驗證的可操作規則計畫全沒編進去：**同 session 重訓在前、耐力在後**；**同肌群重訓 vs 高強度耐力間隔 ≥6h／最好隔天**（**腿部重訓 vs 室內單車都重度徵召股四頭，是干擾最劇組合**）；**泳屬上肢主導，可優先與腿日配對、把車課配上肢/休息日**（降干擾的免費午餐）；干擾受**訓練年資與性別**調節。→ 升為 **WP3/WP7 一級排程約束 + `concurrent_guardrail` 函式**；Profile 新增 `season_priority` 與 `training_status`。

### D. 兩個產品級摩擦會直接殺死黏著度（產品）

- **HITL「每次寫入都要審」對單人自用是過度設計。** 研究的 HITL 本意是「防 AI 暴走」，不是逐筆蓋章。→ 改為**逐週審批一次（WP8）+ 風險例外攔截**（負荷跳幅 >10%、deload、醫療旗標、行事曆衝突才二次打斷），其餘自動執行 + 事後可 undo。
- **重訓 weight 靠手動輸入，是整個 volume-load 引擎的單點故障，卻只給一行風險。** → 加**「缺值補登 inbox」**（sync 後當晚在 Bridge/Slack 推補登卡，不是在錶面敲）；WP2 三態：完整→建議／部分缺→建議但警示／缺太多→先請補登。

### E. exerciseSets 讀回沒有 schema 保證（API）

- `get_activity_exercise_sets` 是對 Garmin 私有端點的**裸 passthrough（源碼 line 2554–2560，無 typed model）**。「weight 以公克、rest 以 REST set」是**未文件化的觀察值，非函式庫契約**，Garmin 改欄位不會報錯只會靜默回不同 dict。→ Phase 0 **dump 真實 raw JSON 存樣本** + 寫 **schema-validation adapter**（未知結構→拒算 + 告警）；WP2 加「volume-load 突變偵測」。

---

## 2. P1 — 重要

| # | 問題 | 角度 | 修正 |
|---|---|---|---|
| 1 | **Tredict 現行 README 已列 strength 為支援運動別**，與研究 §11.1「不能做重訓」矛盾 | API | Phase 0 實機驗：送 strength workout 看手錶是「目標重量+逐組」還是只剩計時 block；結果決定 WP5 存廢。決策 #2 從「不再重議」改「verify-then-lock」 |
| 2 | 資料模型缺 **時區 / schema_version / 自然鍵 upsert / 單位**；ADR-041 已有 naive-datetime 血淚 | 架構 | 含時間欄位改 `AwareDatetime`（+08:00）；加 `schema_version`；`StrengthSet` 宣告自然鍵 + sync upsert |
| 3 | **WP8 scheduled task：Nami 不可 cron 化**（`cron.conf` Nami `__main__` 為 NotImplementedError）；無 idempotency/重試 | 架構 | 改 `python -m agents.<coach> weekly-plan` 寫 `plan[]` + 入 HITL queue，**不直接呼叫 Nami**；加 run idempotency key + 失敗告警 |
| 4 | **VPS 認證故事框錯**：garth 已死但 python-garminconnect 自建 SSO、token 無限續期；真風險是首登 MFA / refresh token 被 revoke / Garmin 再改 SSO。且 `google_calendar.py` 已有「本機登入→token 搬 VPS」現成 pattern | API+架構 | §7/§9 改寫失效面；沿用既有 token pattern，開放問題 #1 視為已解 |
| 5 | **Garmin 原生 Builder「預載 target weight + 逐動作引導」未在你的錶型證實**（官方手冊講「記錄」非「預載」） | API | Phase 0 在實機驗；不支援則 WP5 退化成「課表卡片 + 手錶純記錄」 |
| 6 | **Phase 0 完全沒驗 GCal**（find_free_slots/scope/時區/寫哪個日曆）與資料新鮮度（週日 20:00 當天訓練可能還沒同步上雲） | API+產品 | Phase 0 補 GCal spike + 同步延遲處理 |
| 7 | **進步可視化（看到自己變強）排到 Phase 2**，但這是使用者最想要的情感報酬 | 產品 | 把「E1RM/volume-load 趨勢圖 + PR badge」最小子集拉進 **Phase 1**（WP2 已算出數字，呈現成本低） |
| 8 | **WP8 缺 cold-start / 漏訓補救 / 工作衝突 / 空狀態**；onboarding「第 0 週」基線流程完全空白 | 產品 | 補四個狀態 + onboarding；排不下時降頻策略 |
| 9 | **成功指標全是技術指標**，無一條反映「持續用 + 有價值」 | 產品 | 補採用指標：每週 review 率、加重建議採納率、weight 輸入完整率、一鍵主觀回饋 |
| 10 | **CWI 鐵則應 goal-aware + 部位-aware**（鈍化肥大不鈍化力量；局部非全身） | 運動科學 | 肥大 block 維持禁區；純力量 block 放寬為警告；對映當日訓練部位 |
| 11 | **恢復漏掉蛋白質/睡眠**（比冰浴重要的恢復槓桿；蛋白質還能緩解併行干擾） | 運動科學 | WP6/Profile 加蛋白質目標(1.6–2.2g/kg)提示 + 睡眠納入監控；「沒進步」診斷先排除恢復/營養 |
| 12 | **`fitness_level` 不是真正的策略 switch**；1RM 測試對新手是受傷風險卻被當可選 | 運動科學 | novice→線性漸進+不測1RM；advanced→週期化；安全 guardrail 硬編「新手/有傷史禁 1RM max 測試」 |
| 13 | adapter 抽象過淺：**Tredict 當重訓讀備援是假備援**（讀不到 set-level）；無切換觸發/health_check；未接既有 `shared/alerts.py` | 架構 | 移除假備援；補 health_check/降級語意；接既有告警 |
| 14 | 非 MVP WP（4/6/7/8/10）缺六要素的「範圍/輸入」，違反 CLAUDE.md P9 紀律 | 架構 | 補齊並指向**既有** module |
| 15 | 健康資料 + VPS token 資安過輕（token=帳號完整存取權；訓練史→第三方 LLM provider） | API | 加威脅模型：token 檔加密+檔權+撤銷 runbook；標明哪些欄位流向 LLM |

---

## 3. P2 與「可砍的過度設計」（單人自用視角）

- **可砍/延後**：車+泳雙上 → **先做室內車、泳延後**；雙實作 adapter（備援 B）→ 介面留著只實作一個 backend；WP6 獨立「恢復引擎」→ 瘦身成一條 if + 一行排程；多公式 E1RM 取平均 → 用單一公式（趨勢一致性比絕對值重要）；**自動週期化/DUP/macro（Phase 3）→ 單人大概率永遠用不到，移出 roadmap**。
- **路徑/慣例修正**：`db/strength_sets` → 用 `state.db` / `shared/state.py`；測試用 fixture/契約測試（既有 `monkeypatch` 範例）非真帳號；接 `set_current_agent` 做成本歸因。
- **逆向自動寫重訓列 out-of-scope = 正確**（已檢查，無問題，維持）。

---

## 4. 必須在 Phase 0 解決的矛盾與待驗假設

1. **Tredict 到底能不能做有意義的重訓**（reviewer 之間有矛盾：研究說不行、API reviewer 查到 README 說支援）→ 實機驗，牽動 WP5 存廢。
2. **exerciseSets 回傳 schema 契約 + null 行為**（不是「能讀一次」，是「欄位可不可靠」）。
3. **GCal `find_free_slots`/scope/時區/寫哪個日曆**。
4. **你的錶型是否支援預載 target weight + 逐動作引導**。
5. **Garmin token 在 VPS 無互動續期能撐多久**。
6. **資料新鮮度**：週日排程時當天訓練是否已同步上雲。

> Phase 0 應從「能不能跑通一次」升級為「**驗證契約 + 失效行為 + UX 摩擦量測**」。

---

## 5. v2 應做的結構性改動（摘要）

1. **整合層重寫**：接 ADR-040/041 既有行事曆系統；移除 MCP 幻覺（MCP 落地改 Phase 0 驗）；WP9 改「擴充 ADR-006 schema」。
2. **Agent 身分**：新 agent（非 Zoro）+ 新 ADR。
3. **漸進引擎**：E1RM 只低 rep；deload 客觀代理 + 時間 fallback；指標分層（hard sets 為主，volume-load 不跨動作加總）。
4. **併行干擾**：升一級排程約束 + `season_priority` + `training_status`。
5. **產品**：HITL 改逐週 + 風險例外；加缺值補登 inbox；進步可視化拉進 Phase 1；WP8 補狀態 + onboarding；加採用指標。
6. **範疇瘦身**：先車後泳；單 backend；WP6 瘦身；砍自動週期化。
7. **資料模型**：tz-aware、schema_version、自然鍵 upsert。
8. **補運動科學**：蛋白質/睡眠、新手/進階分流、1RM 測試安全、暖身、推拉平衡。
9. **資安**：健康資料威脅模型 + token 加密/撤銷 runbook。

---

*下一步：依本整合矩陣產出計畫 v2（對齊 codebase 現實 + 修齊 P0/P1）。建議 v2 先做一輪 integration-grounding pass：實讀 ADR-040/041/012/006 + `shared/google_calendar.py`/`calendar_scheduler.py`/`state.py`/`base.py`/`cron.conf`，再重寫整合層與 WP4/7/8/9。*
