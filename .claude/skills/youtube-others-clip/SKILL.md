---
name: youtube-others-clip
description: >
  YouTube 照稿影片第 11 類「他人影片引用」：到 YouTube 找片段（名人的不同訪談、動畫角色……），
  用字幕定位、提候選，修修在 Bridge 直接播 YouTube 標起訖與要不要，只下載核准的那一段，
  接起來左上加「影片來源」交件。Triggers: /youtube-others-clip、「找他的訪談片段」「截這個角色的片段」
  「挑片段」。不用在：stock footage（第 6 類）、關鍵字 montage（第 16 類，插入、保留原聲）。
---

# youtube-others-clip — 他人影片引用

**版本：v0.2（2026-11-17；版面定版：B 款橘底、窗寬 85%、進場動畫）**

修修 2026-11-17：「到 YouTube 上面去找影片並且截取，而且能夠標記是要從哪裡截取到哪裡」——
例：講到某個名人，找三段他接受不同訪問的片段；講到動畫角色（AI Agent 比喻成《忍者龜》的 Krang），截那個角色。
「我之前一直都是採用這樣子的方式來做，這個是在合理使用範圍以內」。規格 §1 第 11 類：**上時間軸靜音、下載保留原聲**。

## 流程

```
Director 寫需求 → DP 找片、提候選 → 修修在 Bridge 標起訖、要／不要 → DP 列下載清單給修修 → 下載 → 交件
```

1. **Director**（規劃表）：「給 DP 的指示」寫找誰／什麼、要幾段、畫面要什麼（例：「Nick Maggiulli 接受訪問的畫面，
   三段來自不同節目；挑他正在講話、臉清楚的地方」）。
2. **DP 找片**（`agents/brook/youtube_video/clips.py`，都不下載影片）：
   ```bash
   python -m agents.brook.youtube_video.clips search "Nick Maggiulli interview" -n 10
   python -m agents.brook.youtube_video.clips info <video_id>        # 頻道、章節、最多人重播的地方、有沒有字幕
   python -m agents.brook.youtube_video.clips locate <video_id> --keywords "wealth ladder,just keep buying" --target 4
   python -m agents.brook.youtube_video.clips peek <video_id> --at 252,600 -o <暫存資料夾>   # 每個秒數一張 360p 小圖
   python -m agents.brook.youtube_video.clips propose "<集數資料夾>" <row_id> <video_id> --start 270.25 --end 273.8 \
     --need 3 --note "字幕：「The wealth ladder is a new…」，應該是 Nick 在回答；畫面還沒確認"
   ```
   候選寫在 `<集數資料夾>/visual_sources/<row_id>.json`。
3. **修修在 Bridge**：規劃表那一列有「挑片段、標起訖」連結 → `/bridge/video-plan/<集>/clips/<row_id>`。
   每段一個 YouTube 播放器（直接在頁面上播，不下載）；「起點」「終點」按一下＝設成播放器目前的位置、
   ▶ 從起點播到終點、改「畫面上的來源」、標要／不要；也可以自己貼網址加一段。改了就存。
4. **下載**：先 `--dry-run` 把檔名、來源、大小列給修修，**修修在對話裡說可以才下載**（規則：下載要先問）。
   ```bash
   python -m agents.brook.youtube_video.clips download "<集數資料夾>" <row_id> --dry-run
   python -m agents.brook.youtube_video.clips download "<集數資料夾>" <row_id>
   ```
   只下標「要」的那一段（`--download-sections`，1080p 以下，連聲音）；檔名帶起訖，起訖改了會重新下載。
5. **交件**：`python -m agents.brook.youtube_video.visuals.others_clip "<集數資料夾>" --row <row_id>`（`youtube-dp` 也會跑）。

## DP 怎麼挑（判斷在這裡）

- **「不同訪問」＝不同節目／頻道**，不是同一集的不同時間點。同一場被好幾個頻道轉載的算一個。
- **頻道優先順序**：節目官方頻道 → 原上傳者 → 轉載（轉載的「來源」要寫原節目，不寫轉載頻道）。
- **字幕定位要帶別名**：自動字幕常聽錯名字（Krang 被聽成 Craig）——關鍵字寫原名、常見誤聽、和那句話的關鍵詞。
  沒有字幕的影片用 `info` 的「最多人重播的地方」和章節找，備註寫清楚是怎麼找的。
- **人物訪談挑他本人在講話的地方**：主持人介紹他時也會講到關鍵字，那段畫面通常是主持人——找他回答的那句。
- **長度**：一段 2–4 秒（修修過去成片的訪談快剪約 2 秒一鏡）；全部加起來不超過這一列蓋住的長度（超過 1 秒會擋）。
- **字幕對上不等於畫面對**：提案前一定用 `peek` 看那幾秒的畫面。《財富階梯》實測：Moneywise 那句是 Nick 在講，
  畫面卻是兩本書的封面圖；換到 10:00 才是他本人。看過的備註寫「看過畫面：…」，沒看過的寫「畫面還沒確認」。
- **一段＝一個畫面，不能閃**（修修：「三個不同的訪談，其實只要三個不同的畫面就好了，如果畫面切得太快，
  反而會讓觀眾覺得奇怪」）：來源影片常在講話中途切鏡頭（Rational Reminder 4:30.25 起頭 0.3 秒是四格畫面）。
  `download` 下載完會自動檢查（`shot_cuts`：畫面變化分數 > 0.25），有不到 1.5 秒的鏡頭就印 ⚠ 和建議起訖
  （片段裡最長的一鏡）；出片時也會擋。照建議在 Bridge 改起訖、重新下載。訪談片段最好整段就是一鏡。
- **純音訊 podcast 不要選**：`info` 的畫面規格低 fps（例：6 fps）、方形 1080×1080，多半只有封面圖（ChooseFI 那支就是）。
- **畫面上的來源**：預設頻道名；節目名比頻道名好認時改節目名（例：The Rational Reminder Podcast）。

## 交件長什麼樣（修修 2026-11-17 定版）

修修：「在底下做一個底圖，然後將引用的畫面縮小……背後可能就是我們品牌的 pattern，然後一個框框可以讓它做類似這樣線條繞圈的簡單動畫」。
三款底圖（冷灰碎片圍一圈／橘色滿版兩角碎片／冷灰兩角碎片）選 **B 橘色滿版＋兩角冷灰碎片**；
窗寬三個大小（80／85／90%）選 **85%**（1632×918，「影片來源＋窗」整塊垂直置中）。

- 片段照候選清單順序接，縮小放進窗；比例不同補黑邊，不裁畫面。29.97、ProRes 4444 帶 alpha＋原聲。
- 進場（修修：「橘色底先出現，然後灰色的框框跑進來，畫面再出現」）：橘底斜著刷進來 → 兩角冷灰碎片
  （手冊 p24 原稿，原樣不重畫）從角外飛進來 → 窗從中線拉開、片段從 0.5 秒開始播 → 白色框線畫一圈 →
  一段黑線繞著框跑（約 2.6 秒一圈）。退場反過來：片段最後 0.25 秒窗關上 → 碎片飛出去 → 橘底刷掉。
- 總長＝0.5 秒進場＋片段＋0.3 秒退場；超過這一列蓋住的長度 1 秒以上就擋。
- 框的左上方「影片來源」：黑色字牌＋白色字牌黑字的來源，每段開始時換字。
- 鋪時間軸只放畫面（`layout.py` mediaType 1）→ 時間軸上是靜音的。
- 元件：`video/compositions/others_clip/compositions/framed.html`（底圖、碎片、框線、來源；窗是透明的），
  `agents/brook/youtube_video/visuals/others_clip.py` 把片段放到窗後面（兩邊的窗位置是同一條算式，要一起改）。

## 已知限制

- yt-dlp 要新版＋`yt-dlp-ejs`（YouTube 的驗證解題）：2026-11-17 修修同意後本機升到 2026.10.26＋ejs 0.8.0；
  舊版讀不到《忍者龜》官方頻道（「This video is not available」）。
- 影像下載優先 HLS（m3u8）：https 的 DASH 影像會被擋 403（ChooseFI 那支）。
- 短時間抓很多支字幕會被 YouTube 限流（HTTP 429）：抓不到字幕就先用 `peek`＋章節、最多人重播的地方，過一陣子再試。
- 起訖改過會重新下載，舊檔留在 `visual_sources/clips/`（檔名帶起訖），還沒有自動清。
