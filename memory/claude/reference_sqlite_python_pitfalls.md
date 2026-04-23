---
name: Python sqlite3 常見誤解與陷阱
description: Nakama approval_queue 實作踩過的 SQLite + Python sqlite3 套件契約陷阱，寫 DB 相關 code 或 review docstring 時先對一下
type: reference
created: 2026-04-23
confidence: high
originSessionId: 749ec7ee-40b5-4729-9a94-d76671f993e2
---
Nakama `shared/approval_queue.py` + `shared/state.py` 實作與 code review 過程累積的 SQLite 與 Python `sqlite3` 模組陷阱。

---

## 1. `check_same_thread=False` 不加 mutex

**常見誤解**：`sqlite3.connect(..., check_same_thread=False)` 會自動加 mutex 讓多 thread 共用 connection 安全。

**實情**：這個參數只**關掉 Python 端的「connection 綁 thread」guard rail**，不會自己加任何 mutex。Python 官方文件明寫：「write operations should be serialized **by the user** to avoid data corruption」。

**真正的序列化來源**：
- **跨 process**：SQLite C 層的 file-level 鎖（WAL mode 允許 1 writer + N readers）
- **同 process 多 thread**：SQLite C 層 serialized threading mode（`sqlite3.threadsafety == 3`，Python 3.11+ 預設建置）

**Nakama 實際部署**：thousand-sunny 與 nakama-gateway 是兩個 systemd service process → file-level 鎖生效；單 process 內的 worker thread 靠 C 層 serialized mutex。

---

## 2. Python sqlite3 implicit transaction 與 `BEGIN IMMEDIATE` 打架

**陷阱**：預設 `isolation_level=""` 時 Python 會自動為 DML 語句開 transaction。此時如果 code 自己 `BEGIN IMMEDIATE`，在多 thread shared conn 會炸 `cannot commit, SQL in progress` / `cannot rollback, no transaction`。

**解法**：Nakama approval_queue 改用 SQLite 單一 statement `UPDATE ... RETURNING`（`claim_approved_drafts`），語意等價 `BEGIN IMMEDIATE` 但不跟 Python sqlite3 打架。見 ADR-006 §3。

---

## 3. 單 statement UPDATE...RETURNING 的原子性

**事實**：SQLite 對單一 statement 保證 atomic — 包括 `UPDATE ... WHERE id IN (SELECT ...) LIMIT N RETURNING ...` 這種 subselect + update 的複合，subselect + update 作為一個步驟原子執行。

**應用**：適合做 batch claim / dequeue 這類「選 N 個 + 標記為 claimed + 回傳完整 row」的 pattern，不需要明確 BEGIN / COMMIT。

**限制**：這個原子性是 statement-level。如果 Python loop 裡對每個 returned row 再做 DB 操作（例如 Nakama 的 compliance post-filter 用 `mark_failed` revert），那部分就不是同一個 atomic scope 了。Crash recovery 要靠 FSM + stale-claim reset 兜。

---

## 4. Python 裡 module 級 `assert` vs test 斷言

**陷阱**：把 `assert ALL_STATUSES == {...}` 放在 module top level，它在 **import 時**就跑，不是「測試時跑」。

**影響**：如果 Python 以 `-O` flag 執行（production optimized），assert 會被 strip 掉。生產部署對 assert 的依賴需要謹慎。

**Nakama 寫法**：module top-level assert 在 dev 環境 import 時就擋飄移，但 prod 若跑 `python -O` 會靜默通過。目前 Nakama 沒跑 `-O`，先不處理；但 docstring 要明寫「import 時 assert」避免下一位 reader 誤解為 test-only。

---

## 5. `check_same_thread=False` + 共用 connection 的風險

即使 SQLite C 層 serialized，Python 的 `sqlite3.Connection` 物件（對 cursor、transaction state 的管理）並**不是純 thread-local 設計**。多 thread 同時操作同一 connection 依然有 subtle 問題：

- Commit / rollback 狀態可能被 interleave
- Cursor 的 fetchmany / fetchall 在不同 thread 交錯讀可能得到 unexpected results

**建議**：需要真多 thread 並用 DB 時，用 `connection-per-thread` pattern（threading.local），而非 shared connection + check_same_thread=False。

Nakama 目前 `_conn: Optional[sqlite3.Connection]` 是 module singleton，這在 systemd-service-per-worker 的部署模型下 OK（每個 worker 是獨立 process，各有自己的 conn），但如果未來把 thousand-sunny 改成多 worker thread 架構，要重新檢討。

---

## 參考

- `shared/approval_queue.py` docstring（更新於 PR #82）
- `docs/decisions/ADR-006-approval-queue-atomic-claim.md` §3
- Python 3.12 sqlite3 docs: https://docs.python.org/3/library/sqlite3.html#sqlite3.connect
