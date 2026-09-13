---
name: reference_test_interpreters
description: 這台機器上哪一個 Python 跑得動測試（py -3.14 有完整依賴、3.10 只有 ruff/whisperx），以及為什麼全 repo `tests/` 不能當 gate
metadata:
  type: reference
---

這台機器有三個 Python，**只有 `py -3.14` 有完整的測試依賴**：

| 指令 | 有什麼 | 用來做什麼 |
|---|---|---|
| `py -3.14` | pytest、cn2an、rapidfuzz、lxml、pypinyin、starlette… | **跑測試就用這個** |
| `py -3.10` | ruff、whisperx（見 [[feedback_no_gpu_heavy_work_until_user_ok]]） | lint、GPU 轉錄 |
| `py -3.12` | 沒有 pytest | — |

CLAUDE.md 寫的 `python -m pytest tests/` 解析到 3.14，所以那一行是對的；但**手動指定
版本的時候很容易挑錯**。用 `py -3.10 -m pytest` 會在 collection 階段就死在
`No module named 'cn2an'`／`pytest`，看起來像「程式壞了」，其實是挑錯 interpreter。

## 全 repo `tests/` 不能當 commit 的 gate

`tests/` 跑滿要**約兩小時**（實測 6.5 分鐘只走到 5%）。而且 `tests/shared/test_raw_ingest.py`
少 `markdownify`，collection 直接中斷整場——要 `--ignore` 掉。

所以 gate 的範圍是：**改動模組自己的測試目錄**（例如 `tests/brook/script_video/`，
約 11 分鐘 / 1100 支）**＋逐一跑過每一個引用到改動 API 的檔案**。ADR-069 那七個階段
就是這樣驗的：模組套件 + 消費端（review adapter、Bridge、publish prep/upload/review、
packaging）各自跑綠。

`-q` 的輸出會被 pipe 緩衝住，所以 `| tail` 在跑完之前什麼都看不到——要盯進度就重導
到檔案再讀，不要以為它卡死了。相關教訓見
[[feedback_dont_run_full_suite_during_renders]]。
