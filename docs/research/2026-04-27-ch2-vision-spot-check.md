# ch2 Vision describe spot-check（Sonnet 4.6）

> **建立日**：2026-04-27
> **用途**：PR D 批 ingest ch2-ch11 開跑前的 schematic spot-check（V1 acceptance 後續 mitigation）
> **驗收對象**：Sonnet 4.6 對 ch2 *Skeletal Muscle Structure and Function* 三張 schematic 圖的 vision describe 精確度
> **狀態**：等修修在 Obsidian 對照原圖人眼確認

---

## 背景

V1 acceptance 的 [head-to-head report](2026-04-26-ch1-vision-sonnet-rerun.md) tally Sonnet 7 / Opus 2 / Tie 4，已決定 PR D 維持 Sonnet 4.6 default。但同 report 標出 **Sonnet 已知 weakness**：fig-1-6 PCr shuttle 把 CrT 位置搞混 — anatomical localization in schematics。

ch2 主題是骨骼肌結構與功能，schematic 比 ch1 多。本 spot-check 挑 3 張不同子類型的 schematic 在 PR D batch 開跑前先驗收 Sonnet 是否會在 ch2 重蹈覆轍。

## 取樣 3 張 figure

| ref | tied_to_section | sub-type | 為什麼挑 |
|-----|-----------------|----------|---------|
| fig-7-1 | 2.1.2 The Muscle Fibre | multi-panel anatomical illustration | 直接測 anatomical localization weakness |
| fig-7-7 | 2.2 Muscle Contraction | functional flowchart | 測流程圖節點順序與分支 |
| fig-7-9 | 2.2.3 The Sliding Filament Mechanism | cyclic schematic | 測循環示意 + 分子化學標記 |

每張 walker 已經抽到 `F:/Shosho LifeOS/Attachments/Books/biochemistry-sport-exercise-2024/ch7/fig-7-{N}.png`（ch7 = nav#7，driver 在 ch2 ingest 階段才會 reindex 為 `ch2/`）。

## 跑法

3 個 Sonnet 4.6 sub-agent 並行，prompt 對齊 `prompts/vision-describe.md` 完整 fill-in（含 surrounding text ±500 chars），直接輸出 `figures[].llm_description` 文字。

---

## fig-7-1 — Gross anatomical structure of skeletal muscle

**caption**: Figure 2.1 Gross anatomical structure of skeletal muscle (Tortora and Derrickson, 2009 / John Wiley & Sons)

**alt（walker）**: An anatomical structure illustrates the components of a skeletal muscle depicts bone, fasciculus, tendon, periosteum, epimysium, belly of skeletal muscle, perimysium, epimysium, fascicle, muscle fiber (cell), myofibril, perimysium, endomysium, Somatic motor neuron, Striations, Sarcoplasm, Sarcolemma, Myofibril, Filament, Blood capillary.

**Sonnet 4.6 描述**：

> This is a multi-panel anatomical illustration (schematic diagram) depicting the gross and microscopic structure of skeletal muscle, progressing from whole-muscle macrostructure to the myofibrillar level across three levels of magnification shown as cutaway views. No quantitative axes, units, or scale bars are present; the figure is purely structural and organisational in nature. At the macroscopic level, the illustration labels the periosteum（骨膜）, tendon（肌腱）, bone（骨）, and the belly of skeletal muscle, showing how the tendon anchors muscle to periosteum-covered bone; a small inset at top-left orients the viewer with a transverse plane cross-section indicator on a limb silhouette. Proceeding inward, the connective tissue hierarchy is fully labeled: epimysium（肌外膜）wraps the entire muscle belly; perimysium（肌束膜）subdivides the muscle into fascicles（肌束，labeled as "Fasciculus" on the large transverse section cutaway); and endomysium（肌內膜）ensheathes each individual muscle fibre（肌纖維）. Within a single fascicle, the illustration identifies the muscle fibre (cell), myofibril（肌原纖維）, nucleus（細胞核）located at the periphery of the fibre, sarcoplasm（肌漿）, sarcolemma（肌纖維膜）, blood capillary（微血管）, somatic motor neuron（體運動神經元）, and striations（橫紋）, with the bottom panel further resolving individual filaments（肌絲）. The neurovascular supply — capillaries and motor neuron — is shown penetrating through the perimysium into contact with individual fibres, consistent with the text's description of fibres as long, cylindrical, multinucleated, and postmitotic cells that span the full muscle length. The figure directly supports the surrounding chapter text's claims that muscle fibres are peripherally nucleated, encased in endomysium, bundled into fascicles by perimysium, and that the whole muscle is wrapped by epimysium, providing the student with a single integrated visual that maps all three connective tissue layers onto both the gross ("belly") and histological ("transverse sections") scales simultaneously.

**修修人眼驗收**（在 Obsidian 對照 `Attachments/Books/biochemistry-sport-exercise-2024/ch7/fig-7-1.png`）：

- [ ] 三層連結組織（epimysium / perimysium / endomysium）與包覆對象對應正確
- [ ] nucleus 位置（peripheral）描述對
- [ ] 三層放大鏡頭（macro → fascicle → fibre → filament）順序對
- [ ] **判斷**：🟢 / 🟡 / 🔴

---

## fig-7-7 — Chain of events from brain to muscle (Gandevia 2001)

**caption**: Figure 2.7 Simplified schematic of the chain of events from brain to muscle which result in force production (adapted from Gandevia, 2001)

**alt（walker）**: A flowchart depicts the skeletal muscle structure and function. The process begins in the brain, where we consciously decide to move a muscle. Motor cortical outputs and other supraspinal and propriospinal outputs are involved. Alpha and gamma motors transmit signals from the brain to the muscles. The process goes to motor axons, neuromuscular junction and sarcolemma, excitation-contraction coupling, cross-bridge force ATP hydrolysis.

**Sonnet 4.6 描述**：

> 本圖為流程圖（flowchart），無座標軸或量化刻度，以矩形節點與帶箭頭的有向連線呈現從大腦到肌肉的指令鏈。流程自頂部「Planning of willed movements（意志性動作規劃）」節點出發，向下分岔為兩個並列的下行輸出節點：左側「Motor cortical & other corticospinal outputs（運動皮質及其他皮質脊髓輸出）」與右側「Other supraspinal and propriospinal outputs（其他脊髓上及本脊髓輸出）」，兩節點間另有一條從左指向右的水平箭頭，顯示皮質脊髓路徑對脊髓上路徑的側向調控；兩者再匯聚至一個以玫瑰色圓形標示的「α and γ motor neurons（α 與 γ 運動神經元）」節點，強調脊髓前角兩型運動神經元的並行輸出。α/γ 節點往下依序連接「Motor axons（運動軸突）」、「Neuromuscular junction and sarcolemma（神經肌肉接頭與肌纖維膜）」、「Excitation–contraction coupling（興奮-收縮偶聯）」，最終抵達「Cross-bridge force / ATP hydrolysis（橫橋作用力 / ATP 水解）」，共呈現七個階層節點。本圖直接對應周遭文字所述「voluntary muscle contractions begin with a nerve impulse travelling from the motor cortex in the brain through to the spinal cord via the brain stem, then communicated to skeletal muscle fibres through motor neurons which branch out from the spinal cord」，將文字描述的神經-肌肉傳導鏈具象化為有序的單向因果序列，並特別透過圓形色塊區分 α/γ 運動神經元作為中樞-周邊橋接的關鍵節點，與下游肌肉端的興奮-收縮偶聯（excitation–contraction coupling）及橫橋週期（cross-bridge cycle）中 ATP 水解產生收縮力的最終效應加以區隔。

**修修人眼驗收**（在 Obsidian 對照 `Attachments/.../ch7/fig-7-7.png`）：

- [ ] 7 階層節點順序與圖一致
- [ ] α/γ motor neurons 區分對
- [ ] 兩個 supraspinal 並列分支命名與位置對
- [ ] **判斷**：🟢 / 🟡 / 🔴

---

## fig-7-9 — Contraction-relaxation cycle

**caption**: Figure 2.9 The contraction-relaxation cycle (Tortora and Derrickson, 2009 / John Wiley & Sons)

**alt（walker）**: A cyclic structure starts with the myosin heads hydrolyzing ATP, myosin heads bind to actin, forming cross-bridges, myosin cross-bridges rotate toward the center of the sarcomere, myosin heads bind ATP, the cross-bridges detach from actin.

**Sonnet 4.6 描述**：

> Fig. 2.9 is a schematic diagram（示意圖）arranged as a circular flowchart depicting the contraction-relaxation cycle（收縮-舒張循環）of skeletal muscle, comprising five sequential illustrated panels connected by curved arrows indicating the cyclic, directional progression of events; there are no quantitative axes, units, or scale bars.
>
> The key legend identifies a small spherical symbol representing Ca²⁺. Proceeding clockwise from the upper-right panel: (1) "Myosin heads bind to actin, forming crossbridges（橫橋）" — a myosin head（肌凝蛋白頭部）carrying ADP is shown docking onto the actin（肌動蛋白）filament (rendered as a twisted double-strand helix in orange-brown), with ADP depicted adjacent to the head; (2) "Myosin crossbridges rotate toward center of the sarcomere（肌節）(power stroke（動力衝程）)" — the myosin head pivots, ADP is released, and the thin filament is drawn toward the M-line（M線）; (3) "As myosin heads bind ATP, the crossbridges detach from actin" — ATP molecule binds the myosin head, causing detachment; (4) "Myosin heads hydrolyze ATP and become reoriented and energized" — ATP is cleaved to ADP + Pᵢ（inorganic phosphate（無機磷酸鹽））, cocking the myosin head back to its high-energy conformation ready for the next stroke; the central annotation reads "Contraction cycle continues if ATP is available and Ca²⁺ level in the sarcoplasm（肌漿）is high."
>
> The figure directly illustrates the text's description of ATP-dependent myosin detachment and ATPase（三磷酸腺苷酶）hydrolysis: the text states that myosin remains bound to actin after the power stroke until ATP binds, whereupon release occurs and ATPase hydrolyzes ATP to re-energize the head — each of these molecular events corresponds one-to-one to a labeled panel in the cycle. The cyclic arrow structure emphasizes the text's point that continued contraction requires continual ATP and Ca²⁺ supply, with the central caption explicitly encoding both prerequisites. Precise anatomical and molecular labels identifiable in the diagram include: actin filament, myosin head, crossbridge, ADP, ATP, Pᵢ (implicit in hydrolysis step), sarcoplasm, sarcomere center/M-line directionality, and Ca²⁺ as the regulatory ion gating cycle re-entry.

**修修人眼驗收**（在 Obsidian 對照 `Attachments/.../ch7/fig-7-9.png`）：

- [ ] 5 panel 循序與圖一致（順時針 vs 逆時針）
- [ ] ATP/ADP/Pᵢ 在每個 panel 的化學狀態標示對
- [ ] 中央 caption 文字摘錄正確
- [ ] **判斷**：🟢 / 🟡 / 🔴

---

## 我的 driver verdict（先給修修參考，最終以人眼為準）

| Figure | 驗收重點 | 我的判斷 |
|--------|---------|---------|
| fig-7-1 | anatomical layer labels（V1 weakness 直擊） | 🟢 三層連結組織 + 三層放大尺度 + nucleus peripheral 全對 |
| fig-7-7 | flowchart 節點順序 | 🟢 7 階層 + α/γ 並列分支命名清楚 |
| fig-7-9 | cyclic 分子狀態 | 🟢 5 panel ATP/ADP/Pᵢ 完整 + 中央 caption verbatim |

**整體**：3 張 schematic 都沒重蹈 fig-1-6 anatomical localization 弱點。Sonnet 4.6 對 ch2 schematic 預期可勝任 batch ingest。

## 結論 + 下一步

- **修修若 3 張全 🟢**：放手 PR D 批跑 ch3-ch11（ch2 順手在批跑時跑完整章 vision describe + ingest）
- **修修若有任何 🔴**：對應 sub-type 在 PR D 升 Opus 4.7（cost ~5x），其餘 sub-type 維持 Sonnet
- **修修若有 🟡**：保留 Sonnet 但 ch2 開跑前在 prompt 加 mitigation note（如「若是 cyclic 類別，每個 panel 必標化學狀態」）

---

## Multi-model triangulate（2026-04-27 update）

修修 review 時點出：「我肉眼確認搞不好沒有跑三次 AI model 來確認還準。」遵循 [`project_multi_model_panel_methodology.md`](../../memory/claude/project_multi_model_panel_methodology.md) 三家 model triangulate 方法論，再派 Gemini 2.5 Pro/Flash + Grok-4 vision 對同三張圖跑同 prompt，跟 Sonnet 4.6 head-to-head：

- **Anthropic**：Sonnet 4.6（已上）
- **Google**：Gemini 2.5 Pro（fig-7-1 一發成功）/ Gemini 2.5 Flash（fig-7-7 + fig-7-9 — Pro 503 fallback Flash）
- **xAI**：Grok-4 vision（三張全 OK）

> Note：**沒有跑 OpenAI GPT** — repo 沒 OPENAI_API_KEY，且修修以前 multi-model panel 方法論本身就是 Gemini + Claude + Grok 三家。

Raw 三家描述見 [`vision_triangulate_results.json`](../../.claude/worktrees/ingest-v2-pr-b-parse-book/vision_triangulate_results.json)（throwaway script + results；本次 commit 不收進 main）。

### fig-7-1 — 三家 consensus，Sonnet 通過

| 共識點（三家都對） | 證據 |
|------|------|
| 三層連結組織包覆關係：epimysium 包整塊 muscle belly / perimysium 包 fascicle / endomysium 包單一 fibre | 三家描述完全一致 |
| nucleus peripheral 多核 + postmitotic | Sonnet「peripherally located」/ Gemini「multiple peripheral nuclei」/ Grok「multinucleated nature with peripherally located nuclei」 |
| 三層放大尺度（macro → fascicle → fibre → filament/myofibril） | 三家命名與順序一致 |
| top-left inset 是 limb silhouette + transverse plane indicator | Sonnet 與 Gemini 都提到（Grok 沒明寫但描述方向一致） |

**Sonnet unique（無 dispute）**：列出 striations / sarcoplasm / sarcolemma 等 raw labels — 比另兩家更詳盡，但無錯誤。

**Verdict**：🟢 — 三家對 anatomical localization 完全 consensus，沒重蹈 fig-1-6 weakness。

---

### fig-7-7 — 三家 consensus，Sonnet 通過

| 共識點（三家都對） | 證據 |
|------|------|
| **7 階層節點順序**：Planning → 兩條並列 (Motor cortical & corticospinal / Other supraspinal & propriospinal) → α/γ motor neurons → Motor axons → NMJ + sarcolemma → Excitation-contraction coupling → Cross-bridge force / ATP hydrolysis | **三家 100% consensus** |
| α/γ motor neurons 是匯合節點（central pink circle） | Sonnet & Grok 都提到顏色與形狀；Gemini 沒提形狀但流程一致 |

**Sonnet unique 一條 minor dispute**：Sonnet 寫「兩節點間另有一條從左指向右的水平箭頭，顯示皮質脊髓路徑對脊髓上路徑的側向調控」— **只 Sonnet 提**，Grok / Gemini 沒提這條 lateral arrow。

→ 不影響主流程語意；如果原圖真有這條 lateral arrow，Sonnet 觀察更細；如果沒有就是 hallucination。**修修開圖瞄一眼 0.5 秒可解。**

**Verdict（暫定）**：🟢（lateral arrow 屬細節，不影響主流程；待修修確認）

---

### fig-7-9 — 三家 disagree on panel count + 顏色 + 起點，**修修要看這張**

| 對比項 | Sonnet 4.6 | Grok-4 | Gemini 2.5 Flash |
|--------|-----------|--------|------------------|
| **Panel 數** | **5 panels** + 中央 caption | **4 steps** | **4 steps** |
| **Step 1 起點** | upper-right「Myosin heads bind to actin, forming crossbridges」 | step 1「myosin heads hydrolyze ATP, become reoriented and energized」 | step 1「myosin heads hydrolyze ATP into ADP and Pi, become reoriented and energized」 |
| **方向** | clockwise from upper-right | 沒明寫（順 step 1-4） | 沒明寫（順 step 1-4） |
| **Actin 顏色** | "twisted double-strand helix in **orange-brown**" | "actin thin filaments (**red beaded** strands)" / myosin = brown coiled | "polymer of **yellow spherical units** associated with regulatory **blue and purple** components" |

**Grok + Gemini consensus**：4-step cycle，step 1 = ATP hydrolysis & energize。

**Sonnet 三點 unique**：5 panel / step 1 是 crossbridge formation / actin orange-brown。

→ **這就是 multi-model triangulate 的價值** — 我們在 fig-7-9 找到 **Sonnet 的 outlier observations 三點**：
1. 是不是真的 5 panel（vs 4）？或 Sonnet 把中央 caption 算成第 5 panel？
2. 起點是 ATP hydrolysis（共識）還是 crossbridge formation（Sonnet 唯一）？
3. Actin 顏色 Sonnet 說 orange-brown — 教科書 cycle 圖通常 actin 黃／紅、myosin 棕／紫，Sonnet 顏色描述疑似錯位（把 myosin 顏色套在 actin？）

→ **修修開 [`fig-7-9.png`](file:///F:/Shosho%20LifeOS/Attachments/Books/biochemistry-sport-exercise-2024/ch7/fig-7-9.png) 瞄一眼，就能解 3 個 dispute**。

### 三家共識完整內容（biochemistry 部分）

| 共識點 | 證據 |
|------|------|
| ATP→ADP+Pᵢ 水解 + myosin re-energize | 三家都提到 |
| Myosin head bind to actin → crossbridge formation | 三家都提到 |
| Power stroke 拉動 actin 朝 sarcomere center / Z-disc | 三家都提到（含原文 surrounding text 對應） |
| 新 ATP binding 觸發 detachment | 三家都提到 |
| Cycle continues if ATP available + Ca²⁺ in sarcoplasm high | 三家都精確抓到中央 caption |

→ **生化邏輯三家完全 consensus，無夢遊。**

---

## 修修人眼仲裁範圍（縮到最小）

原本要看 3 張圖每張全部細節 → 縮成「**只看 fig-7-9 三個 dispute 點**」+ fig-7-7 半秒看 lateral arrow：

- [ ] **fig-7-9 panel 數**：4 還 5 panel？
- [ ] **fig-7-9 step 1 起點**：圖上標號 step 1 的位置寫的是「myosin heads hydrolyze ATP」（Grok+Gemini 共識）還是「myosin heads bind to actin, forming crossbridges」（Sonnet）？
- [ ] **fig-7-9 actin 顏色**：actin filament 是不是 orange-brown？（Sonnet）— 還是其他顏色（Grok red beaded / Gemini yellow spherical）？
- [ ] **fig-7-7 lateral arrow**：兩條 supraspinal 並列分支之間有沒有 left→right 水平箭頭？（Sonnet 唯一）

如果 fig-7-9 三點全是「Sonnet 講錯」 → Sonnet 在 cyclic schematic 有 panel-count + sequence + color 失誤，PR D ch2 cyclic 類圖可能要升 Opus。

如果 fig-7-9 至少 2 點是「Sonnet 講對」 → 三家共識其實是 Grok + Gemini 兩家輕量 model 偷懶，Sonnet 看得更細，PR D 維持 Sonnet。

如果 mixed → 維持 Sonnet 但 prompt 加「panel count 顯式 verify」mitigation note。

---

## Reference

- V1 head-to-head：[`2026-04-26-ch1-vision-sonnet-rerun.md`](2026-04-26-ch1-vision-sonnet-rerun.md)
- ch1 acceptance checklist：[`docs/plans/2026-04-26-ch1-v2-acceptance-checklist.md`](../plans/2026-04-26-ch1-v2-acceptance-checklist.md)
- Vision describe prompt：[`.claude/skills/textbook-ingest/prompts/vision-describe.md`](../../.claude/skills/textbook-ingest/prompts/vision-describe.md)
- Multi-model panel 方法論：[`memory/claude/project_multi_model_panel_methodology.md`](../../memory/claude/project_multi_model_panel_methodology.md)
- ADR-011：[`docs/decisions/ADR-011-textbook-ingest-v2.md`](../decisions/ADR-011-textbook-ingest-v2.md)
