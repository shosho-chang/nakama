---
name: reference_standin_footage_library
description: stand-in 演員（Envato YuriArcursPeopleimages 的留鬍亞裔男）是所有 stock footage 的首要人選，本地素材庫在 E:\data\stock footage\asian man，索引在同資料夾 INDEX.md
metadata:
  type: reference
---

**任何一次 stock footage 搜尋，先看這位 stand-in 有沒有適合的**，再去看別人的。
修修 2026-09-14：

> 「`YuriArcursPeopleimages` 這個帳號底下的留鬍子亞洲男性當作 stock footage 的
> 首要人選，每次搜尋一定要看這個帳號底下有沒有適合的。」

他是修修的化身。同一張臉反覆出現，觀眾讀得出這是同一個頻道；不同男模特兒輪流
充當是視覺 bug。這條原本只管「b-roll 要演修修本人」的場合，2026-09-14 擴大到
**每一次搜尋**。

## 順序：先查本地，再去 Envato

| | 在哪 |
|---|---|
| 本地素材庫 | `E:\data\stock footage\asian man`（11 支、9.9 GB、ProRes） |
| 索引 | 同資料夾的 `INDEX.md`——情境關鍵詞 → 檔名 → 規格 |
| 判準與找片工法 | `.claude/skills/brook-dp/SKILL.md`〈修修本人情境的固定 stand-in〉 |

已經下載過的直接用，不必重抓、也不必再走一次授權流程。新抓他的素材一律放同一個
資料夾，並回頭補 `INDEX.md` 那張表。

現有情境：慶祝達標／專注工作／挫折卡住／遠距工作／講電話／閱讀夜晚／起床／
旅行出發／健行／兩人同行／健身重訓。

## 兩個會踩到的坑

**十一支裡有十支是 4096×2160 DCI 4K**（1.896:1，**不是** 16:9），放進 16:9 時間軸
會上下留黑邊。`scripts/build_resolve_project.py` 已把 timeline 設成 `centerCrop`；
走別條路上軌要自己處理。唯一的真 16:9 是讀電子書那支（3840×2160）。

**這個庫對短片沒有用**——全是橫式。短片線直式是硬條件、裁不出來，只能回 Envato 帶
`filter.orientation=Vertical` 重搜。見 [[feedback_dp_acquires_stock_without_asking]]
的採購路徑。

## 核臉要防兩個方向

skill 原本寫著「該帳號旗下有多位模特兒，每次都要核對臉」，防的是**誤收別人**。
2026-09-14 我犯了反方向的錯：把庫裡健身房那支標成「另一位模特兒」（捲髮、體格壯
得多、膚色較深、鬍型較粗），修修當場更正**那也是他**。四個理由都成立，但四個都是
情境造成的——高對比側光、充血的肌肉、汗濕往後撥的頭髮、用力時繃緊的表情。

**How to apply**：
- 只有「確定不是他」才排除；**不確定就問修修**，不要自己判死
- 排除之前至少看一段動態，不是幾張靜格
- 比對拿鼻型、眉骨、下顎線與鬍子連鬢角的走向；**不要拿體格、膚色、髮型比**，
  那三樣會隨情境、打光與拍攝期間改變
- 相關：[[feedback_human_verified_is_final]]（修修看過的就是定案）、
  [[reference_music_library]]（同一類：`E:\data` 底下的素材庫）
