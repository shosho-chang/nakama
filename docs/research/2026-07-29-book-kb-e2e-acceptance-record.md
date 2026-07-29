# 《財富階梯》Book KB E2E 驗收紀錄（2026-06-04 實跑，2026-07-29 歸檔）

worktree 清理時發現 `E:\nakama-book-acceptance`（branch `accept/book-kb-e2e`）內的
`.kb-preview/_INDEX.md` 是整本書 promotion pipeline 端到端驗收的**唯一一份紀錄**，
main 的 docs/ 與 memory/ 皆無對應物。修修 2026-07-29 裁決：抄進 docs/research 存檔，
worktree 收掉。

## 工作落地對照

| 工作 | 落地狀況 |
|---|---|
| monolingual evidence track（ADR-024）+ N519 LLM ClaimExtractor + real chapter titles | ✅ 已由 PR #833（`2cfd07a`）進 main |
| chapter_title 補欄位等 5 檔 tracked 修改 | ✅ 與 main 內容逐字相同（路徑已搬到 `agents/robin/promotion/`），worktree 副本捨棄 |
| cross-domain KB flag（`NAKAMA_BOOK_DIGEST_KB_HITS`）+ book reader「同步到 KB」按鈕 | ❌ PR #834 CLOSED（browser 驗收沒跑完就擱置，非否決）。**工作保全於 `origin/accept/book-kb-e2e` 的 `4561b32`**，要重啟從該 commit 重開 PR |
| 完整 `.kb-preview` 產物（22 章合併 31KB + COMMIT/SKIP 分類夾） | 歸檔於 `E:\data\AgentOutput\20260729-book-kb-e2e-acceptance\` |

---

## 驗收預覽原文（`.kb-preview/_INDEX.md` 逐字保存）

# 財富階梯 — Promotion 驗收預覽

manifest: mfst_ebook:財富階梯_2026-06-04T00:14:21Z
成本: 27 calls / 142,231 in / 17,753 out / $0.6930 (~NT$22) / 333.9s

## 會寫進 KB 的 22 章 (COMMIT_這些會寫進KB/)

| ch | conf | 證據 | KB 路徑 | 章節 claim |
|---|---|---|---|---|
| ch-3 | 0.95 | 3 | KB/Wiki/Sources/財富階梯/ch-3.md | 各界推薦: 威廉．伯恩斯坦認為馬朱利能將理財寫得引人入勝，並描繪出財富階梯穩步向上的路徑 |
| ch-4 | 0.90 | 3 | KB/Wiki/Sources/財富階梯/ch-4.md | 推薦序　了解財富階梯，做出明智的財務決定／綠角: 《財富階梯》一書根據個人淨資產總值，劃分出六階 |
| ch-5 | 0.92 | 3 | KB/Wiki/Sources/財富階梯/ch-5.md | 前言: 財富的累積需要正確的策略架構，而非僅僅努力工作或遵循一般理財建議 |
| ch-7 | 0.92 | 3 | KB/Wiki/Sources/財富階梯/ch-7.md | 第1章｜順著財富階梯向上而增加消費: 克麗奧佩托拉聲稱她能夠一餐花掉1千萬古羅馬幣（約為今天的2 |
| ch-8 | 0.90 | 3 | KB/Wiki/Sources/財富階梯/ch-8.md | 第2章｜順著財富階梯向上而增加收入: 松下幸之助出生於1894年，15歲在電器業找到第一份工作， |
| ch-9 | 0.85 | 3 | KB/Wiki/Sources/財富階梯/ch-9.md | 第3章｜順著財富階梯向上而增加投資: 2020年3月23日至2021年11月4日期間，伊隆．馬斯 |
| ch-11 | 0.95 | 3 | KB/Wiki/Sources/財富階梯/ch-11.md | 財富階梯: 作者撰寫第2部的目的是根據讀者目前的財務狀況來幫助踏上累積財富之旅 |
| ch-12 | 0.92 | 3 | KB/Wiki/Sources/財富階梯/ch-12.md | 第4章｜第1階（< 1萬美元）: 麥克．布萊克在2020年新冠肺炎疫情期間進行「百萬美元回歸」實 |
| ch-13 | 0.92 | 4 | KB/Wiki/Sources/財富階梯/ch-13.md | 第5章｜第2階（1萬～10萬美元）: 拉斯洛．波爾加認為只要從小接受密集訓練，任何人都能成為天才 |
| ch-14 | 0.95 | 3 | KB/Wiki/Sources/財富階梯/ch-14.md | 第6章｜第3階（10萬～100萬美元）: 每隔175年，木星、土星、天王星及海王星會排列成幾乎同 |
| ch-15 | 0.95 | 3 | KB/Wiki/Sources/財富階梯/ch-15.md | 第7章｜第4階（100萬～1,000萬美元）: 1982年諾魯是全世界人均最富有的國家，因硫酸鹽 |
| ch-16 | 0.92 | 3 | KB/Wiki/Sources/財富階梯/ch-16.md | 第8章｜第5階（1,000萬～1億美元）: 許多身價千萬、億，甚至數十億美元的富豪，有一個相同的 |
| ch-17 | 0.90 | 3 | KB/Wiki/Sources/財富階梯/ch-17.md | 第9章｜第6階（1億美元以上）: 阿佛烈·諾貝爾因1888年報紙錯誤刊登他的訃聞而決心改變世人記 |
| ch-18 | 0.95 | 3 | KB/Wiki/Sources/財富階梯/ch-18.md | 第10章｜攀爬財富階梯得花多久時間？: 里克特的老鼠實驗顯示，曾經「獲救」的老鼠最終平均撐上60 |
| ch-20 | 0.90 | 3 | KB/Wiki/Sources/財富階梯/ch-20.md | 財富階梯: 離婚、官司、財務紛爭等問題對每個財富階層都會造成影響，並非特定階層獨有 |
| ch-21 | 0.92 | 3 | KB/Wiki/Sources/財富階梯/ch-21.md | 第11章｜金錢能買到幸福嗎？: 康納曼和迪頓2010年研究指出年收入超過75,000美元後，更多 |
| ch-22 | 0.92 | 3 | KB/Wiki/Sources/財富階梯/ch-22.md | 第12章｜人生強化器: 鹽是食物界的終極強化器，它的作用是強化原本就存在的味道，而不是增添新味道 |
| ch-23 | 0.92 | 3 | KB/Wiki/Sources/財富階梯/ch-23.md | 第13章｜我在財富階梯往上爬的旅程: 作者父母在1980年代末期在麥當勞工作時結識，都出身勞工階 |
| ch-24 | 0.90 | 3 | KB/Wiki/Sources/財富階梯/ch-24.md | 結語｜在複雜中尋求簡單: 古希臘人在發現π或圓面積公式之前，使用內切和外切多邊形的近似法來估算圓 |
| ch-25 | 0.85 | 2 | KB/Wiki/Sources/財富階梯/ch-25.md | 致謝: 作者感謝Carl Joseph-Black、Katie Gatti Tassin、Fra |
| ch-26 | 0.90 | 1 | KB/Wiki/Sources/財富階梯/ch-26.md | 註釋: 2022年的消費者財務狀況調查中，有25%的調查對象是在2023年1月至4月進行調查的 |
| ch-27 | 0.95 | 3 | KB/Wiki/Sources/財富階梯/ch-27.md | 版權頁: 《財富階梯》作者為尼克．馬朱利（Nick Maggiulli），譯者為李芳齡 |

## 跳過: 6 章 LLM 自動 defer (SKIP_LLM自動defer/) — 封面/書名頁/部標題
## 跳過: 64 concept dry-run 佔位 (SKIP_dryrun概念佔位/) — 整句當 label，等真概念抽取 slice
