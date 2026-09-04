# 桌機 Python 直譯器盤點（2026-09-04）

## 為什麼要有這份文件

`pyproject.toml` 只寫 `requires-python = ">=3.11"`——那是下限，不是釘死的版本。這台桌機
（Windows + RTX 5070 Ti）實際上同時需要**三個**用途完全不同的 Python，每一個的精確版本
都是被某個沒寫下來的相容性問題逼出來的：torch/CUDA wheel、DaVinci Resolve 的
scripting API、FastAPI 程式碼裡的 `datetime.UTC`。因為從來沒人把這三個原因一次寫在同一
個地方，過去半年這些限制被**各自獨立重新發現**了好幾次（PR #1232 的 Bridge 開機自啟事故、
2026-09-04 的 Resolve 21 fusionscript ABI 事故），每次都要重新診斷一輪。

這份文件就是那個「同一個地方」。改動任何一個直譯器的版本之前，先讀這裡，不要用感覺挑。

## 三個直譯器

| 用途 | 路徑 | 精確版本 | 為什麼是這個版本 |
|---|---|---|---|
| **repo venv**（podcast pipeline、Resolve 自動化） | `E:\nakama\.venv-v2` | **3.12.10**（釘在 repo 根目錄的 `.python-version`） | 兩個獨立限制剛好都落在 cp312：① torch 2.11.0+cu128／faster-whisper／Qwen3-ASR 這套 Blackwell（sm_120）wheel 是對 cp312 建置的；② DaVinci Resolve 21.0.3 的 `fusionscript.dll` 也是 cp312——3.10 與 3.14 在 `import DaVinciResolveScript` 當下都會 ACCESS_VIOLATION 崩潰、沒有 traceback（2026-09-04 實測）。**只要換版本，兩邊都可能同時炸**，改動前兩邊都要重驗 |
| **Bridge／FastAPI app server** | `C:\Python314` | 3.14.x（沒有精確釘版本，只要求 ≥3.11） | 程式碼用 `datetime.UTC`，需要 3.11+；PR #1232 選了當時桌機上已有的 3.14。這個選擇本身沒有下限以外的理由，未來要換版本風險較低（不像 `.venv-v2` 卡在 cp312 這個精確 ABI） |
| **legacy／WhisperX**（ADR-063 之前的舊字幕路徑，已退役，只做 forensic） | `C:\Users\Shosho\AppData\Local\Programs\Python\Python310` | 3.10.6 | 歷史原因，不要用於任何新工作。ADR-063 後正式路徑是 Memo Dual-Audit，不經過這個直譯器 |

`.venv`（無 `-v2` 後綴）是更早的殘留，Python 3.10，只在復盤舊 commit 時有意義，新工作不要碰。

## `.venv-v2` 為什麼一直斷

2026-08 那次建置用的 Python 3.12，不是透過任何會登記進 Windows registry 的正式安裝流程裝的
（可能是可攜式 zip 或某工具夾帶的副本）。所以它：
- **不出現在** `HKCU:\Software\Python\PythonCore` 或「新增或移除程式」
- **沒有任何東西保護它不被清理**——任何磁碟清理、資料夾搬移、跟它相關的軟體移除，都可能
  把它連帶清掉，而且 `.venv-v2` 本身的 `Lib/site-packages`（幾 GB 的 torch/CUDA wheel）完全
  不會受影響、看起來一切正常，直到有人真的呼叫 `python.exe` 才會炸

2026-09-04 用 `winget install --id Python.Python.3.12 -e --scope user` 重裝，這次**有**登記進
registry。只要之後沒人再用可攜式 zip 去「修」這個直譯器，這個特定死法不會再發生。

## 如果 `.venv-v2` 又斷了

**不要手動猜路徑重寫 `pyvenv.cfg`，也不要每次都重新 `winget install`。** 跑：

```powershell
powershell -ExecutionPolicy Bypass -File E:\nakama\scripts\repair_venv_v2.ps1
```

這支腳本只做一件事：查 registry 有沒有 `.python-version` 釘的那個精確版本，**有的話直接修好
`pyvenv.cfg`，不用問、不用裝任何東西**（只是把指標修回已經在那裡的直譯器）。只有連 Python
本身都不見了，才會印出 `winget install` 指令要你自己跑——那才是真的需要新裝軟體、需要你同意
的時刻。

podcast-pipeline skill 的 S0.0 健檢會自動先跑這支腳本。

## 改動任何一個版本前

1. 先查這份文件，確認會不會撞到另一個直譯器的限制（尤其 `.venv-v2`：torch wheel 和 Resolve
   ABI 兩邊都要重驗，不能只驗其中一邊就換版本）
2. 改完在這份文件裡更新版本號與理由，不要讓下一個人（或下一次的我）重新診斷一次
3. `.venv-v2` 換版本要同步更新 `.python-version` 與這份文件的表格
