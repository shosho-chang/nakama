-- 018_releases.sql — 發布線資料模型（video-publishing-plan Q3，ADR-055）
--
-- 三層模型：Episode（資料夾）→ Cut（winners.json 的一支成品）→
-- Release Target（Cut × platform，執行單位）。
-- 刻意不沿用 approval_queue（Q3 裁決：該表為 WP 文字稿設計，缺檔案身分/
-- 多平台群組/可查排程欄位/跨機器認領/斷點續傳五樣，且生產環境零次成功執行）。
-- DB 是 release plan + 執行狀態的 SoT；vault 只收發布完成後的結果（ADR-055）。

CREATE TABLE IF NOT EXISTS releases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode      TEXT NOT NULL,             -- "20260723 謝伯讓"（G:\footages 資料夾名）
    cut_id       TEXT NOT NULL,             -- winners.json id，如 "punch-L5"
    format       TEXT NOT NULL,             -- long | short
    work_title   TEXT NOT NULL DEFAULT '',  -- miner 工作代號（非發布標題）
    file_path    TEXT NOT NULL,             -- 匯出 mp4 絕對路徑（桌機）
    file_bytes   INTEGER NOT NULL DEFAULT 0,
    duration_sec REAL NOT NULL DEFAULT 0,
    rendered_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (episode, cut_id)               -- 重跑 publish_prep = 更新，不重複建
);

CREATE TABLE IF NOT EXISTS release_targets (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id         INTEGER NOT NULL REFERENCES releases(id),
    platform           TEXT NOT NULL,      -- youtube | ig | fb | threads（v1 只 youtube）
    status             TEXT NOT NULL DEFAULT 'draft',
        -- draft → approved → uploading → uploaded → published；failed 可重試
    title              TEXT,               -- 發布標題（packaging 交接檔來）
    description        TEXT,               -- LLM 草稿 + 修修改
    thumbnail_path     TEXT,               -- vault-relative（長片 only，短片 NULL）
    publish_at         TEXT,               -- ISO8601；YT 原生排程用（可查欄位，Q3 硬要求）
    video_id           TEXT,               -- 平台回傳 id（上傳完成即寫，防重複上傳）
    url                TEXT,
    error              TEXT,
    upload_session_uri TEXT,               -- resumable 續傳（crash 後續傳不重傳）
    updated_at         TEXT NOT NULL,
    UNIQUE (release_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_release_targets_status
    ON release_targets (status, publish_at);
