# Meta／Carousel 多平台實際連線 Runbook

Stage anchor：**Stage 6 Publishing**。本文件把 Instagram Reels、Instagram
Carousel、Facebook Page Reels、Facebook Page 多圖貼文，以及 YouTube Community
Carousel 的第一次連線與實際驗收拆成可以逐步勾選的程序。

## 先讀：哪些動作會真的發文

| 標記 | 意義 | 外部副作用 |
|---|---|---|
| **READ ONLY** | 只讀取 Page／Instagram 身分 | 不發文 |
| **DRY RUN** | 只檢查本機檔案與列印計畫；命令中沒有 `--execute` | 不連 Meta、不測 R2、不發文 |
| **LIVE WRITE** | 命令中有 `--execute` | 會立刻在指定 Meta 帳號建立真實公開貼文 |
| **MANUAL WRITE** | 人在 YouTube 網頁完成 Community post | 會依網頁選擇立即發布或排程 |

Meta probe 沒有「private upload」模式，也沒有自動刪文。第一次 LIVE WRITE 請使用
你可接受出現測試貼文的自有 Page／Instagram Professional account，caption 前綴統一用
`[E2E TEST]`。測完後若要刪除，必須到平台 UI 手動刪除。

## 完成定義

完成本 runbook 時，以下項目必須全部成立：

- [ ] `credentials` 回傳預期的 Facebook Page ID／名稱與 Instagram ID／username。
- [ ] Instagram Reel probe 回傳 `external_id` 與可開啟的 `permalink`。
- [ ] Facebook Page Reel probe 回傳 `external_id` 與可開啟的 `permalink`。
- [ ] Instagram Carousel probe 回傳 `external_id` 與可開啟的 `permalink`。
- [ ] Facebook Page 多圖貼文 probe 回傳 `external_id` 與可開啟的 `permalink`。
- [ ] Bridge Short 頁三個 target 都不再顯示 `NOT EXECUTABLE`。
- [ ] YouTube Community 的 browser handoff、人工發文與 receipt 回填完成一次。
- [ ] 每個測試都留下時間、Graph API version、asset hash、external ID 與 permalink；紀錄中沒有 token。

任何一項失敗就停在該步，不要先按 Bridge 的「核准並上傳」。

## 0. 準備測試帳號、素材與終端機

### 0.1 帳號與素材 checklist

- [ ] 你擁有或可管理一個 Facebook Page，且具備建立內容的權限。
- [ ] 要連線的 Instagram 是 **Professional account（Business 或 Creator）**，不是個人帳號。
- [ ] Instagram Professional account 已連結到上面的 Facebook Page。
- [ ] 你的 Facebook 使用者已加入 Meta App role；若 App 仍在 Development mode，這一步不可少。
- [ ] 準備一支已取得權利、4–60 秒、9:16 的 MP4，先用 59 秒左右最保守。
- [ ] 準備 2–10 張已取得權利的 PNG/JPG，順序與最後貼文順序一致。
- [ ] 準備一個只放暫存素材的 Cloudflare R2 bucket，不得使用任何 backup bucket。

目前可拿來做 Short probe 的本機檔案範例：

```text
G:\Footages\20260721 鄭國威\highlights\partner-shorts\郝哥＿EP01財富自由的公式 Final.mp4
```

這不是鄭國威本集素材，因此 caption 必須明確寫 `[E2E TEST]`，不可當正式內容發布。

### 0.2 開啟正確 worktree

在新的 PowerShell 視窗執行：

```powershell
$repo = 'E:\nakama\worktrees\social-upload-inventory'
Set-Location -LiteralPath $repo
git branch --show-current
```

預期 branch：

```text
codex/social-upload-inventory
```

### 0.3 選定 Python

請使用能啟動目前 Bridge 的同一套 Python。先設定 `$python`，再驗證必要 dependency：

```powershell
$python = (Get-Command python).Source
& $python -c "import boto3, fastapi, pydantic, uvicorn, yaml; print('Python dependencies OK')"
```

再執行 Stage 6 發布 preflight。這個命令只檢查目前 Python 的 `boto3`、Meta 設定與
R2 staging 設定，不呼叫外部 API、不讀取 Release state，也不輸出 credential value：

```powershell
& $python scripts/publish_dispatch.py --preflight --platform instagram_reels
```

輸出必須包含 `"preflight": true` 與 `"network_calls": false`；若 `"ok": false`，
依各 component 的 `action` 修復後重跑，不可直接進入 `--execute`。

若 import 失敗，先建立這個 worktree 專用的 venv；`py -0p` 可列出已安裝 Python：

```powershell
py -0p
py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
```

如果安裝了多個 Python，也可以把 `py -m venv` 改成 `py -3.12 -m venv` 之類的明確版本；
專案要求 Python 3.11 以上。後續所有命令都沿用同一個 PowerShell 視窗與 `$python`。

## 1. 建立／確認 Meta App 與資產關係

本程式走的是 **Instagram API with Facebook Login**，不是新版的 Instagram Login
token 流程；它需要 Page access token、Facebook Page ID 與連到該 Page 的 Instagram
Professional account ID。

### 1.1 先確認 Instagram 與 Page 已連結

1. 登入 Facebook，切換到目標 Page。
2. 打開 Page 的設定，找到 **Linked accounts／已連結帳號**。
3. 在 Instagram 區塊連結目標 Professional account。
4. 回到 Meta Business Suite／Business Settings，確認同一個 Business Portfolio 能看到：
   - Facebook Page；
   - Instagram account；
   - 你目前登入的使用者。
5. 若 Instagram 還是 personal account，先在 Instagram App 內切換成 Business 或 Creator。

### 1.2 在目前的 `Nakama Usopp` App 加入 Instagram 發布權限

以下步驟以 2026-08-19 實際看到的 Meta Dashboard 介面為準。這個專案使用
**Instagram API with Facebook Login**；不要選預設的 **API setup with Instagram login**。

1. 開啟 [Meta for Developers Apps](https://developers.facebook.com/apps/)。
2. 在 App 清單點 **Nakama Usopp**。
3. 確認畫面左上方 App 名稱是 **Nakama Usopp**，右上方 **App Mode** 是
   **In development**。第一次連接自己管理的 Page／Instagram 時先不要切成 Live。
4. 在左側選單點 **Use cases**。
5. 找到卡片 **Manage messaging & content on Instagram**。
6. 在這張卡片右側點 **Customize**。
7. 若跳出 **Learn how to customize use cases** 對話框，點右上角 **Close**。
8. 進入 **Customize use case** 頁面後，點上方頁籤
   **API setup with Facebook login**。不要停在 **API setup with Instagram login**。
9. 確認頁面說明提到：Instagram professional account 已連到 Facebook Page，並透過
   Facebook Login for Business 授權。若不是這段說明，先停止，不要繼續按。
10. 往下找到 **1. Add required permissions**。
11. 在 **Manage content on Instagram** 區塊確認目前列出：
    - `instagram_basic`
    - `instagram_content_publishing`（Dashboard 目前顯示的名稱）
    - `pages_read_engagement`
    - `business_management`
    - `pages_show_list`
12. 點這個區塊內的 **Add required content permissions**。
13. 不要點下方 **Send messages on Instagram** 區塊的
    **Add required messaging permissions**；目前的短影片／Carousel 發布不需要收發私訊。
14. 這一輪也不要設定 **Configure webhooks**、不要進 **Complete app review**，也不要填
    Callback URL、Verify token 或 App Secret。

完成這 14 步的判定：Instagram 的內容發布權限已加入 App；尚未產生 access token，也尚未
發出任何貼文。

### 1.3 在同一個 App 加入 Facebook Page 發布權限

1. 在左側選單再次點 **Use cases**。
2. 找到卡片 **Manage everything on your Page**。
3. 在這張卡片右側點 **Customize**。
4. 進入頁面後，確認上方選到 **Manage Pages**，並停在
   **Permissions and features** 頁籤。
5. 按 `Ctrl+F`，輸入 `pages_manage_posts`，再按 Enter。
6. 找到名稱完全等於 **pages_manage_posts** 的那一列。說明文字會提到 App 可以建立、
   編輯與刪除 Page 貼文。
7. 在該列最右側點 **Add**。
8. 不要順手加入 Page Mentions、Live Video API、email、messaging、ads 或 comments 權限。

完成這 8 步的判定：`pages_manage_posts` 那一列不再顯示待加入的 **Add** 動作，或頁面明確
顯示該權限已加入。到這裡先停；下一節才會產生 token。

### 1.4 最終權限核對清單

之後在產生 User access token 時，至少要能取得以下能力：

```text
pages_show_list
pages_read_engagement
pages_manage_posts
publish_video
instagram_basic
instagram_content_publish
business_management
```

Dashboard 的按鈕目前把 Instagram 發布項目顯示為 `instagram_content_publishing`；官方 Graph
API token／文件可能仍顯示 canonical scope `instagram_content_publish`。不要在 Dashboard 手動猜名稱，
照第 1.2 節的 **Add required content permissions** 加入，再於 Graph API Explorer 實際核對 token
可取得的 scope。`pages_manage_posts` 是此專案寫入 Facebook Page Reel／多圖貼文所需的 Page
發布權限。

## 2. 取得 Page token、Page ID 與 IG user ID

這一節先用 Graph API Explorer 完成第一次連線。Explorer token 適合 supervised probe，
不應直接當長期 production credential。

### 2.1 產生 User access token

1. 開啟 [Graph API Explorer](https://developers.facebook.com/tools/explorer/)。
2. 看右側 **Meta App** 下拉選單；選擇 **Nakama Usopp**。不要使用 Meta 預設 App。
3. 看畫面上方 API path 左側的版本下拉選單。2026-08-19 實際介面顯示 `v26.0`；
   先保留這個版本，並把 `v26.0` 記為後續的 `META_GRAPH_API_VERSION`。
4. 在右側找到 **Permissions** 區塊，點 **Add a Permission**。
5. 在展開選單點 **Pages**，逐一加入：
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `publish_video`
6. 每加完一個權限，若選單自動收起，就再次點 **Add a Permission → Pages** 加下一個。
7. 再點 **Add a Permission → Other**，逐一加入：
   - `instagram_basic`
   - `business_management`
   - Instagram 發布權限：若 Explorer 顯示 `instagram_content_publish` 就選它；若只顯示
     `instagram_content_publishing` 就選目前介面提供的那個。不要自行輸入不存在的名稱。
8. 核對 **Permissions** 清單共有上述七項；不要加入 messaging、ads、comments 或 email。
9. 點右側 **User or Page** 下方的 **Get Token**。
10. 展開後會看到三個選項：
    - **Get User Access Token**
    - **Get App Token**
    - **Get Page Access Token**
11. 點 **Get User Access Token**。不要點另外兩個。
12. Facebook 授權視窗出現後，由目前 App administrator 的 Facebook 帳號完成授權：
    - 若先問是否以目前帳號繼續，確認帳號正確後點 **Continue／繼續**；
    - 若要求選 Business Portfolio，選擇管理目標 Page 與 Instagram 的 Portfolio；
    - 若要求選 Page，只選目標 Facebook Page；
    - 若要求選 Instagram account，只選連到該 Page 的目標 Professional account；
    - 權限確認頁保留第 1.4 節的七項，再點 **Save／Continue／儲存／繼續**。
13. 回到 Explorer 後，右側 **Access Token** 欄位出現內容就代表 User token 已產生。
14. 不要按 **Copy Token**，也不要把 token 貼到聊天、截圖、CLI argument 或 Git-tracked
    檔案。後續只透過 Explorer 執行查詢，再把 Page token 寫進本機 ignored `.env`。

### 2.2 查 Page 與連結的 Instagram identity

先用不含 token 的查詢核對 identity。在 Graph API Explorer 上方的 path 輸入框中，刪除原本的
`me?fields=id,name`，改成下列內容（輸入框左側已經有 `/`，不要再輸入前導斜線）：

```text
me/accounts?fields=name,id,tasks,instagram_business_account{id,username}
```

按 **Submit**。在回傳的 `data` 找到目標 Page，應該類似：

```json
{
  "name": "目標 Page 名稱",
  "id": "Facebook Page ID",
  "tasks": ["CREATE_CONTENT", "MANAGE"],
  "instagram_business_account": {
    "id": "Instagram Graph ID",
    "username": "instagram_username"
  }
}
```

這份回傳不含 secret，可以核對 Page 名稱、Page ID、Instagram username 與 Instagram Graph ID。
把值對應如下：

| Graph 回傳欄位 | Nakama 設定 |
|---|---|
| Explorer 選定的版本，例如 `vXX.X` | `META_GRAPH_API_VERSION` |
| Page record 的 `id` | `META_PAGE_ID` |
| `instagram_business_account.id` | `META_IG_USER_ID` |

停止條件：

- `data` 沒有目標 Page：檢查登入使用者的 Page access、App role 與 `pages_show_list`。
- record 沒有 `instagram_business_account`：回第 1.1 節重新連結 Page 與 Instagram。
- `tasks` 沒有建立／管理內容能力：先修正 Page／Business Portfolio 權限。

### 2.3 將 User token 切換成目標 Page token

identity 核對正確後才取得 Page token：

1. 在 Explorer 右側點 **User or Page → Get Token**。
2. 若下拉選單已直接列出目標 Page 名稱，點該 Page；否則點 **Get Page Access Token**，再選擇
   同一個目標 Page。
3. 若 Meta 再次要求授權，確認仍是同一個 Business Portfolio 與 Page，再點 **繼續**。
4. 回到 Explorer，確認 **User or Page** 顯示目標 Page 名稱，而不是 Facebook 使用者名稱。
5. 此時 **Access Token** 欄位已換成 Page token。只有在準備立刻寫入本機 ignored `.env` 時，
   才點 **Copy Token**。
6. 不要把 Page token 貼到聊天、截圖、Markdown、Bridge 表單或 shell command。

把 Page token 對應到 `META_PAGE_ACCESS_TOKEN`。此 Token 仍可能過期；第一次 E2E 先使用 Explorer
產生的 token，production 化時再改成明確的長效／system-user credential rotation 流程。

## 3. 把 Meta 設定寫進本機 `.env`

編輯主 repo 的 ignored 檔案 `E:\nakama\.env`，填入：

```dotenv
META_GRAPH_API_VERSION=vXX.X
META_PAGE_ID=<Facebook Page ID>
META_IG_USER_ID=<Instagram Graph ID>
META_PAGE_ACCESS_TOKEN=<Page access token>
```

規則：

- 版本必須含前導 `v`，例如 `v23.0`；程式不會猜最新版。
- 一個 key 一行，值不要包引號，也不要在值後面加 inline comment。
- `.env` 已被 `.gitignore` 排除；仍要先用 `git status --short` 確認它沒有被追蹤。
- token 絕不能放進 Bridge 表單、job JSON、Markdown 結果、shell command 或截圖。

## 4. 建立 Cloudflare R2 暫存 bucket

Instagram media container 與 Meta 多圖 API 需要 Meta server 能讀取圖片／影片 URL。
本專案會先把本機檔案上傳到 private R2 bucket，產生短效 presigned GET URL，完成或失敗後刪除物件。

### 4.1 建 bucket

1. 登入 [Cloudflare Dashboard](https://dash.cloudflare.com/)。
2. 進入 **Storage & databases → R2 → Overview**。
3. 按 **Create bucket**。
4. 建議名稱：`nakama-meta-staging`。
5. 維持 private；不需要開 public development URL 或 custom domain。
6. 記下 R2 Overview 顯示的 Account ID。

bucket 名稱不得含 `backup`，也不可等於 `NAKAMA_R2_BACKUP_BUCKET`；程式會 fail closed。

### 4.2 建最小權限 R2 token

1. 回到 **R2 Object Storage → Overview**。
2. 右下方 **Account Details** 區塊點 **Manage API Tokens**。
3. 在 **Account API Tokens** 區塊點 **Create Account API token**。這是持續運作的服務
   credential；不要選只綁個人任職狀態的 User API token。
4. **Token name** 輸入 `nakama-meta-staging-publisher`。
5. **Permissions** 選 **Object Read & Write**。不要選 Admin Read & Write。
6. **Specify bucket(s)** 選 **Apply to specific buckets only**。
7. 點出現的 **Select...** 下拉選單，只選 `nakama-meta-staging`；確認沒有
   `nakama-backup`、`xcloud-backup` 或其他 bucket。
8. **TTL** 保持 **Forever**；此 token 已由 bucket scope 限權，之後另排 rotation。若改成有期限，
   必須同時建立到期提醒，否則 unattended publish 會直接中斷。
9. **Client IP Address Filtering** 的 Include 與 Exclude 都留空；桌機與未來 worker 的出口 IP
   尚未固定，現在填入會造成合法上傳被擋。
10. 點 **Create Account API Token**。
11. 建立後立刻保存：
   - Access Key ID；
   - Secret Access Key。
12. Secret 只顯示一次；遺失就 revoke 後重建，不要到 log 找。

Read & Write 是必要的，因為 worker 會 `PutObject`、產生 `GetObject` presigned URL，最後
`DeleteObject`。

### 4.3 加 1 天 lifecycle backstop

1. 打開 `nakama-meta-staging` bucket。
2. 點上方 **Settings** 頁籤。
3. 在左側章節索引點 **Object Lifecycle Rules**，再點該區塊右側 **Add**。
4. 右側表單的 **Object lifecycle rule is enabled** 保持開啟。
5. **Rule name** 填 `delete-meta-stage-after-1-day`。
6. **Rule scope → prefix** 填 `meta-stage/`。
7. **Lifecycle action** 只勾 **Delete uploaded objects after**，數值填 `1`，單位保持
   **Days**。
8. 不要勾 **Abort incomplete multipart uploads after** 或
   **Transition objects to Infrequent Access storage class after**；bucket 已經有獨立的預設
   multipart abort rule。
9. 點 **Save changes**。
10. 回到規則列表，確認 `delete-meta-stage-after-1-day` 的 Prefix 是 `meta-stage/`、Action 是
    1 day 後刪除、Status 是 **Enabled**。

正常路徑會立即刪除；lifecycle 只處理斷電／process crash 留下的孤兒物件。

### 4.4 填入 `.env`

在 `E:\nakama\.env` 加入：

```dotenv
META_MEDIA_R2_ACCOUNT_ID=<Cloudflare Account ID>
META_MEDIA_R2_ACCESS_KEY_ID=<R2 Access Key ID>
META_MEDIA_R2_SECRET_ACCESS_KEY=<R2 Secret Access Key>
META_MEDIA_R2_BUCKET=nakama-meta-staging
META_MEDIA_R2_PRESIGNED_TTL_SECONDS=900
META_MEDIA_PUBLIC_BASE_URL=
```

TTL 接受 60–3600 秒，預設與建議值是 900。`META_MEDIA_PUBLIC_BASE_URL` 目前留空；現行
stager 一律使用 R2 S3 endpoint 的 presigned GET URL，不需要也不應公開 bucket。

## 5. 將 `.env` 安全載入目前 PowerShell process

重要：`meta_publish_probe.py` 與 `publish_dispatch.py` 目前不會自行讀取
`E:\nakama\.env`。填完檔案後，仍必須在啟動 probe／Bridge 的同一個 PowerShell process
載入以下指定 keys。

```powershell
$envFile = 'E:\nakama\.env'
$socialKeys = @(
  'META_GRAPH_API_VERSION',
  'META_PAGE_ID',
  'META_IG_USER_ID',
  'META_PAGE_ACCESS_TOKEN',
  'META_MEDIA_R2_ACCOUNT_ID',
  'META_MEDIA_R2_ACCESS_KEY_ID',
  'META_MEDIA_R2_SECRET_ACCESS_KEY',
  'META_MEDIA_R2_BUCKET',
  'META_MEDIA_R2_PRESIGNED_TTL_SECONDS',
  'META_MEDIA_PUBLIC_BASE_URL'
)

Get-Content -LiteralPath $envFile | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
    $key = $Matches[1]
    if ($socialKeys -contains $key) {
      [Environment]::SetEnvironmentVariable($key, $Matches[2], 'Process')
    }
  }
}

$required = $socialKeys | Where-Object { $_ -ne 'META_MEDIA_PUBLIC_BASE_URL' }
$missing = $required | Where-Object { [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, 'Process')) }
if ($missing) { throw "Missing social settings: $($missing -join ', ')" }
'Social publishing settings loaded; values intentionally hidden.'
```

預期只顯示最後一行成功訊息，不會輸出任何 secret。關閉這個 PowerShell 視窗後，process
環境就消失；不會改 Windows 的永久 user／machine environment。

## 6. Meta 身分只讀 probe

標記：**READ ONLY**。

```powershell
& $python scripts/meta_publish_probe.py credentials
```

預期輸出：

```json
{
  "page": {
    "id": "<META_PAGE_ID>",
    "name": "<預期 Page 名稱>"
  },
  "instagram": {
    "id": "<META_IG_USER_ID>",
    "username": "<預期 Instagram username>"
  }
}
```

逐字核對兩個 ID 與兩個名稱。只要不是預期帳號就停下來；不要為了讓 probe 通過而把
`.env` 改成陌生的回傳值。

## 7. 準備本次 probe 變數

在同一個 PowerShell 視窗設定本機素材路徑：

```powershell
$short = 'G:\Footages\20260721 鄭國威\highlights\partner-shorts\郝哥＿EP01財富自由的公式 Final.mp4'
$card1 = 'G:\請替換\carousel\01.png'
$card2 = 'G:\請替換\carousel\02.png'

Get-Item -LiteralPath $short, $card1, $card2 | Select-Object FullName, Length
```

若 `Get-Item` 有任何錯誤，先修正路徑。Carousel 需要至少兩張，Instagram 上限與目前
schema 都是十張；正式測試前確認編號排序、尺寸與肉眼內容。

## 8. 先跑四個 dry-run

標記：**DRY RUN**。以下命令沒有 `--execute`。

```powershell
& $python scripts/meta_publish_probe.py ig-reel `
  --file $short `
  --caption '[E2E TEST] Instagram Reel connection probe'

& $python scripts/meta_publish_probe.py fb-reel `
  --file $short `
  --caption '[E2E TEST] Facebook Reel connection probe'

& $python scripts/meta_publish_probe.py ig-carousel `
  --file $card1 --file $card2 `
  --caption '[E2E TEST] Instagram Carousel connection probe'

& $python scripts/meta_publish_probe.py fb-multi-photo `
  --file $card1 --file $card2 `
  --caption '[E2E TEST] Facebook multi-photo connection probe'
```

每個命令都應回傳：

```json
{
  "dry_run": true,
  "command": "...",
  "files": ["..."],
  "caption_chars": 42
}
```

這一步只證明 CLI 參數與本機檔案存在；**不證明 Meta token、R2 或 publishing
permission 可用**。

## 9. 逐一跑 live probes

以下每一小節都會真的發文。一次只跑一個；看到 permalink 並到平台肉眼確認後，才跑下一個。

### 9.1 Instagram Reel

標記：**LIVE WRITE**。這也是第一個真正測試 R2 upload／presigned URL 的步驟。

```powershell
& $python scripts/meta_publish_probe.py ig-reel `
  --file $short `
  --caption '[E2E TEST] Instagram Reel connection probe' `
  --execute
```

預期末尾：

```json
{
  "external_id": "<Instagram media ID>",
  "permalink": "https://www.instagram.com/..."
}
```

驗收：

- [ ] permalink 可開啟且帳號正確。
- [ ] 影片、聲音、直式畫面、caption 正確。
- [ ] R2 bucket 沒有殘留該次 `meta-stage/` object。

### 9.2 Facebook Page Reel

標記：**LIVE WRITE**。此路徑直接把本機 binary 傳到 Facebook，不使用 R2。

```powershell
& $python scripts/meta_publish_probe.py fb-reel `
  --file $short `
  --caption '[E2E TEST] Facebook Reel connection probe' `
  --execute
```

目前 production gate 固定限制 60 秒。官方 sample 也以 4–60 秒、9:16 為驗收規格；不要先拿
74 秒片測正式流程。若未來要調整上限，另開 supervised capability probe 與程式變更。

### 9.3 Instagram Carousel

標記：**LIVE WRITE**。

```powershell
& $python scripts/meta_publish_probe.py ig-carousel `
  --file $card1 --file $card2 `
  --caption '[E2E TEST] Instagram Carousel connection probe' `
  --execute
```

驗收圖片數量、順序、裁切與 caption。需要更多頁時重複 `--file`，最多十次。

### 9.4 Facebook Page 多圖貼文

標記：**LIVE WRITE**。

```powershell
& $python scripts/meta_publish_probe.py fb-multi-photo `
  --file $card1 --file $card2 `
  --caption '[E2E TEST] Facebook multi-photo connection probe' `
  --execute
```

驗收圖片全部落在**同一則** Page feed post，而不是多則單圖貼文。

### 9.5 Live probe 發生 error 時

不要立刻重跑。probe checkpoint 只在記憶體中，process 結束後不保存；另外，貼文可能已成功，
只是 permalink 查詢或 R2 cleanup 失敗。先到 Instagram／Facebook UI 檢查是否已有貼文，再決定：

- 平台完全沒有新貼文：修正原因後重跑。
- 平台已有新貼文：視為已產生副作用，記下 post ID／URL；不要重跑造成 duplicate。
- R2 留下 `meta-stage/` object：先確認不是執行中的 job，再從 R2 UI 刪除；1-day lifecycle 也是 backstop。

## 10. 讓 Bridge 繼承相同連線設定

Bridge 用 `sys.executable` 啟動背景 publisher，並繼承 Bridge process 的 environment。因此要在
**已完成第 5 節載入設定的同一個 PowerShell** 重啟 Bridge：

```powershell
Set-Location -LiteralPath $repo
& $python -m uvicorn thousand_sunny.app:app --host 127.0.0.1 --port 8013
```

若 8013 已被舊 Bridge 佔用，先回舊終端按 `Ctrl+C` 正常結束，再執行上面命令；不要同時跑兩個
Bridge instance 指向同一份 state DB。

打開：

```text
http://127.0.0.1:8013/bridge/publish/20260721%20%E9%84%AD%E5%9C%8B%E5%A8%81/partner-haoge-S01
```

重新整理後確認：

- YouTube、Instagram Reels、Facebook Page Reels 三個 target 都出現。
- Instagram／Facebook 不再顯示 `NOT EXECUTABLE · missing ...`。
- Instagram 的 connection check 同時要求四個 Meta settings 與四個 R2 settings；Facebook 只要求 Meta settings。

注意：畫面顯示 ready 只代表環境變數存在；真正連線能力以第 6、9 節的 probes 為準。

## 11. 從 Bridge 上傳一支 Short 到所有平台

標記：**LIVE WRITE**。

1. 確認 Title／Description 是正式要送出的文字；目前 Instagram／Facebook caption 取 target 的 description，空白時才退回 title。
2. 確認 YouTube 的 visibility／schedule 設定。
3. 按一次 **核准並上傳**。
4. Bridge 會為 Short 建立／核准三個 targets，背景 dispatcher 依序處理：
   - `youtube`；
   - `instagram_reels`；
   - `facebook_reels`。
5. 頁面會 poll target 狀態；每個平台應各自變成 `uploaded`／`published`，並留下 external ID／URL。
6. 一個平台失敗不會回滾已成功的平台。使用該平台旁的 retry，只重試 failed target；不要再按整批上傳。

若進度不動，先看頁面 target error；背景 stdout／stderr 寫在 runtime data dir 的
`upload_progress/<episode>_<cut>.log`（未設定 `NAKAMA_DATA_DIR` 時是本 worktree 的
`data/upload_progress/`）。不要把完整 log 貼到外部，先確認其中沒有意外 token。

## 12. Carousel：Meta 自動發布 + YouTube Community 人工 handoff

YouTube Data API 沒有建立 Community post 的 insert endpoint，因此 Carousel 的理想「一次按下」
在 YouTube 這一站仍必須停下來讓已登入瀏覽器／人完成，然後把 receipt 回填。Instagram 與
Facebook Page 則由 API 自動發布。

### 12.1 先對 job 做 dry-run

標記：**DRY RUN**。

```powershell
$job = 'G:\請替換\carousel\publish-job.json'
& $python scripts/publish_dispatch.py --carousel-job $job --dry-run
```

確認 job targets 包含：

```text
instagram
facebook_page
youtube_community
```

### 12.2 產生 YouTube browser handoff

執行：

```powershell
& $python scripts/publish_dispatch.py --carousel-job $job --execute
```

當 `youtube_community` 尚未有 receipt 時，這個命令會輸出：

```json
{
  "kind": "browser_handoff",
  "platform": "youtube_community",
  "state": "awaiting_receipt",
  "caption": "<貼文文字>",
  "asset_paths": ["<最多十張圖片>"],
  "target_url": "https://www.youtube.com/"
}
```

並以 exit code `2` 結束。這是預期的「等待人工 receipt」，不是 crash；此時尚未處理同 job
後面的 Meta targets。

### 12.3 在 YouTube 網頁完成 Community post

標記：**MANUAL WRITE**。

1. 用已登入正確頻道的瀏覽器開啟 handoff 的 `target_url`。
2. 按 Create／建立 → Create post／建立貼文。
3. 貼上 handoff 的完整 `caption`。
4. 依 `asset_paths` 順序上傳圖片；YouTube UI 最多接受十張。
5. 肉眼確認帳號、順序、裁切與文字。
6. 選立即 Post 或 Schedule。
7. 發布後打開該貼文，複製 permalink；不要只複製頻道首頁 URL。

### 12.4 回填 receipt，繼續 Meta targets

標記：**LIVE WRITE**。把 `<youtube-community-permalink>` 換成實際貼文 URL：

```powershell
& $python scripts/publish_dispatch.py `
  --carousel-job $job `
  --execute `
  --youtube-community-permalink '<youtube-community-permalink>'
```

dispatcher 會把 YouTube Community 標成 published，再處理尚未完成的 Instagram 與
Facebook Page target。成功後檢查 job JSON／輸出中的每個 platform result 都有 receipt ID；
Instagram／Facebook 應另有 permalink。

不要用頻道 URL、Studio 編輯頁 URL 或手寫字串冒充 receipt。沒有真實 post ID／permalink 時，
`youtube_community` 必須維持 pending。

## 13. 第一次連線後的 production hardening

Graph API Explorer 產生的 token 只適合第一次 supervised probe。正式讓 Bridge 長期使用前：

1. 在 Meta Business Settings 建 dedicated System User，或依你的 Business Portfolio 政策取得長效 User/Page token。
2. 只指派目標 Page、Instagram account 與必要的 create/manage content 權限。
3. 用新 token 重跑第 6 節 credential probe 與一輪受監督 live probes。
4. 在 secret manager／本機 `.env` 保存 token；記錄 owner、建立日、預計輪替日與撤銷程序。
5. 設定 token rotation；輪替後先 probe，再重啟 Bridge。
6. App 從只服務自有資產擴大到第三方帳號之前，完成 Meta App Review／Advanced Access，不可假設 Development mode 權限可服務外部使用者。

## 14. 故障排除

| 症狀 | 最可能原因 | 安全處理 |
|---|---|---|
| `missing required Meta settings` | `.env` 已填但沒有載入目前 process | 重跑第 5 節；不要輸出值 |
| Bridge 顯示 `NOT EXECUTABLE` | Bridge 是在載入 env 前啟動 | `Ctrl+C` 正常停止，從同一 PowerShell 重啟 |
| `credentials` 有 Page、沒有 IG | IG 不是 Professional，或沒連到該 Page | 回第 1.1 節修正資產關係 |
| `Application does not have permission` | token 少權限、App role 未接受、Page task 不足或需要 Advanced Access | 用 Graph Explorer 重新檢查 scopes／roles；不要擴加無關權限 |
| `Invalid OAuth access token` | token 過期、被 revoke 或 token/App 不匹配 | 重新產生 token，更新 `.env`，重跑 read-only probe |
| R2 `AccessDenied` | token 不是 Object Read & Write，或 scope 到別的 bucket | 重建／重 scope 專用 R2 token |
| R2 `SignatureDoesNotMatch` | Account ID、Access Key、Secret 或 endpoint jurisdiction 不匹配 | 對照同一 token confirmation page；EU jurisdiction bucket 目前不符合程式固定 endpoint，另開修正不要硬繞 |
| Instagram container timeout／ERROR | Meta 抓不到短效 URL、素材 codec／尺寸不合、處理逾時 | 先檢查 R2、影片規格與平台 UI；確認沒貼文後才重跑 |
| Facebook Reel 顯示 ineligible | 片長超過目前 60 秒 production gate | 換 60 秒內素材；不可偷偷裁切或重壓 |
| 命令報錯但平台已有貼文 | publish 已完成，後續 permalink／cleanup 失敗 | 記錄既有 post，禁止直接重跑以免 duplicate |
| Carousel `--execute` 回 exit code 2 | 正在等待 YouTube Community receipt | 照第 12.3、12.4 節，不是 failure retry |
| 只有一個平台 failed | 平台隔離正常運作 | 只 retry failed target；不得刪除或重建成功 siblings |

若 token 可能出現在 terminal、log、截圖或聊天，先在 Meta／Cloudflare revoke，再建立新 token；
不要先花時間確認「是不是有人看到」。

## 15. 驗收紀錄模板

每次 live probe 只記非敏感資訊：

```markdown
- tested_at: 2026-08-19T00:00:00+08:00
- operator: <name>
- graph_api_version: vXX.X
- platform: instagram_reels | facebook_reels | instagram | facebook_page | youtube_community
- account_name: <expected public account name>
- external_id: <post/media id>
- permalink: <public permalink>
- asset_path: <local path>
- asset_sha256: <sha256>
- duration_sec: <video only>
- result: passed | failed
- warning: <none or sanitized error; never token>
```

PowerShell 計算 SHA-256：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath $short, $card1, $card2
```

## 官方參考

- [Meta Instagram API official Postman collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api) — Professional account、Facebook Login 權限、Page token／IG ID discovery、Reels／Carousel publishing。
- [Meta Facebook API official Postman collection](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api) — Page token discovery 與 Facebook Reels upload／publish。
- [Meta Reels official sample repository](https://github.com/fbsamples/reels_publishing_apis) — Instagram／Facebook Reels sample implementations。
- [Cloudflare R2 S3 setup](https://developers.cloudflare.com/r2/get-started/s3/) — bucket、specific-bucket Object Read & Write token 與 S3 endpoint。
- [Cloudflare R2 presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/) — 短效 URL 行為與安全邊界。
- [Cloudflare R2 object lifecycle](https://developers.cloudflare.com/r2/buckets/object-lifecycles/) — `meta-stage/` crash artifact 的 expiry backstop。
- [YouTube Data API Activities resource](https://developers.google.com/youtube/v3/docs/activities/list) — Activities 僅有讀取方法，沒有 Community-post insert。
- [Create a YouTube post](https://support.google.com/youtube/answer/7124474) — browser/manual Community post 與圖片限制。
