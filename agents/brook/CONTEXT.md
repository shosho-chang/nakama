# Brook

Brook 負責把已存在的 source material 轉換成可供各 channel 製作的內容規格；上游已提供 editorial direction 時遵守它，未提供時依各 flow 的契約完成內容取捨。Podcast Carousel 是其中一條 Stage 5 repurpose flow。

## Language

### Podcast Carousel

**Podcast Carousel Template**:
Podcast episode carousel 的純視覺版型，固定頁型骨架為封面、單一主要 Hook、有序內容重點、來賓金句、CTA；模板內文字全部是 placeholder，不是任何正式產出的內容來源。
_Avoid_: Carousel copy、文案模板

**Podcast Carousel Copy Spec**:
從乾淨逐字稿萃取、可回指逐字稿 evidence，並符合 Podcast Carousel 固定頁型的結構化文案。
_Avoid_: Template copy、placeholder replacement、IGRenderer output

**Carousel Editorial Direction**:
選填的 `social_brief.md`，用來約束 **Podcast Carousel Copy Spec** 的內容取捨與編排方向；缺少時 Copy Skill 必須能從乾淨逐字稿自行建立 **Episode Highlight Arc**。
_Avoid_: Required brief、template theme

**Transcript Evidence**:
支撐一段 carousel 文案的乾淨逐字稿原文與定位資訊，完整保存且不因版面需求而改寫。
_Avoid_: Display quote、template copy

**Display Copy**:
實際放上 carousel 的文字，可以為版面縮寫、順句或摘要 **Transcript Evidence**，但不能改變原意、事實或說話者歸屬。
_Avoid_: Verbatim quote、raw transcript

**Social Editorial Voice**:
社群經理以訪談內容為依據，為 IG 受眾撰寫封面、Hook、內容重點與 CTA 時使用的節目／品牌編輯聲音。可以自然地對受眾使用「你」，但不得讓編輯文案看起來像主持人或來賓未曾說過的第一人稱原話。
_Avoid_: Transcript voice、unattributed first-person quote、guest impersonation

**Internal Editorial Panel**:
在唯一主版本進入 render 前，由多個互不知情的 reviewer agent 獨立檢查同一份 **Podcast Carousel Copy Spec**，再由主 agent 對照逐字稿查證、收斂並修訂的內部 editorial review。Panel 結果是產生最終主版本的決策訊號，不是人類 approval，也不直接把最高分版本自動送出。
_Avoid_: Multi-version approval、agent majority vote、unverified critique

**IG Audience Lens**:
檢查 Hook、閱讀節奏、理解成本，以及看完後是否會想聽完整節目的 panel reviewer 視角。
_Avoid_: Generic copy score、platform compliance check

**Episode Editorial Lens**:
檢查 Carousel 是否涵蓋整集最值得傳播的重點、敘事弧是否成立，以及是否漏掉關鍵主題的 panel reviewer 視角。
_Avoid_: Exhaustive transcript coverage、chronological summary check

**Brand and Evidence Lens**:
檢查是否改變原意、錯置說話者、創造不存在的因果或讓來賓被斷章取義的 panel reviewer 視角。
_Avoid_: Legal approval、majority vote、verbatim-only enforcement

**Episode Highlight Arc**:
一份 Podcast Carousel 對整集訪談中多個吸引人重點的編排，不限於單一主題，也不要求逐段摘要全部內容。
_Avoid_: Single-topic post、exhaustive transcript summary

**Channel-native Asset**:
為特定 channel 的受眾、閱讀節奏與語言重新組織後，可以脫離原始媒介獨立成立並發布的內容資產。
_Avoid_: Transcript excerpt、episode summary、promotional by-product

**Hook Copy**:
P2 主要 Hook 的語意資料，由一個不需訪談背景也能理解的受眾問題 `question`、該問題內唯一一段完整原字串 `emphasis`，以及說明繼續滑會得到什麼的 `bridge` 組成。`bridge` 描述 episode payoff，不是固定來賓履歷欄。
_Avoid_: Guest bio block、template placeholder、context-dependent question

**Quote Layout Variant**:
Podcast Carousel 倒數第二頁的 deterministic layout 選型：A 為單一來賓金句，B 為主持人提問加上直接相連的來賓回答。
_Avoid_: Random quote layout、unpaired Q&A、renderer-selected variant

**Content Sequence**:
主要 Hook 與來賓金句之間的有序內容重點；每一點都必須承接主要 Hook 的 episode payoff。
_Avoid_: Re-hook、unordered takeaways

**Publish Compatibility**:
依總頁數標示 Podcast Carousel 的發布能力：10 頁內為 `api_compatible`，11–20 頁為 `manual_only`。
_Avoid_: Content quality score、approval status

**Carousel Review Gate**:
在圖片 render 後，以 Web App 同時檢查成圖、Display Copy 與 Transcript Evidence，並完成整份 carousel approval 的唯一正式 gate。
_Avoid_: Copy-only approval、raw JSON approval、publish approval

**Carousel Review Grid**:
在同一頁一次顯示全部 carousel 卡片的總覽式 review layout；桌機以每列 5 張為基準，每張卡片下方只保留最小 feedback 空間。非空 feedback 表示該頁要修改，空白表示該頁無修改要求。
_Avoid_: Single-card viewer、swipe preview、full-size editor per card

**Carousel Review Detail**:
從 **Carousel Review Grid** 點擊單一卡片後開啟的側邊 panel，顯示放大成圖、結構化 Display Copy、逐字稿原文、說話者與時間位置；用於仔細閱讀與 evidence 查核，不取代總覽的 decision controls。
_Avoid_: Always-expanded evidence、second approval surface、standalone page viewer

**Carousel Correction Feedback**:
Review Gate 中 revision-bound、page-bound 的非空修改指示；只有非空欄位會進入 correction job。空白不是單卡核准，只表示該頁沒有修改要求。
_Avoid_: Per-card radio decision、blank-as-approval、free-floating comment

**Carousel Correction Job**:
由一個以上 **Carousel Correction Feedback** 建立的 agent-neutral 工作交接，狀態為 `queued → claimed → in_progress → completed|failed`；保存來源 revision、manifest hash、claim、progress 與結果 revision。
_Avoid_: Provider-specific task、implicit executor、direct JSON mutation

**Carousel Correction Executor**:
實際承接整輪 E2E 修訂的當前 Codex 或 Claude Code agent；它 claim job、回報 progress、產生新 revision，並把 IG Audience、Episode Editorial、Brand and Evidence 分派給三個獨立 subagents。
_Avoid_: External LLM API、hidden provider、reviewer-as-executor

**Podcast Carousel Flow**:
以 episode folder 為工作單位、只產 Podcast Carousel 的獨立 Stage 5 flow；不綁定 Blog／FB fan-out，其他 Social Post 等此 flow 穩定後再 fork。
_Avoid_: Multi-channel repurpose run、generic social renderer

**Podcast Carousel Package**:
位於 `<episode>/ig-carousel/` 的獨立可發布 asset package，與 `<episode>/packaging/` 同層，保存 Copy Spec、render revisions、review feedback 與 approved images。
_Avoid_: Packaging subfolder、repurpose run directory

**Podcast Carousel Render**:
把 **Podcast Carousel Copy Spec**、episode cutouts 與 **Podcast Carousel Template** 組合成 1080×1080 cross-platform square 圖片序列。
_Avoid_: Copy extraction、IG copy generation

**Fit Escalation**:
renderer 對單一文字區塊逐步縮小字級與行距後，只有降到 pilot 可讀範圍以下才能完整放入時，仍完整 render 但將該頁標為 `needs_review` 的狀態。
_Avoid_: Copy rejection by length、silent clipping、renderer rewrite

**Template Snapshot**:
從設計系統取得、以內容 hash 識別並封存在 **Podcast Carousel Package** 的當次 render 模板唯讀副本；authoring 仍只在設計系統進行，同一 revision 使用同一 snapshot。
_Avoid_: Second authoring source、mutable latest template、untracked template copy

## Relationships

- 一個 Podcast episode 的每個 Carousel revision 產生一份 **Podcast Carousel Copy Spec**；同一時間只有一份主版本進入 review。
- Copy Skill 可以在內部探索多個內容角度，但每次執行只交付並 render 一份主版本；未採用方向不成為待審 Carousel artifact。
- **Podcast Carousel Flow** 的 canonical entrypoint 是獨立 `/ig-cards` Skill，不是舊 `Line1Extractor → Blog / FB / IGRenderer` multi-channel fan-out。
- **Podcast Carousel Flow** 只負責 Podcast Carousel；書本、身心健康與其他 Social Post 不在 v1 共用同一模板或 schema，待此 flow 跑順後再 fork。
- **Podcast Carousel Flow** 的所有產物寫入 episode-local **Podcast Carousel Package**；它是 `packaging/` 的 sibling，不是其子資料夾。
- **Podcast Carousel Package** 可以讀取 `packaging/cutouts/` 等製作素材，但 `packaging/` 只保存支援製作的封面、cutout 等素材，不擁有可獨立發布的 Carousel asset。
- 乾淨逐字稿是 **Podcast Carousel Copy Spec** 的必要輸入；**Carousel Editorial Direction** 是選填輸入。
- 有 **Carousel Editorial Direction** 時 Copy Skill 必須遵守其內容取捨與編排方向；沒有時 Copy Skill 自行建立 **Episode Highlight Arc**，不能因此停止。
- 一份 **Podcast Carousel Copy Spec** 表達一條 **Episode Highlight Arc**，可以涵蓋訪談中的多個主要主題。
- Podcast Carousel 是 IG context 裡的 **Channel-native Asset**；它不是依逐字稿順序排列的摘要，也不只是 Podcast 宣傳附圖。
- 封面、Hook、內容重點與 CTA 使用 **Social Editorial Voice**；來賓金句保留來賓聲音並清楚標示歸屬。若內容頁需要使用主持人或來賓的第一人稱觀點，必須標示說話者並連回 **Transcript Evidence**。
- 一份 **Podcast Carousel Copy Spec** 包含一個封面、一個主要 Hook、一個以上有序內容重點、一個來賓金句與一個 CTA；v1 不接受第二個 Hook 或 Re-hook。
- 內容重點數量由 **Episode Highlight Arc** 的實際內容完整度決定；4 點或 6 點只是常見案例，不是 schema、template 或 editorial hard limit。不得為了湊數加入弱點，也不得為了壓頁數刪除足以代表整集的重要主題。
- **Content Sequence** 保存每張中段內容重點的順序；不得用第二個 Hook、Re-hook 或裝飾性 divider 打斷序列。
- 主要 Hook 使用 **Hook Copy**，並為後續所有 points 建立清楚一致的 episode payoff；renderer 只負責 fit 與視覺套用，不替 Copy Skill補寫欄位。
- 一份 **Podcast Carousel Copy Spec** 最多 20 頁；超過 20 頁必須重新篩選內容。
- 主版本進入 **Podcast Carousel Render** 前必須通過 **Internal Editorial Panel**：reviewer agent 獨立盲審，主 agent 逐項查證其發現是否能由 Copy Spec、Transcript Evidence 與乾淨逐字稿支持，再收斂成修訂。
- **Internal Editorial Panel** 固定包含三個互不替代的 reviewer：**IG Audience Lens**、**Episode Editorial Lens**、**Brand and Evidence Lens**；三者獨立輸出 findings，不以平均分或多數決消除少數 lens 的有效問題。
- **Internal Editorial Panel** 不新增 copy-only HITL gate；只有 render 後的 **Carousel Review Gate** 是人類正式 approval。
- **Publish Compatibility** 只描述發布路徑，不得為了維持 `api_compatible` 而刪除重要內容；11–20 頁可以完整 render，但標為 `manual_only`。
- **Podcast Carousel Copy Spec** 產生後直接進入 **Podcast Carousel Render**，不先停在獨立的純文字 approval。
- **Carousel Review Gate** 必須是與長 Highlight、短影片 review 同類的 Web App；同一 surface 顯示成圖、Display Copy 與 Transcript Evidence。
- **Carousel Review Gate** 與 finished-cut review 共用 Thousand Sunny process、登入與 feedback pattern，但使用獨立的 `/bridge/ig-cards/{episode_slug}` route 與 `nakama.podcast_carousel_review_manifest.v1` page-based contract。
- Carousel page 不是 cut 或 timeline component；Carousel manifest 不得沿用 finished-cut manifest 的 cut、subtitle、timeline lane 語言。
- **Carousel Review Gate** 的主要 surface 是 **Carousel Review Grid**，不是逐張切換的 viewer；桌機 10 張應能用兩列完成總覽。
- **Carousel Review Grid** 不把逐字稿 evidence 常駐展開；點擊卡片開啟 **Carousel Review Detail** 查核完整內容，總覽卡片仍只保留成圖與 1–3 行 feedback。
- 每頁 feedback 非空即為 **Carousel Correction Feedback**；空白只表示該頁無修改要求。Review Gate 不使用 per-card `approved`／`needs_changes` radio。
- 送出任何非空 feedback 時，只把非空項目寫入一個 revision-bound **Carousel Correction Job**；這個動作不核准 carousel。
- 所有 feedback 欄位皆空時才能執行整份 Approve。Approve 只記錄目前 revision 的人類核准，不修改 artifact、不建立 correction job，也不發布。
- **Carousel Correction Job** 是 agent-neutral contract；目前 E2E agent 以 `codex` 或 `claude_code` claim，並以 claim token 回報單調 progress 與完成後的新 revision。若沒有 executor 在線，job 保持 `queued`。
- **Carousel Correction Executor** 不得呼叫外部 LLM API 或隱性 provider；同一輪三個 panel lens 仍由互不知情的 subagents 執行，E2E agent 負責 evidence 查證、修訂、render 與逐頁視覺 QA。
- Feedback 綁定 source revision、manifest hash、page identity 與 artifact hash；來源已漂移時不得 claim 或套用。局部 feedback 可以只修改受影響頁面，但若牽涉 Hook、順序或 **Episode Highlight Arc**，必須同步修訂所有受影響頁並重新跑 panel。
- 修改 Display Copy 或圖片後必須建立更新的 revision；correction job 只有在新 revision 完成 panel、render 與逐頁 QA 後才能標記 `completed`。
- 文案長度沒有 hard limit；**Podcast Carousel Render** 對 headline、body、quote 等 content box 分別 fit，不因其中一區過長連帶縮小其他區塊，也不得截字、省略或自行改寫 **Display Copy**。
- 文字必須低於 pilot 可讀範圍才能 fit 時採 **Fit Escalation**，不中止整份 render；最低可讀值以鄭國威 episode 的實際成圖與 review 結果校準，再決定是否凍結成 design token。
- 每個核心文案區塊同時保存 **Display Copy** 與一個以上 **Transcript Evidence**。
- 內容重點的唯一 emphasis 必須是 headline 的完整原字串；body 保持純文字，不套第二種強調語法。
- 來賓金句與 B 版主持人提問也使用 **Display Copy**：可以縮寫或順句，但必須保留原意並連回原始 **Transcript Evidence**。
- **Quote Layout Variant** 預設依 episode number 輪替：奇數集 A、偶數集 B，允許人工 override；同一集重跑不得隨機換版。B 找不到能可靠配對的主持人問題時降級為 A，不得補寫假的問題。
- 一則來賓金句只能濃縮自一個連續問答區段；主持人短暫插話不切斷同一回答，但不同時間的段落不得拼成一句金句。
- 需要兩個不連續逐字稿區段才能成立的內容只能作為內容重點，不得包裝成單一來賓金句。
- **Content Sequence** 可以依 IG 閱讀節奏重新排列，不必遵守逐字稿時間順序；但不得顛倒同一故事的前因後果，也不得創造原訪談不存在的因果關係。
- **Podcast Carousel Render** 只消費 **Podcast Carousel Copy Spec** 與 episode assets，不讀取模板 placeholder 作為內容。
- **Podcast Carousel Template** 可以獨立演進視覺設計，但不能改變或生成 **Podcast Carousel Copy Spec** 的事實內容。
- CTA 只顯示 episode topic 與 Apple Podcasts、Spotify、YouTube 三個固定平台入口，不顯示留言互動行。
- 設計系統是 **Podcast Carousel Template** 的唯一 authoring source；每個 revision 以 **Template Snapshot** 與 hash 固定實際 render 版本，模板升級必須建立新 revision。

## Example dialogue

> **Dev:**「模板裡已經有一句來賓金句，可以先拿來 render 嗎？」
> **Domain expert:**「不行。模板文字全部是 placeholder；先從乾淨逐字稿產生有 evidence 的 **Podcast Carousel Copy Spec**，再交給 **Podcast Carousel Render**。」

> **Dev:**「來賓原本回答太長，可以縮成一句上卡嗎？」
> **Domain expert:**「可以；原文留在 **Transcript Evidence**，卡片使用不改變原意的 **Display Copy**。」

## Flagged ambiguities

- 舊程式使用 `IGRenderer` 同時指涉文案生成；本 context 將文案萃取稱為 **Podcast Carousel Copy Spec**，將生圖階段稱為 **Podcast Carousel Render**，避免兩者混淆。
- 「一份 carousel 只講一個主軸」已否決；Podcast Carousel 應以單一 Hook 帶出整集訪談中多個吸引人重點，不使用 Re-hook 重整節奏。
- 舊 ADR-014 把 IG 放在 Blog／FB 同一個 RepurposeEngine fan-out；Podcast Carousel v1 已決定採獨立 **Podcast Carousel Flow**，不再以該 fan-out 作 canonical entrypoint。
