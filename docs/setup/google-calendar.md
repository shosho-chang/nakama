# Google Calendar OAuth 設定指南

Nami 的 Google Calendar 整合需要一次性的 OAuth 授權設定。全程約 15-20 分鐘。

## 前提

- 有一個 Google 帳號（Nami 會操作這個帳號的 primary calendar）
- 這個帳號可以登入 [Google Cloud Console](https://console.cloud.google.com/)

---

## Step 1：建立 / 選擇 Google Cloud Project

1. 打開 https://console.cloud.google.com/
2. 頂部專案選單 → **NEW PROJECT**
   - Project name：`nakama`（或任何你喜歡的名字）
   - Location：No organization（個人帳號不用選）
3. 建立後，確認右上角切到這個 project

> 💡 如果已經有其他 project，可以沿用，不一定要新建。

---

> 📌 **UI 變動注意**：Google 2026 年把 OAuth 相關設定從「APIs & Services」搬到新的 **Google Auth Platform**。以下路徑對照：
> - 舊：APIs & Services → OAuth consent screen → 新：**Google Auth Platform → Branding / Audience / Data Access**
> - 舊：APIs & Services → Credentials → 新：**Google Auth Platform → Clients**
> - 啟用 API（Step 2）還是在「APIs & Services → Library」

## Step 2：啟用 Google Calendar API

1. 左側選單 → **APIs & Services** → **Library**
2. 搜尋 `Google Calendar API`
3. 點進去 → **ENABLE**

---

## Step 3：設定 OAuth Consent Screen（Google Auth Platform）

從左側選單進 **Google Auth Platform**（或搜尋 "auth platform"）。

### 3.1 Branding（品牌資訊）
1. 左側 → **Branding**
2. App name：`Nakama Nami`（或任何名字）
3. User support email：你的 gmail
4. Developer contact：你的 gmail
5. App logo / domain：可跳過
6. → SAVE

### 3.2 Audience（受眾 + 發佈狀態）
1. 左側 → **Audience**
2. User type：**External**（個人 gmail 只能選這個）
3. **⚠️ 關鍵：Publishing status**
   - 如果顯示 **Testing** → 點 **PUBLISH APP**
   - 彈窗警告「需要驗證」 → **CONFIRM**
   - 狀態變 **In production**
4. **不發佈的話，refresh token 會每 7 天過期，Nami 會突然失效。**

> 驗證（verification）只有在用 sensitive scope 且給**其他使用者**用時才強制需要。
> 自己給自己用的單使用者 app 可以維持 Unverified，consent 時會看到警告畫面，點「Advanced → Go to Nakama Nami (unsafe)」繼續即可。

### 3.3 Data Access（scope）
1. 左側 → **Data Access**
2. → **ADD OR REMOVE SCOPES**
3. 搜尋 `calendar.events`
4. 勾選 `https://www.googleapis.com/auth/calendar.events`（**只勾這一個**，不要勾 full calendar scope）
5. → UPDATE → SAVE

---

## Step 4：建立 OAuth 2.0 Client ID

1. 左側選單 → **Google Auth Platform** → **Clients**
2. 頂部 **+ CREATE CLIENT**
3. Application type：**Desktop app**
4. Name：`Nakama Nami Desktop`
5. → CREATE
6. 彈窗顯示 Client ID + Client Secret → **DOWNLOAD JSON**
7. 把下載的 json 存到 repo：`data/google_oauth_credentials.json`

```bash
# 從下載資料夾搬過來（路徑看你的環境調）
mv ~/Downloads/client_secret_*.json f:/nakama/data/google_oauth_credentials.json
```

> `data/` 已在 .gitignore，不會被 commit。

---

## Step 5：本機執行 Consent 流程

Nami 的授權腳本會開瀏覽器讓你同意，然後把 token 存到 `data/google_calendar_token.json`。

```bash
# 在 repo 根目錄
python scripts/google_calendar_auth.py
```

流程：
1. 終端機印出一個網址
2. 瀏覽器自動打開（或手動貼網址）
3. 選你剛才在 Cloud Console 設定的那個 gmail 帳號
4. 出現「Google 尚未驗證此 app」警告 → **Advanced** → **Go to Nakama Nami (unsafe)**
5. 勾選「查看、編輯、建立和刪除你的 calendar」→ **Continue**
6. 終端機看到 `✅ Token saved to data/google_calendar_token.json`

---

## Step 6：搬 Token 到 VPS

```bash
# 從本機 scp 到 VPS
scp data/google_calendar_token.json nakama-vps:/home/nakama/data/google_calendar_token.json

# VPS 端確認權限（只有 nakama user 可讀）
ssh nakama-vps "chmod 600 /home/nakama/data/google_calendar_token.json"
```

> `google_oauth_credentials.json` 也要 scp 過去（refresh token 流程需要 client secret）。

```bash
scp data/google_oauth_credentials.json nakama-vps:/home/nakama/data/google_oauth_credentials.json
ssh nakama-vps "chmod 600 /home/nakama/data/google_oauth_credentials.json"
```

---

## Step 7：驗證

VPS 端：

```bash
ssh nakama-vps
cd /home/nakama
python -c "from shared.google_calendar import list_events; \
  from datetime import datetime, timedelta, timezone; \
  now = datetime.now(timezone.utc); \
  events = list_events(time_min=now, time_max=now+timedelta(days=7)); \
  print(f'✅ Fetched {len(events)} events')"
```

如果印出 `✅ Fetched N events` 就 OK。

---

## 故障排除

### `invalid_grant: Token has been expired or revoked`
- 代表 refresh token 已失效。常見原因：
  - OAuth app 還在 Testing 模式（見 Step 3 最後一步）
  - 使用者在 Google 帳號 → Security → Third-party apps 手動撤銷了授權
  - 超過 6 個月沒用
- **解法**：重跑 `python scripts/google_calendar_auth.py`，新 token 會覆寫舊的

### `403: Request had insufficient authentication scopes`
- Scope 不對或授權時沒勾 calendar.events
- 解法：Step 3 重檢查 scope，Step 5 重跑 consent

### `FileNotFoundError: data/google_oauth_credentials.json`
- Step 4 的 JSON 沒放對位置。檢查路徑

### VPS 突然打 Calendar API 失敗
- 最可能：token rotate 後沒寫回檔案（file lock 問題）
- 看 `shared.google_calendar` 的 log，找 refresh 相關錯誤
- 緊急解法：本機重跑 Step 5 + 重 scp（Step 6）

---

## 安全注意

- `google_oauth_credentials.json` 和 `google_calendar_token.json` **都是 secret**，不要 commit、不要貼到 chat、不要放雲端
- 萬一洩漏：Google Cloud Console → Credentials → 刪掉該 OAuth Client → 重建
- Token 拿到 == 完整 calendar.events 權限。謹慎保管
