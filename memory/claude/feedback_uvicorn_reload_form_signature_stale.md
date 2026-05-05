---
name: uvicorn --reload on Windows can leave route signature stale even after WatchFiles "Reloading..." log
description: 改 FastAPI route signature（特別是 Form/File 參數預設值）後，uvicorn --reload 在 Windows 偶爾會 log "Detected changes... Reloading" 但 OpenAPI schema 沒換；驗證真的 reload 要 curl /openapi.json 看 required 欄位，不要只信 log
type: feedback
created: 2026-05-05
---

改 FastAPI route 的參數簽章（`book_id: str = Form(...)` → `str | None = Form(None)`）後，**uvicorn `--reload` 在 Windows 不能保證真的把新版本載進 ASGI app**。Symptoms：

- `.uvicorn.log` 有寫 `WARNING: WatchFiles detected changes in '...' . Reloading...`
- 之後新 request 都還是依舊 schema 處理 → 422 `Field required` for the parameter that should now be optional
- `curl /openapi.json` 看到 `"required": [..., "book_id"]` 跟新 code 不符就是中招

**Why**：reloader subprocess 重啟 worker 但 importlib 的模組快取或 FastAPI 的 route 收集對某些 edit pattern 特別不可靠；尤其同一 reload cycle 內連續多次 edit、或 edit 期間有 in-flight request、或 Windows mtime 解析度（2 秒）讓兩次寫入看起來同一時間戳，都會讓 reloader 「以為已經處理完」。本機 dev 偶發，CI / production 不會撞（systemd cold start）。

**How to apply**：
- 改完 route signature 後**永遠**用 `curl http://127.0.0.1:8000/openapi.json | jq '.components.schemas.<Body_xxx>'` 驗 required 欄位 + property 型別跟新 code 一致；只看 log 的 "Reloading..." 不算數
- 422 `Field required` 對某個你「明明已經改成 optional」的參數 → **第一假設是 stale reload**，不要懷疑 FastAPI 語法
- 修法：`netstat -ano | grep :8000` 找 PID，`cmd //c "taskkill /F /PID <pid> /T"` 殺掉 reloader 整棵樹（包含 spawn worker），重啟 `.venv/Scripts/python -m uvicorn thousand_sunny.app:app --reload --port 8000 --host 127.0.0.1`
- 如果是頻繁切 branch 觸發，先看 `feedback_shared_tree_devserver_collision.md` 那個是 mtime 觸發 reload 跑「不對 branch」的 code；**這個 feedback 是反過來：reload 沒抓到對的 code**

撞過：PR #429 上線當下 user 上傳得到 422 missing book_id，OpenAPI schema 證實 required:["bilingual","book_id"] 跟 disk 上 code 不符；強制重啟後立即恢復 required:["bilingual"]。
