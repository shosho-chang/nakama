---
name: pip install whisperx 會把 torch 從 cu128 降到 CPU 版
description: 任何 pip install whisperx / pyannote.audio 後必驗 torch.cuda.is_available()；安裝後一律重跑 cu128 wheel
type: feedback
created: 2026-04-30
---

`pip install whisperx`（含 pyannote.audio 連帶）在 Windows 上會把既有的 `torch 2.11.0+cu128` 解析降級成 `torch 2.8.0`（**+cpu wheel**），CUDA 完全失效。

**Why:** 2026-04-30 ADR-013 引擎 swap 時踩到。原本 `torch.cuda.is_available()=True / Device=RTX 5070 Ti / CUDA 12.8`，跑 `pip install whisperx` 後變 `torch 2.8.0+cpu / cuda available=False`。WhisperX 雖然啟動但跑在 CPU、嚴重變慢。

**How to apply:**
- 任何 ML 套件 install 後**必跑** `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`
- 若顯示 `+cpu` 或 `cuda available=False` → 跑：
  ```
  pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu128 --upgrade
  ```
- 為什麼會降級：whisperx / pyannote 的 `torch>=X` 是 unpinned，pip 解析時會挑當下默認 PyPI wheel；cu128 wheel 只在 PyTorch 自己 index 上，pip 不會自動找
- 同類風險：所有依賴 torch 的套件（如 sentence-transformers、FAISS-GPU）都可能踩到
- 跟 `feedback_dependency_check` 互補（前者是版本衝突；本條是 wheel variant 默默降級）
