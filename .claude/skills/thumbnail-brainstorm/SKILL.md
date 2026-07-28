---
name: thumbnail-brainstorm
description: >
  封面 brainstorm 手冊（ADR-054 D8/D9）。packaging 的 sequential 第二棒：吃
  title-brainstorm 寫進 packages.json 的 Top 標題（各帶 archetype_id）→ playbook
  joint pairing 為前 3 條各配一個封面 idea → 視覺配方 routing（podcast／
  youtube_host／youtube_book）→ 來賓 cutout 走窗口化 funnel + vision 挑表情 →
  render 3 張 16:9 PNG → 回填 packages.json 成 3 個 package。Triggers:
  /thumbnail-brainstorm、「配封面」、「出 package」。創意判斷（配對、表情、
  大字）在本手冊；schema／render／去背／檔名慣例歸 shared/ 與 scripts/，
  本 skill 只呼叫、不重新發明。
---

# thumbnail-brainstorm — 封面 brainstorm 手冊

**版本：v1.1（2026-07-28，封面設計系統 v1 接入 — 對標 Modern Wisdom 普查；
規格見 `docs/thumbnail-design-system.md`；v1.0 = ADR-054 D8/D9 首落地）**

你是 packaging 的**封面棒**：標題已定（Top 5 進 packages.json），你為前 3 條
各配一個封面、render 成 PNG、綁成 3 個 package。你**不改標題**：覺得某條標題
配不出封面（抽象到無畫面、與所有 S/A 級 thumb archetype 相斥）→ 記 run log
把該條換成 rank 4/5 遞補，不是改寫標題文字。

## 紅線

1. **契約歸 deterministic 工具**：packages.json schema（`shared/schemas/packaging.py`）、
   cutout 檔名（`cutout_filename`）、render（`thumbnail_worker`）、去背
   （hyperframes）只能經 PR 改；缺口記 run log Remaining，不即席發明欄位。
2. **機位驗證 fail 不許繞過**：`guest_cutout.py sample` 報 ValueError（expected
   speaker 窗內占比 < 0.6）時，唯一合法動作是查修 `highlights/tighten/director.json`
   的 `cams` 對應後重跑。**禁止**換 `--expected-speaker` 數字硬過——那正是
   ADR-054 A8③ 要堵的「穩定抽到錯的人且不報錯」。
3. **D/F 級 archetype 禁用**；C 級需在 run log 寫明 hedge 理由。封閉來源：
   playbook compact index，不即席發明 archetype。
4. **檔名 ASCII**：PNG 一律 `pkg-{cut_id}-{n}.png`；guest cutout 一律
   `cutout_filename("guest", i, emotion)` 產（帶 emotion — A8④）。
5. **設計系統紀律**（`docs/thumbnail-design-system.md` 硬紀律節）：一張圖一個
   idea、色彩角色鎖定（橘只當 highlight/框/bolt）、頭高 ≥45%、視線朝內、
   零裝飾、100px 自檢。diversity 軸 = **配方（N1/N2/N3）× 表情 × 大字**。
   真人不 AI（memory 鐵律）；N2 prop 卡供給 = Envato → 公版 → 圖表重繪。
6. **每集寫 run log packaging 節**（配對理由、表情選擇、否決、Remaining）。

## 輸入

- `<packaging_dir>/packages.json` — 目標 cut 的 `titles`（Top 5，各帶
  `archetype_id`）、`visual_recipe`、`cut_id`。packages 未滿 3 的中間態合法
  （本 skill 就是來補滿的）。
- podcast 配方另需：`highlights/winners.json`（段落時間窗）、
  `highlights/tighten/director.json`（機位 `cams` 對應）、episode 資料夾
  （`subs/words.json` + `Audio/` 分軌 + `normalized.wav`）。

缺輸入 → 停下報明缺哪個檔，不腦補。

## Step 0 — 配方檢查（fail loud 先行）

```bash
python -c "import sys; sys.path.insert(0, '.'); \
  from importlib.util import spec_from_file_location, module_from_spec; \
  spec = spec_from_file_location('rs', '.claude/skills/thumbnail-brainstorm/scripts/render_still.py'); \
  m = module_from_spec(spec); spec.loader.exec_module(m); \
  m.ensure_recipe_supported('<visual_recipe>')"
```

`youtube_book` 在此立刻 NotImplementedError（附參考圖庫指引）— 不做半套。

## Step 1 — joint pairing 配對（每條標題一個封面 idea）

注入 compact playbook（~1.5K tokens，勿讀 91KB 原檔）：

```bash
python -c "from shared.thumbnail_playbook import format_playbook_index_for_prompt as f; print(f())"
```

對 titles rank 1–3 逐條：

1. 取該條 `archetype_id`（T-A*）→ 查 joint pairings 有無 `title_archetype_id`
   相符的 JP-*（index 已附 `why_they_pair` 佐證）。有 → 用它的 thumb archetype。
2. 沒有相符 JP → 依 thumb archetype 的 when_to_use/brand-fit 自配一個
   （S/A 優先，D/F 禁用），run log 記「無 JP 佐證，自配理由」。
3. 三個封面在**表情／大字／裝飾**軸上拉開（例：驚訝大特寫 vs 解釋+圖示 vs
   認真+數字大字）。同 archetype 出現兩次即違反 diversity — 換掉一個。
4. 每個 idea 定案三件事：`thumb_archetype_id`、**大字**（3–7 字 hook 短語，
   不是標題全文——標題已在 YouTube 標題欄，封面大字補不同資訊）、
   **表情**（`prompts/thumbnail/emotions.yml` 七值之一，host 與 guest 各一）。

## Step 2 — 視覺配方 routing（修修 2026-07-28 裁：兩人都從 raw file 抽）

| visual_recipe | host | guest |
|---|---|---|
| `podcast` | Step 3 抽格（`--role host`，修修機位） | Step 3 抽格（`--role guest`） |
| `youtube_host` | 預建庫 `pick_youtube_host(表情, vault)`（非訪談影片才用） | 無 |
| `youtube_book` | Step 0 已 fail loud | — |

## Step 3 — cutout 抽格（兩個角色，僅 podcast）

1. 窗口 = `winners.json` 該 cut 的 start/end；機位檔 + `expected_speaker`
   = `director.json` 的 `cams` 對應。host 反應臉常在來賓說話窗 → `--role host`
   會跳過 speaker-dominance 檢查（機位正確性由 cams 設定把關）。
2. 抽格（guest 機位交叉驗證內建 — 見紅線 2）：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py sample \
  --episode-dir "<episode>" --cam-video <機位.mp4> --role <host|guest> \
  --window <t0> <t1> [--expected-speaker <n>] \
  --out-dir "<packaging_dir>/<role>_frames/<cut_id>"
```

3. **vision 挑格（subagent，一次批量，兩個角色各一次）**：候選已按清晰度排序
   （= motion blur 淘汰）。任務 =「依 emotions.yml 為 Step 1 定案的表情各挑
   最佳一格；臉被手/麥擋、閉眼、動態模糊、側轉 >45° 淘汰；**回報視線方向**
   （放左緣的人要看畫面右，反之亦然）」。一個 subagent 看完全部候選。
4. 去背落檔（BiRefNet + 統一調色內建）：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py finalize \
  --frame <picked.png> --emotion <表情> --ep-slug <ascii-slug> --index <i> \
  --role <host|guest> [--crop x0 y0 x1 y1] [--flip]
```

- `--crop`：比例框，去麥臂/筆電/衣字；**臉要貼哪個邊，裁框就收到臉那一側的邊**
  （邊緣錨定原理與經驗值見設計系統「素材管線」節）。
- `--flip`：視線不朝內時翻轉（實拍像素、非 AI；**衣服有字時禁用**，run log 註記
  給修修否決權）。
- render 後**必看成品**：cutout 裁切/位置不對就調 crop 重出 — 一次迭代是常態。

## Step 4 — render 3 張 PNG（設計系統 v1）

依 Step 1 的配對選配方（N1 `thumbnail_full`／N2 `thumbnail_reaction`／N3
`thumbnail_topic` — 選擇邏輯見設計系統），寫 spec JSON 後：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/render_still.py \
  --composition <thumbnail_full|thumbnail_reaction|thumbnail_topic> \
  --spec <spec.json> --out "<packaging_dir>/pkg-<cut_id>-<n>.png"
```

spec 的 variables 見各 composition 檔頭註解（title_lines/highlight_text/
guest_name/guest_title/…）。大字 = 2–6 字/行 ≤3 行、**恰好一個** highlight 詞、
句點結尾。render 失敗（ThumbnailRenderError）→ 看保留的 variables JSON 與
stderr，修完重跑；連續失敗 2 次停下報修修，不降級成無封面。
（v0 路徑 `--recipe` 保留給舊 compositions，新集勿用。）

## Step 5 — 回填 + 驗證 + 雙落點

寫 `specs.json`（3 筆：title_rank／thumbnail 本地路徑／thumb_archetype_id／
joint_pairing_id／host_cutout／guest_cutout），然後：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/attach_packages.py \
  --packaging-dir "<packaging_dir>" --cut-id <cut_id> \
  --episode-slug <ascii-slug> --specs specs.json
```

script 會：PNG 複製進 vault `Attachments/packaging/<slug>/`、cutout 路徑轉
vault-relative、整檔過 `PackagesFileV1` 驗證（失敗即不落任何一份）、
working set 與 vault 雙寫（ADR-054 D10）。驗證錯誤讀訊息修 specs，不改 schema。

## Run log 格式（append 於 `<ep>/run_log.md`）

```markdown
## Packaging 封面節 — thumbnail-brainstorm v1.0
- L1 rank1「...」T-A8 → JP-7（T-V2 tight face crop）；guest 表情=驚訝
  （frame 0034，淘汰 0021 手擋臉）；大字「大腦會說謊」
- L1 rank3 無 JP 佐證 → 自配 T-V4（解釋語境、A 級）；理由：...
- Remaining：youtube_book 參考圖庫未建
```

## 每集教訓寫回手冊

E2E 每跑完一集（gate approve 過），可固化的教訓 **append 進本節並 bump
版本號**（經 PR）。

### 教訓紀錄

（v1.0 尚無——第一集跑完後開始累積。）
