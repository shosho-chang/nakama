---
name: Mac 開發環境 bring-up（Python + venv + deps）
description: /Users/shosho/Documents/nakama 的 Mac 機器初次啟動需裝 Python 3.12 + 建 .venv + pip install -e .，因為系統 python3.9 無 anthropic/pydantic deps
type: reference
tags: [mac, dev-setup, python, venv]
originSessionId: 23d6fe90-ddb9-4038-946e-a916801421f8
---
Mac 機器（2026-04-23 第一次用作 nakama 第二開發機）沒預裝專案需要的 Python 3.11+。系統 `python3` 指向 `/Library/Developer/CommandLineTools/.../python3.9`，無 deps。

## Bring-up 步驟

```bash
brew install python@3.12
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .                        # 讀 pyproject.toml dependencies
pip install pytest pytest-cov pytest-asyncio ruff  # dev tools（未在 pyproject main deps）
```

## 日常用法

每次開 terminal：
```bash
cd /Users/shosho/Documents/nakama
source .venv/bin/activate
# 現在 python / pytest / ruff 都是 .venv 版
```

## Gotchas

- `.venv/` 已在 `.gitignore`，不會進 commit
- 若之後升 Python 版本，`brew` + 重建 `.venv` 即可；requirements 都在 `pyproject.toml`
- `/opt/homebrew/opt/python@3.12/bin/python3.12`（實際 binary 在 `bin/` 不是 `libexec/bin/`）
- `pip install` 不帶 `-e .` 時不會把 local code 註冊成可 import；必須用 editable install
