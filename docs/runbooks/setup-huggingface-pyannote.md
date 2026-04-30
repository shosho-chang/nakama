# Setup — HuggingFace token + pyannote diarization

修修一次性手動操作（5-10 分鐘）。完成後 nakama transcribe pipeline（WhisperX 引擎）才能在訪談 podcast 上做 speaker diarization（自動分主持人 vs 來賓）。

依據：[ADR-013-transcribe-engine-reconsideration.md](../decisions/ADR-013-transcribe-engine-reconsideration.md)

## 為什麼需要 HuggingFace

WhisperX 的 speaker diarization 走 [pyannote-audio](https://github.com/pyannote/pyannote-audio) — 兩個 model 託管在 HuggingFace，需要：
1. HF 帳號 + read token（access）
2. 對兩個 pyannote model 各 click 一次「Accept terms」（EULA）— **免費，但是 click-through gate**

不做這步 → WhisperX 仍可純轉字幕，但**不會產 `[SPEAKER_00]` 標籤**，Line 1 訪談的「主持人 vs 來賓分軌」做不到。

## 步驟

### Step 1 — 申請 HuggingFace 帳號（如果還沒有）

1. 開 https://huggingface.co/join
2. 用 email 註冊，不需要付費、不需要驗證信用卡
3. 完成 email 驗證

### Step 2 — 產 Read Access Token

1. 登入後到 https://huggingface.co/settings/tokens
2. 點 **「+ Create new token」**
3. **Token name**：`nakama-pyannote`（隨意，方便日後 revoke 識別）
4. **Token type**：選 **「Read」**（不需要 Write 權限，pyannote 只 download model 不 push）
5. 按 **Create token**
6. **複製 token**（長得像 `hf_xxxxxxxxxxxxxxxxxxx`）— 只會顯示一次，請存好

### Step 3 — Accept pyannote EULA（兩個 model）

⚠️ **必須登入 HF 帳號**才能 accept。

1. 開 https://huggingface.co/pyannote/speaker-diarization-3.1
2. 滑到頁面中段、找到 **"You need to agree to share your contact information to access this model"** 區塊
3. 填好：Company / affiliation（隨便填如 `Personal`）+ 用途說明（如 `Personal podcast transcription, non-commercial`）
4. Click **「Agree and access repository」**
5. 看到頁面變成可下載狀態 → 完成

重複以上步驟對第二個 model：

6. 開 https://huggingface.co/pyannote/segmentation-3.0
7. 同樣 Accept terms → 看到可下載狀態

### Step 4 — Token 寫進 `.env`

開 `E:\nakama\.env`（沒有就從 `.env.example` 複製），找到（或新增）一行：

```bash
HUGGINGFACE_TOKEN=hf_xxxxxxxxxxxxxxxxxxx
```

把 Step 2 複製的 token 貼進去。

⚠️ **不要把 token 貼到對話 / commit / Slack**，per [feedback_no_secrets_in_chat](../../memory/claude/feedback_no_secrets_in_chat.md)。`.env` 已在 `.gitignore`、不會被 commit。

## 驗證

實作完成後（ADR-013 引擎 swap merge 後），跑：

```bash
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('HUGGINGFACE_TOKEN')
assert token and token.startswith('hf_'), 'token 未設定或格式錯'
print('Token OK')

from pyannote.audio import Pipeline
pipeline = Pipeline.from_pretrained('pyannote/speaker-diarization-3.1', use_auth_token=token)
print('pyannote pipeline load OK')
"
```

成功應該看到：
```
Token OK
pyannote pipeline load OK
```

如果 raise `RepositoryNotFoundError` 或 `403 Forbidden` → 回 Step 3 確認兩個 EULA 都 accept 了。

## Token 出問題了？

| 症狀 | 原因 | 修法 |
|---|---|---|
| `Cannot access gated repo` | EULA 沒 accept | 回 Step 3 |
| `Invalid user token` | token 過期 / revoke | https://huggingface.co/settings/tokens 重產，更新 .env |
| `Connection timeout` | 公司網路擋 huggingface.co | 換網路或設 proxy |

## 安全 / 退場

- Token 限 **read-only**，外洩風險低（只能 download public + gated model）
- 如果懷疑 token 外洩：https://huggingface.co/settings/tokens → 點 token 旁的 **「Revoke」**，重產新 token 並更新 `.env`
- 完全退場 nakama / 不再用 transcribe：刪掉 `.env` 該行 + 在 HF settings revoke token 即可，HF 帳號可保留

## 連結速查

- HF 註冊：https://huggingface.co/join
- HF Token：https://huggingface.co/settings/tokens
- pyannote/speaker-diarization-3.1：https://huggingface.co/pyannote/speaker-diarization-3.1
- pyannote/segmentation-3.0：https://huggingface.co/pyannote/segmentation-3.0
