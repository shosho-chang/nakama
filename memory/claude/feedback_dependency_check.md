---
name: 安裝新依賴前必須檢查版本衝突
description: pip install 前先檢查新套件的依賴約束，避免降級核心套件
type: feedback
created: 2026-04-14
updated: 2026-04-14
confidence: high
---

安裝新 Python 套件前，先檢查它的依賴約束是否與現有套件衝突（特別是 anthropic、torch 等核心依賴）。

**Why:** 安裝 openlrc 時，它的 `anthropic<0.40` 約束把 Nakama 的 anthropic 從 0.89 降到 0.39，導致所有 Claude API 呼叫 crash（httpx 版本不相容）。pip 不會警告降級的嚴重性。

**How to apply:**
1. 安裝前先跑 `pip install --dry-run` 或 `pip show <package>` 查看依賴
2. 發現衝突時用 `--no-deps` 安裝，再手動補缺少的子依賴
3. 或只安裝真正需要的子套件（如 `faster-whisper` 而非整個 `openlrc`）
4. 安裝後立即驗證核心功能是否正常
