# PR D ch2 ingest — 下個 session pickup handoff

> **建立日**：2026-04-27
> **本 session 完成的 advance work**：ch2 全 15 張 figure Sonnet 4.6 vision describe + 2 tables markdown 預讀全 cache 進 [`2026-04-27-ch2-ingest-cache.json`](../research/2026-04-27-ch2-ingest-cache.json)
> **目的**：給下個 session 啟動 PR D ch2 ingest 一個 zero-overhead pickup 點

---

## 0. 接手前先看的兩件事

1. **修修對 ch2 spot-check 的 verdict**（[`2026-04-27-ch2-vision-spot-check.md`](../research/2026-04-27-ch2-vision-spot-check.md)）：
   - fig-7-9 三點 dispute（panel 數 / step 1 起點 / actin 顏色）
   - fig-7-7 lateral arrow yes/no
2. **B1 拍板**（[`2026-04-26-ch1-v2-acceptance-checklist.md`](2026-04-26-ch1-v2-acceptance-checklist.md) §B1）：4 個 noop page schema_version=1 接受 by-design 還是改 ADR

兩件解了 → 下個 session 就動 PR D。

---

## 1. 本 session advance work（已 commit 進 repo）

### 1.1 ch2 vision describe cache（15 figs Sonnet 4.6）

[`docs/research/2026-04-27-ch2-ingest-cache.json`](../research/2026-04-27-ch2-ingest-cache.json) 結構：

```json
{
  "figures": {
    "fig-7-1": "<sonnet description>",
    ...
    "fig-7-15": "<sonnet description>"
  },
  "tables": {
    "tab-7-1": {"caption": "...", "tied_to_section": "...", "markdown_content": "..."},
    "tab-7-2": {...}
  },
  "meta": {"book_id": "biochemistry-sport-exercise-2024", "chapter_index": 2, "walker_nav_index": 7, ...}
}
```

**fig-7-9 / fig-7-10 描述狀態**：
- 兩張屬 cyclic sub-type
- ~~已 cache 一份 Sonnet 4.6 描述（先 run）~~ → **2026-04-27 evening update：兩張已 Opus 4.7 rerun + cache 替換完成**（見 §7）
- ~~若修修 verdict cyclic 升 Opus → 下個 session 重 run 這 2 張用 Opus~~ → **已執行**：基於 PR #204 4-model triangulate 強信號（Opus + Grok + Gemini 三家共識 vs Sonnet 1 家），driver 直接拍板 cyclic 升 Opus，無需修修人眼 verdict

### 1.2 PR D ingest driver inject pattern

ADR-011 §3.3 Step 4b 寫 idempotency 是看 chapter source page 已 exist 才 skip vision call。本 session 的 cache 是「ch2 chapter source 還沒 write 但 vision 已 done」的 pre-stage 狀態。

**下個 session 的 driver 動作**：

```python
import json
cache = json.loads(Path("docs/research/2026-04-27-ch2-ingest-cache.json").read_text(encoding="utf-8"))
fig_descriptions = cache["figures"]   # {"fig-7-1": "...", ...}
table_contents = cache["tables"]       # {"tab-7-1": {"markdown_content": "..."}, ...}

# 直接 inject 進 chapter-summary.md prompt 的 figures[] / tables[] 欄位
# 跳過 Step 4b vision call
```

這樣 driver 在 ch2 ingest 時不用重跑 11+ 張 vision call（除非 verdict 升 Opus）。

---

## 2. 5 MB image size limit 教訓（PR D 跑 ch3-ch11 必撞）

### 觀察

ch2 walker export 出來的 15 張 PNG 中 **4 張超過 5 MB**：

| ref | raw size | resized to (longest-side 1600px) |
|-----|----------|-----|
| fig-7-2 | 8.6 MB | 1463×1465, 1.3 MB |
| fig-7-3 | 12.5 MB | 1093×1600, 1.9 MB |
| fig-7-6 | 10.8 MB | 1326×1600, 1.4 MB |
| fig-7-8 | 10.6 MB | 1335×1600, 1.3 MB |

**Anthropic API base64 image limit = 5 MB** — 直接 send raw walker PNG 會 400 invalid_request_error。

### 解法（已驗證）

PIL LANCZOS resize 到 longest-side ≤ 1600px：

```python
from PIL import Image
from io import BytesIO
import base64

def resize_under_5mb(img_path, limit=5_000_000):
    img = Image.open(img_path)
    max_side = 1600
    while max_side > 200:
        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img2 = img.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
        else:
            img2 = img
        buf = BytesIO()
        img2.convert("RGB").save(buf, format="PNG", optimize=True)
        if buf.tell() < limit:
            return base64.b64encode(buf.getvalue()).decode()
        max_side -= 200
    raise ValueError(f"cannot resize {img_path} under {limit} bytes")
```

LANCZOS 對 anatomical illustration 細節保留好（spot-check 三家驗證 fig-7-1 anatomical 描述精度未受影響）。

### 應該加進 production code 的位置

下個 session **PR D 啟動時順手做**（不需要單獨開 PR）：

1. **`.claude/skills/textbook-ingest/SKILL.md` Step 4b** — 加 note：「Anthropic vision API 5 MB base64 limit；driver 必須 resize PNG > 5 MB 到 longest-side ≤ 1600px（PIL LANCZOS）before vision call」
2. **`.claude/skills/textbook-ingest/prompts/vision-describe.md`** — Inputs 表格加一行：「`{path}` resolves to potentially > 5 MB binary; driver responsibility to resize before base64 encode」
3. （Optional）**`shared/` 開新 helper `vision_image_prep.py`** — 給 ingest driver + 其他 vision call site 共用

---

## 3. PR D ch2 ingest 步驟（按順序）

### Step A. 處理修修 verdict（5 min）

| Verdict 內容 | 動作 |
|------|------|
| **A1**. fig-7-9 三點全 Sonnet 對 → cyclic 維持 Sonnet | 直接用 cache，無動作 |
| **A2**. fig-7-9 全 Sonnet 錯 → cyclic 升 Opus | rerun fig-7-9 + fig-7-10 with Opus 4.7（其他 13 張 cache 留用） |
| **A3**. Mixed → 加 prompt mitigation | rerun fig-7-9 + fig-7-10 with augmented prompt |
| **B1 接受 by-design** | 收尾 acceptance checklist 第 4 條 |
| **B1 改 ADR** | 開 PR 改 ADR-011 §4.3，etc. |

### Step B. ch2 driver reindex attachments（2 min）

PR C 經驗：walker export `ch7/`（nav#7），driver 改名 `ch2/`：

```bash
mv "F:/Shosho LifeOS/Attachments/Books/biochemistry-sport-exercise-2024/ch7" \
   "F:/Shosho LifeOS/Attachments/Books/biochemistry-sport-exercise-2024/ch2"

# 同時重新命名 figure / table refs 從 fig-7-N → fig-2-N
cd "F:/Shosho LifeOS/Attachments/Books/biochemistry-sport-exercise-2024/ch2"
for f in fig-7-*; do mv "$f" "${f/fig-7-/fig-2-}"; done
for f in tab-7-*; do mv "$f" "${f/tab-7-/tab-2-}"; done
```

cache JSON 也要同步 rename keys（fig-7-N → fig-2-N、tab-7-N → tab-2-N）。

### Step C. 寫 ch2 chapter source page

跑 `chapter-summary.md` prompt（v2，含 placeholder swap rules / Section concept maps / verbatim quotes）：

- Input：cache JSON 的 figures[] + tables[] + ch7.md raw text
- Output：`KB/Wiki/Sources/Books/biochemistry-sport-exercise-2024/ch2.md`

### Step D. concept extract + 4-action dispatch

跑 Robin v2 `extract_concepts.md` prompt + `kb_writer.upsert_concept_page()`：

預期：
- 0-3 create（新概念）
- 0-5 update_merge（既有 page lazy migrate v1 → v2）
- 0-2 update_conflict（發現新文獻分歧）
- 0-N noop（既有 page 已 v2 + 已含 ch2 mentioned_in）

### Step E. ch2 acceptance smoke check

- 7+ 改動 page schema_version=2、0 個 legacy `## 更新` block 殘留
- 0 個 `<<FIG:>>` / `<<TAB:>>` 占位符 leak（per F3 fix）
- chapter source body 圖片 Obsidian 顯示 OK

### Step F. PR D 後續 ch3-ch11

ch2 完成後可直接 batch 接 ch3-ch11：

- 每章重複 Step B-E
- 每章 walker output 已 cache 在 `/tmp/textbook-ingest-bse/chapters/ch{nav}.md`
- 每章 attachments 已 export 在 `Attachments/.../ch{nav}/` 對應 nav#index
- 但 — **每章還要走 vision describe loop**（除非也 pre-stage cache，see §4）
- 每章 ~150k token / 30-60 min

預期 ch3-ch11 也會撞 5 MB image limit（教科書 anatomical illustration 普遍偏大）— 走 §2 同 resize。

---

## 4. （Optional）給後續章節 pre-stage vision cache

如果想避免 ch3-ch11 batch ingest 時阻塞在 vision call：

```bash
# 同 batch_vision_ch2.py pattern，但 chapter index 改成 8/9/10/...
python tools/batch_vision_chapter.py --chapter 3 --output docs/research/2026-04-27-ch3-ingest-cache.json
```

但 — 這個 over-engineering。實際 ingest 流程同步跑 vision describe（每章 15-50 張，並行 6 worker，3-5 min）也 OK。

---

## 5. 本 session 成本實測（2026-04-27 evening）

| 項目 | Cost |
|------|------|
| ch2 spot-check 3 張（Sonnet sub-agent + Grok-4 + Gemini）| ~$0.5 |
| ch2 12 張非 cyclic batch（Anthropic API Sonnet 4.6）| ~$0.5 |
| ch2 fig-7-10 + 重跑 fig-7-1/7-7/7-9 統一格式 | ~$0.2 |
| **小計** | **~$1.2** |

15 張 figure ch2 vision describe 全 cache + spot-check + multi-model triangulate 報告 ~ $1.2。

PR D 預估 ch3-ch11（總 ~270 figures）若全 Sonnet API 跑：~$10-15；若 Opus 4.7 quota（Max 200 包含）：免費（但 wall time 較長）。

---

## 6. Post-handoff Opus rerun（2026-04-27 evening continuation）

PR #204 4-model triangulate 強信號（Opus + Grok + Gemini 三家共識對 Sonnet 1 家）已等同 verdict — driver 直接執行 cyclic 升 Opus，cache replace + SOP 文件化：

### 6.1 cache update（fig-7-9 + fig-7-10）

- **fig-7-9** ([`docs/research/2026-04-27-ch2-ingest-cache.json`](../research/2026-04-27-ch2-ingest-cache.json) `figures.fig-7-9`) — 從 Sonnet 4.6 (1593 chars) 換成 Opus 4.7 (1738 chars)：
  - ✅ 4 step（不是 5）
  - ✅ Step 1 = ATP hydrolysis（不是 crossbridge formation）
  - ✅ Actin = yellow beads + tropomyosin/troponin / Myosin heads = orange（不是 Sonnet 「actin orange-brown」誤標）
  - ✅ 順時針 + 中央 caption verbatim quote
  - 來源：本 session pre-stage 進 `/tmp/fig-7-9-opus.txt`，promote 進 cache JSON
- **fig-7-10** ([`docs/research/2026-04-27-ch2-ingest-cache.json`](../research/2026-04-27-ch2-ingest-cache.json) `figures.fig-7-10`) — 從 Sonnet 4.6 (1839 chars) 換成 Opus 4.7 in-session multimodal Read (2258 chars)：
  - ✅ "2 Sarcomeres" 標籤（Sonnet 漏）
  - ✅ I-band shaded **pink**（Sonnet 誤標 light blue）
  - ✅ 顯式提 H. E. Huxley & J. Hanson sliding filament theory（Sonnet 漏 attribution）
  - ✅ thin filament termination at M-line in panel (a)
  - 注：fig-7-10 嚴格說是 a/b/c 三態 composite（非典型 cyclic）但 handoff 標 cyclic 子類別、保守起見一起升 Opus
- **meta.figure_model + meta.note** 更新反映 13 figs Sonnet / 2 figs Opus 混用 + driver resize SOP

### 6.2 5 MB image resize SOP 文件化

handoff §2 教訓寫進 production code 路徑：

- [`SKILL.md` Step 2.a](../../.claude/skills/textbook-ingest/SKILL.md) — 加 "5 MB image size limit (driver responsibility)" subsection + PIL LANCZOS 1600px helper snippet
- [`vision-describe.md` Inputs 表](../../.claude/skills/textbook-ingest/prompts/vision-describe.md) — `{path}` 行下加 「Image size precondition」 blockquote 引用 SKILL.md helper

vision_image_prep.py shared helper（handoff §2 third bullet）暫不開 — ingest driver 是唯一 caller，inline 即可；之後若 Brook / SEO 也要 vision call 再 promote 到 shared/。

### 6.3 acceptance status — fig-7-7 ✅ resolved，B1 待修修 sign-off

- ~~fig-7-7 lateral arrow~~ → **2026-04-27 evening Opus 4.7 in-session resolution**：driver 直接看 fig-7-7.png 確認 left→right 水平箭頭存在，Sonnet + Opus 4.7（Anthropic 家族）兩家觀察一致，Grok / Gemini 是輕量 model 漏看 minor edge。**cache 描述保留無需修改**。完整紀錄見 [`spot-check.md`](../research/2026-04-27-ch2-vision-spot-check.md) § fig-7-7 Resolution
- **B1（4 個 noop page schema_version=1）** — driver 建議接受 by-design（已寫進 [`spot-check.md`](../research/2026-04-27-ch2-vision-spot-check.md) §「建議的修修 verdict」）；待修修 sign-off

B1 sign-off 後 PR D 啟動 Step B-F（attachments reindex / chapter source write / concept extract / acceptance / batch ch3-ch11，~5-8 hr wall time 寫進 vault）。

---

## 7. Reference

- ch2 spot-check + multi-model triangulate：[`2026-04-27-ch2-vision-spot-check.md`](../research/2026-04-27-ch2-vision-spot-check.md)（PR #201）
- ch1 acceptance checklist（B1 / F2 / 等）：[`2026-04-26-ch1-v2-acceptance-checklist.md`](2026-04-26-ch1-v2-acceptance-checklist.md)
- ADR-011：[`docs/decisions/ADR-011-textbook-ingest-v2.md`](../decisions/ADR-011-textbook-ingest-v2.md)
- SKILL.md：[`.claude/skills/textbook-ingest/SKILL.md`](../../.claude/skills/textbook-ingest/SKILL.md)
- vision-describe prompt：[`.claude/skills/textbook-ingest/prompts/vision-describe.md`](../../.claude/skills/textbook-ingest/prompts/vision-describe.md)
- Memory：[`project_ingest_v2_step3_in_flight_2026_04_26.md`](../../memory/claude/project_ingest_v2_step3_in_flight_2026_04_26.md)
