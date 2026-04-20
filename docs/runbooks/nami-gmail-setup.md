# Nami Gmail 設定 Runbook

## 概覽

Nami 的 Gmail 整合使用與 Google Calendar **不同**的 Google 帳號，但共用同一個 Google Cloud Project 的 OAuth credentials。

- Calendar 帳號：修修的個人 Google 帳號（行事曆用）
- Gmail 帳號：修修的 Gmail 帳號（可以是同一個或不同帳號）
- credentials 檔：`data/google_oauth_credentials.json`（共用）
- Calendar token：`data/google_calendar_token.json`
- Gmail token：`data/google_gmail_token.json`（**新建，不覆蓋 Calendar token**）

---

## Phase 1：修修手動（本機）

### Step 1：在 Google Cloud Project 啟用 Gmail API 並新增 Scope

這個 Step 要做兩件事：**啟用 Gmail API** + **在 OAuth 同意畫面加上 Gmail 的授權範圍（scope）**。

#### 1-A：啟用 Gmail API

1. 前往 https://console.cloud.google.com/
2. 右上角確認目前選的 Project 是當初設 Calendar 的那個（Project 名稱顯示在頁面頂端的下拉選單裡）
3. 左邊選單點「**APIs & Services**」→「**Library**」
4. 搜尋框輸入 `Gmail API`，點進去
5. 點「**Enable**」藍色按鈕

   > 如果按鈕顯示「Manage」代表已啟用，可跳過。

#### 1-B：在 OAuth 同意畫面加上 Gmail Scope

Google Cloud Console 已換新版介面（叫「Google Auth Platform」）。左側選單長這樣：
Overview / Branding / Audience / **Clients** / **Data Access** / Verification Center / Settings

Scope 的設定在 **Data Access**，不在 Overview 頁。

1. 點左邊「**Data Access**」
2. 頁面上方會有「**Add or Remove Scopes**」按鈕，點它
3. 右側滑出一個面板。在搜尋框輸入 `gmail`
4. 從清單中找到並勾選這兩個：
   - `https://www.googleapis.com/auth/gmail.modify`（讀信、草稿、標記已讀）
   - `https://www.googleapis.com/auth/gmail.send`（發送）

   > 如果搜尋沒出現，滾到面板底部找「Manually add scopes」，把這兩行直接貼進去（一行一個 URL），點「Add to table」。

5. 點「**Update**」（右下角）
6. 頁面回到 Data Access，確認剛才兩個 scope 出現在清單裡，點「**Save**」

#### 1-C：不要填「Approval required」的審核表

加完 scope 後，Google 會顯示「Approval required」要你填理由。**直接忽略，不需要填。**

那個表格是給要「公開上架給所有 Google 用戶」的 app 用的。Nami 是個人工具，不需要 Google 官方審核。

#### 1-D：Publishing status 確認保持「In production」

左邊點「**Audience**」，確認 Publishing status 顯示「**In production**」。

- **In production** → refresh token 長效有效（不會 7 天後過期）✓
- **Testing** → refresh token 7 天過期，需要一直重跑授權腳本 ✗

「Your app requires verification」的黃色警告**直接忽略**。那是 Google 要求公開上架 App 才需要驗證，個人工具不需要。

#### 1-E：確認 credentials.json 不需要重新下載

不需要。加完 scope 後，`data/google_oauth_credentials.json` 不變。
下次跑 auth script 時，瀏覽器會根據新的 scope 重新要求授權。

> **關於「This app hasn't been verified」警告**：跑 auth script 時瀏覽器會出現紅色警告頁。這是正常的 —— 點「**Advanced**」→「**Go to [app name] (unsafe)**」繼續就好。

### Step 2：執行 OAuth consent（本機）

```bash
cd f:/nakama
python scripts/google_gmail_auth.py
```

**重要**：瀏覽器開啟後，**請登入 Gmail 那個帳號**（不是 Calendar 的帳號，如果它們不同的話）。

授權完成後會寫入 `data/google_gmail_token.json`。

### Step 3：驗證 token（本機）

```python
# 在專案根目錄下執行
python -c "
from shared.google_gmail import _get_service
s = _get_service()
p = s.users().getProfile(userId='me').execute()
print('Gmail account:', p['emailAddress'])
print('Messages total:', p.get('messagesTotal', '?'))
"
```

應印出 Gmail 帳號的 email 地址，確認是對的帳號。

---

## Phase 2：部署到 VPS

### Step 1：scp token 到 VPS

```bash
scp data/google_gmail_token.json nakama-vps:/home/nakama/data/
ssh nakama-vps 'chmod 600 /home/nakama/data/google_gmail_token.json'
```

（credentials.json 若已在 VPS 上則不需要再傳）

### Step 2：重啟 nakama-gateway

```bash
ssh nakama-vps 'systemctl restart nakama-gateway'
```

---

## Phase 3：E2E 測試（Slack）

在 Slack 用 @Nami 測試以下情境：

```
@Nami 我 Gmail 有什麼新信
```
→ 應回傳未讀信件列表

```
@Nami 幫我看第 1 封的完整內容
```
→ 應回傳信件 From / Subject / Body

```
@Nami 幫我拒絕這封，簡短說目前行程很滿無法配合
```
→ Nami 應詢問是否要婉拒 → 完成後貼出草稿 → 等你說「發」

---

## Token 過期 / 授權失效

如果 Nami 回覆「Gmail 授權失效」，在本機重跑 consent：

```bash
python scripts/google_gmail_auth.py
# 確認用 Gmail 帳號登入
scp data/google_gmail_token.json nakama-vps:/home/nakama/data/
ssh nakama-vps 'systemctl restart nakama-gateway'
```

---

## 常見問題

**Q：授權過後 refresh_token 是空的**
A：在 Google Cloud Console → OAuth Consent Screen → Audience，確認 app 狀態是 Published（或 Testing 且你的帳號在 Test users 名單內）。然後再跑一次 auth script（`access_type=offline + prompt=consent` 會強制重新授權）。

**Q：`[ERROR] 找不到 OAuth credentials`**
A：確認 `data/google_oauth_credentials.json` 存在。如果是首次設定，從 Cloud Console 下載 OAuth 2.0 Client ID JSON 並放到 `data/` 下。

**Q：Calendar 可以用但 Gmail 說沒有權限**
A：Gmail API 需要在 Cloud Project 中單獨啟用（見 Step 1），且 consent screen 的 scope 要包含 gmail.modify + gmail.send。重跑 auth script 以更新授權範圍。
