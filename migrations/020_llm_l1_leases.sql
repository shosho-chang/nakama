-- ADR-070 S1：L1（Claude 訂閱 / Agent SDK）機器層級併發租約 + 用量紀錄欄位。
-- Canonical mirror: shared/state.py（_init_tables 會自動建立，這支只做文件鏡像）。

-- D2 第 5 項：每一列 = 一個正在跑的 CLI 子進程名額；expires_at 是 epoch 秒的 TTL，
-- process 掛掉沒 release 的名額過期後由下一次 acquire 清掉。
CREATE TABLE IF NOT EXISTS llm_l1_leases (
    lease_id    TEXT PRIMARY KEY,
    pid         INTEGER NOT NULL,
    agent       TEXT,
    call_class  TEXT,
    acquired_at TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_l1_leases_expires
    ON llm_l1_leases(expires_at);

-- D2 第 6 項：L1 / L2 用量紀錄。全部 nullable，舊 row 與舊路徑維持 NULL。
ALTER TABLE api_calls ADD COLUMN lane_actual TEXT;
ALTER TABLE api_calls ADD COLUMN model_actual TEXT;
ALTER TABLE api_calls ADD COLUMN rate_limit_status TEXT;
ALTER TABLE api_calls ADD COLUMN rate_limit_type TEXT;
ALTER TABLE api_calls ADD COLUMN rate_limit_resets_at INTEGER;
