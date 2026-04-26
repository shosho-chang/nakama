---
type: research
created: 2026-04-26
purpose: PR D Vision LLM model selection — Sonnet 4.6 baseline rerun for ch1 13 figures
runner_model: claude-sonnet-4-6 (via Claude Code Sonnet sub-agent)
prompt_template: .claude/skills/textbook-ingest/prompts/vision-describe.md
domain: bioenergetics
book_subtype: textbook_pro
system_role: biochemistry expert
---

# Ch1 Vision describe — Sonnet 4.6 rerun

對照標的：既有 ch1.md frontmatter `figures[].llm_description`（Opus 4.7 in-session）

---

## fig-1-1 (ATP hydrolysis equation)
- **tied_to_section**: 1.1 Adenosine Triphosphate: The Energy Currency
- **caption**: ATP hydrolysis equation (rasterised inline equation)
- **Sonnet 4.6 description**:

  Rasterised chemical equation showing the reversible hydrolysis of adenosine triphosphate（ATP，三磷酸腺苷）: $$\text{ATP} \leftrightarrow \text{ADP} + P_i + \text{Energy}\ (7.3\ \text{kcal or}\ 30.5\ \text{kJ})$$. The image is a typeset formula rendered in a serif font against a white background, with a bidirectional arrow (↔) separating reactant from products, and the energy release value parenthetically appended after the inorganic phosphate（無機磷酸鹽, $P_i$）term. The subscript $i$ in $P_i$ denotes inorganic phosphate released upon terminal phosphoanhydride bond cleavage. No axes, scale bars, or graphical annotations are present — the figure is purely an equation image. The 7.3 kcal/mol (≈30.5 kJ/mol) value represents the standard free-energy change ΔG°' for phosphoanhydride hydrolysis and anchors §1.1's claim that ATP is the sole directly usable energy currency（能量貨幣）for muscle contraction; the reversibility arrow indicates that the same reaction can be driven in reverse (ADP rephosphorylation) given sufficient energy input from PCr, glycolysis, or oxidative phosphorylation.

---

## fig-1-2 (ATP structural diagram)
- **tied_to_section**: 1.2 Energy Continuum
- **caption**: Figure 1.1 Adenosine triphosphate (ATP)
- **Sonnet 4.6 description**:

  Schematic structural diagram of adenosine triphosphate（ATP，三磷酸腺苷）illustrating its three-component architecture. On the upper left, the Adenine（腺嘌呤）base is depicted as a fused bicyclic ring system (a hexagonal pyrimidine ring fused to a pentagonal imidazole ring) in blue-grey and green shading. The adenine base connects via a glycosidic linkage to a Ribose（核糖）sugar represented as a pink pentagon. Extending rightward from the ribose are three consecutive phosphate groups（磷酸基）each labeled "P" inside a yellow circle and connected by wavy bonds; the label "Phosphates" with a curly brace spans the three P groups beneath them, and a callout line from the terminal bond between the second and third phosphate group is labeled "High-energy bond"（高能磷酸鍵）. The diagram visually encodes §1.1's textual description that ATP = adenine + ribose + three phosphate groups linked by high-energy phosphoanhydride bonds, and specifically identifies the terminal β–γ phosphoanhydride bond as the locus of energy release during hydrolysis — the bond whose cleavage is depicted in fig-1-1.

---

## fig-1-3 (Energy continuum line plot)
- **tied_to_section**: 1.2 Energy Continuum
- **caption**: Figure 1.2 Energy continuum
- **Sonnet 4.6 description**:

  Multi-curve line plot depicting the rate of energy used (Y-axis: kcal·kg⁻¹·h⁻¹, linear scale 0–45) versus time (X-axis: seconds, linear scale 0–100) for three ATP-resynthesis systems during maximal continuous exercise. The PCr（磷酸肌酸）curve (red, filled circles) begins at peak ≈45 kcal·kg⁻¹·h⁻¹ at t=0, descends steeply and monotonically, reaching approximately zero at t≈30 s. The Glycolytic（糖解）curve (blue, open triangles) starts at zero, rises to a peak of approximately 21 kcal·kg⁻¹·h⁻¹ at t≈10 s, then declines to zero by t≈45 s. The Aerobic（有氧）curve (green, filled triangles) begins near zero at t=0 and rises gradually, reaching approximately 7 kcal·kg⁻¹·h⁻¹ at t≈35 s and continuing to rise to ≈15 kcal·kg⁻¹·h⁻¹ by t=100 s where it approaches a plateau. Two key crossover points are visible: the PCr and Glycolytic curves intersect near t≈15 s, and the Glycolytic and Aerobic curves intersect near t≈40 s — these crossovers define the 1–10 s PCr-dominant, 10–60 s glycolysis-dominant, and >60 s aerobic-dominant dominance bands described in §1.2.

---

## fig-1-4 (Running speed vs distance)
- **tied_to_section**: 1.2 Energy Continuum
- **caption**: Figure 1.3 Primary energy sources for different running distances
- **Sonnet 4.6 description**:

  Categorical line-and-point plot mapping world-record running speed (Y-axis left: Running speed in mph, 0–30; Y-axis right: % of maximum speed, scale visible with dashed horizontal reference line at 100%) against eight standard track and road distances (X-axis: 100 m, 200 m, 400 m, 800 m, 1500 m, 5000 m, 10,000 m, Marathon). Three coloured background zones partition the X-axis by primary energy source: a narrow olive-green PCr（磷酸肌酸）band covering 100 m only; a light cyan Anaerobic glycolysis（無氧糖解）band covering 200–400 m; and a pink Aerobic metabolism（有氧代謝）band covering 800 m through Marathon. The green data line with filled circles connects approximate world-record speeds: 100 m ≈23.5 mph (≈100% max, dashed red horizontal reference line at this value), 200 m ≈22.4 mph, 400 m ≈20 mph, 800 m ≈17 mph, 1500 m ≈15.7 mph, 5000 m ≈14.3 mph, 10,000 m ≈14.0 mph, Marathon ≈12.4 mph (≈53% of max). The figure operationalises §1.2's energy continuum: as event distance increases, average sustainable speed decreases along a roughly hyperbolic curve, and the dominant ATP-resynthesis pathway shifts sequentially from PCr → anaerobic glycolysis → aerobic oxidation.

---

## fig-1-5 (Creatine kinase reaction equation)
- **tied_to_section**: 1.3 Energy Supply for Muscle Contraction
- **caption**: Creatine kinase (CK) reaction equation
- **Sonnet 4.6 description**:

  Rasterised typeset chemical equation depicting the reversible phosphotransfer reaction catalysed by creatine kinase（肌酸激酶，CK）: $$\text{ADP} + \text{PCr} \underset{\text{CK}}{\rightleftharpoons} \text{ATP} + \text{Cr}$$. The enzyme name "CK" is placed as a subscript beneath the double-headed equilibrium arrow (⇌), indicating catalysis. Reactants are adenosine diphosphate（ADP，二磷酸腺苷）and phosphocreatine（PCr，磷酸肌酸）on the left; products are ATP and free creatine（Cr，肌酸）on the right. No axes or graphical elements are present — the figure is a plain equation image rendered in a serif font. This is the Lohmann reaction, the single-step mechanism by which PCr donates its phosphate group to regenerate ATP from ADP during the first 1–10 seconds of high-intensity muscle contraction, as described in §1.3; the equilibrium arrow indicates that the reverse reaction (mi-CK–catalysed PCr resynthesis from mitochondrial ATP) also occurs during recovery.

---

## fig-1-6 (PCr shuttle schematic)
- **tied_to_section**: 1.3 Energy Supply for Muscle Contraction
- **caption**: Figure 1.4 PCr shuttle (mi-CK is mitochondrial CK; mm-CK is skeletal muscle CK; CrT is creatine transporter)
- **Sonnet 4.6 description**:

  Anatomical-biochemical schematic illustrating the phosphocreatine shuttle（磷酸肌酸能量穿梭）within a cross-sectionally rendered skeletal muscle fibre (depicted as a pink elongated fusiform cell with tapered ends). On the left, a yellow oval labelled "Oxidative Phosphorylation" (with ADP→ATP arrows circling inside) represents the mitochondrion（粒線體）; a small green cylinder on the mitochondrial surface represents mi-CK (mitochondrial creatine kinase，粒線體型肌酸激酶), which phosphorylates Cr to PCr using mitochondrially-generated ATP. PCr diffuses rightward through the sarcoplasm (indicated by an arrow labelled "PCr") to a magenta square at the contractile site representing CrT (creatine transporter，肌酸轉運體), where mm-CK (skeletal muscle CK, depicted as a blue circle in the legend) converts PCr back to ATP for contraction while releasing Cr. Free Cr then diffuses leftward (arrow labelled "Cr") back to the mitochondrion, completing the shuttle cycle. A separate blue arrow from outside the cell labelled "Cr" entering via CrT represents sarcolemmal import of plasma creatine. The legend at the bottom identifies colour codes: mi-CK = green cylinder, mm-CK = blue circle, CrT = magenta square. The figure mechanistically explains §1.3's PCr shuttle concept: spatial decoupling between mitochondrial ATP production and contractile ATP demand is resolved by shuttling high-energy phosphate as PCr rather than as ATP itself.

---

## fig-1-7 (PCr resynthesis recovery curve)
- **tied_to_section**: 1.3 Energy Supply for Muscle Contraction
- **caption**: Figure 1.5 Resynthesis of PCr after exercise with and without an occluded blood supply (adapted from Hultman et al., 1990)
- **Sonnet 4.6 description**:

  Time-course line plot with error bars showing muscle phosphocreatine concentration (Y-axis: PCr in mmol/kg dw, linear scale 0–80) versus recovery time (X-axis: minutes, −1 to 20, with t=0 marking end of exercise). A teal-shaded vertical band labelled "Exercise" occupies the period immediately left of t=0, and a downward arrow at the left edge is labelled "Rest," indicating the pre-exercise baseline. Two experimental conditions are shown: "Blood flow intact" (red curve, filled circles with error bars) — resting PCr ≈75 mmol/kg dw, depleted to approximately 12 mmol/kg dw at end-exercise (t=0), rapidly recovering to ≈67 by t=2 min, ≈70 by t=4 min, ≈72 by t=8 min, and ≈75 by t=20 min, tracing a biphasic exponential recovery with a fast initial phase then a slower plateau phase. "Blood flow occluded" (green curve, filled squares with error bars) — same end-exercise depletion to ≈12 mmol/kg dw, but PCr remains essentially flat at 8–10 mmol/kg dw through t=1, 2, 3, 4, and 5 min observation, with no recovery. The stark contrast between the two curves directly demonstrates §1.3's claim that PCr resynthesis（磷酸肌酸再合成）is obligatorily oxygen- and blood-flow dependent, requiring mitochondrial oxidative phosphorylation; adapted from Hultman, Bergstrom, Spriet & Soderlund (1990), Biochemistry of Exercise VII 21, 73–92.

---

## fig-1-8 (Anaerobic glycolysis overall equation)
- **tied_to_section**: 1.3 Energy Supply for Muscle Contraction
- **caption**: Anaerobic glycolysis overall equation
- **Sonnet 4.6 description**:

  Rasterised typeset linear equation summarising the net stoichiometry of anaerobic glycolysis（無氧糖解）from intramuscular glycogen: $$\text{Glycogen} \rightarrow \text{Glucose-1-P} \rightarrow \text{Lactic acid} + \text{ATP}$$. The equation is rendered as a single line of serif text with right-facing arrows connecting substrate to intermediate to products. Glycogen（肌糖原）enters as the primary substrate via glycogenolysis（肝糖分解）yielding glucose-1-phosphate（葡萄糖-1-磷酸, Glucose-1-P）as the proximal intermediate; Glucose-1-P then undergoes the Embden–Meyerhof pathway (10 reactions, abbreviated by the single arrow) to yield lactic acid（乳酸）and ATP as terminal products. No axes, numerical coefficients, or stoichiometric details (net 3 ATP from glycogen, 2 ATP from glucose) are shown — the figure conveys the qualitative headline points §1.3 emphasises: substrate is glycogen, the pathway is oxygen-independent (anaerobic), and end products are lactate and ATP.

---

## fig-1-9 (Aerobic metabolism overall equations)
- **tied_to_section**: 1.3 Energy Supply for Muscle Contraction
- **caption**: Aerobic metabolism overall equations
- **Sonnet 4.6 description**:

  Two parallel rasterised typeset equations, each on a separate line, summarising aerobic oxidative metabolism（有氧氧化代謝）in mitochondria. The first equation: $$\text{Glucose} + \text{Oxygen} \rightarrow \text{Carbon dioxide} + \text{Water} + \text{ATP}$$. The second equation: $$\text{Fatty acid} + \text{Oxygen} \rightarrow \text{Carbon dioxide} + \text{Water} + \text{ATP}$$. Both equations are rendered in a plain serif typeface using full English chemical names rather than chemical formulas (i.e. "Oxygen," "Carbon dioxide," "Water" rather than O₂, CO₂, H₂O), with right-facing arrows indicating the net direction of reaction. No stoichiometric coefficients, net ATP yield values, or axis information are present. The two equations compress the multi-step glucose oxidation cascade (glycolysis + pyruvate dehydrogenase + TCA cycle + electron transport chain, ~26 reactions) and the fatty acid oxidation cascade (β-oxidation + TCA + ETC, ~90–100 reactions) into their headline net stoichiometry; §1.3 invokes these to establish that both substrates require O₂ and yield CO₂, H₂O, and ATP, while §1.5 uses the reaction-step count difference to explain why fatty acid oxidation has a lower maximal ATP production rate than carbohydrate oxidation.

---

## fig-1-10 (Substrate use at three exercise intensities)
- **tied_to_section**: 1.3 Energy Supply for Muscle Contraction
- **caption**: Figure 1.6 Carbohydrate and fat use at three exercise intensities (adapted from Romijn et al., 1993)
- **Sonnet 4.6 description**:

  Stacked vertical bar chart showing total energy expenditure (Y-axis: kcal·kg⁻¹·min⁻¹, linear scale 0–300) partitioned between two substrates at three exercise intensities (X-axis: 25%, 65%, 85% of VO₂max, presented as categorical groups). The lower bar segment (pink) represents Fat（脂肪）contribution; the upper bar segment (teal/cyan) represents Carbohydrate（醣類）contribution; a legend in the upper left identifies both segments. At 25% VO₂max (low intensity, ~walking pace): total bar height ≈80 kcal·kg⁻¹·min⁻¹, fat segment ≈70 (≈87% of total), carbohydrate segment ≈10. At 65% VO₂max (moderate aerobic): total ≈207, fat ≈100 (≈48% of total), carbohydrate ≈107. At 85% VO₂max (high-intensity aerobic with partial anaerobic contribution): total ≈280, fat ≈70 (≈25% of total), carbohydrate ≈210. The figure encodes a non-monotonic fat utilisation pattern: fat fractional contribution decreases monotonically across intensities, but absolute fat oxidation rate peaks at 65% VO₂max (≈100) and falls again at 85% VO₂max (≈70); this pattern supports §1.3's description of intensity-dependent substrate selection（強度依存性基質選擇）and is the foundational dataset from Romijn, Sidossis, Gastaldelli, Horowitz, Endert & Wolfe (1993), American Journal of Physiology 265, E380–E391.

---

## fig-1-11 (Sustainable speed vs distance)
- **tied_to_section**: 1.4 Energy Systems and Running Speed
- **caption**: Figure 1.7 Sustainable running speed and distance run
- **Sonnet 4.6 description**:

  Area-filled hyperbolic decay plot showing sustainable running speed (Y-axis left: Speed in mph, linear scale 0–23; Y-axis right: % of maximum speed, scale 0–100, with corresponding tick marks) against running distance (X-axis: Distance, with labelled points at 100 m, 10,000 m, and 20,000 m on a non-uniform scale that compresses as distance increases). A green curve traces the speed-distance relationship, starting at approximately 22.8 mph (≈100% max) at 100 m, falling steeply through 15 mph by ≈150 m, then continuing to decline more gradually to approximately 12 mph (≈52% max) by 20,000 m+. Two filled zones beneath and beside the curve partition the plot by energy system dominance: a red zone ("Anaerobic zone") fills the narrow distance band up to the vertical dashed red line at 100 m, and a blue zone ("Aerobic zone") fills the remaining wide area from 100 m onward. The vertical dashed red line at 100 m marks the approximate boundary where aerobic metabolism（有氧代謝）becomes dominant over anaerobic systems（無氧系統）. The hyperbolic shape of the speed-distance relationship makes §1.4/§1.5's thesis visually explicit: the steep decline in the 0–150 m range corresponds to exhaustion of PCr and anaerobic glycolysis capacity, while the shallower asymptote beyond 150 m reflects the lower but sustained ATP production rate of aerobic fat and carbohydrate oxidation.

---

## fig-1-12 (Energy substrate schema flowchart)
- **tied_to_section**: 1.6 Energy Sources and Muscle
- **caption**: Figure 1.8 Schema of key sources and processes for skeletal muscle to derive energy during exercise
- **Sonnet 4.6 description**:

  Three-column biochemical pathway flowchart depicting convergence of three macronutrient classes on a central mitochondrial oxidative core. The three input columns at the top are labelled LIPIDS (left, yellow-background boxes), CARBOHYDRATES (centre, purple-background boxes), and PROTEINS (right, grey-background boxes). LIPIDS: Triglycerides（三酸甘油酯）→ Fatty acids + Glycerol (separate branches). CARBOHYDRATES: Glycogen（肝糖）→ Glucose, with bidirectional red arrows (downward, catabolic) and blue arrows (upward, anabolic) connecting Glucose ↔ Pyruvic acid（丙酮酸）via Glycolysis（糖解，red arrow label, downward) and Gluconeogenesis（糖質新生，blue arrow label, upward). PROTEINS: Proteins → Amino acids（胺基酸）→ Pyruvic acid (merging with the carbohydrate column). Fatty acids, Glycerol, and Pyruvic acid all converge via red arrows on Acetyl-CoA（乙醯輔酶 A, purple box) which is enclosed within a blue-shaded oval representing the Mitochondrion（粒線體）compartment. Inside the mitochondrion, Acetyl-CoA feeds into the TCA Cycle（檸檬酸循環, yellow circle), with CO₂ exiting at both the Acetyl-CoA entry step and the TCA cycle. A red bubble labelled "H" (representing reduced cofactors NADH/FADH₂) passes from the TCA cycle to the Electron Transport System（電子傳遞鏈, pink box), which receives O₂ and outputs H₂O; two ATP labels (yellow ovals) appear at the bottom of the mitochondrion, indicating oxidative phosphorylation output. A key in the lower right identifies red arrows = Catabolic pathway（異化途徑）and blue arrows = Anabolic pathway（同化途徑）. The figure operationalises §1.6's claim that anaerobic processes (ATP, PCr, glycolysis) occur in the cytoplasm while aerobic processes occur in the mitochondria.

---

## fig-1-13 (Amino acid energy use flowchart)
- **tied_to_section**: 1.7 Can Muscle Use Protein for Energy?
- **caption**: Figure 1.9 Likely use of amino acids for energy
- **Sonnet 4.6 description**:

  Hierarchical two-branch flowchart illustrating the metabolic routing of amino acids（胺基酸）derived from protein catabolism during exercise. At the top, a pink-background box labelled "PROTEIN" branches via two arrows into two blue-shaded irregular cloud shapes representing amino acid pools: "Glutamine and alanine"（麩醯胺酸與丙胺酸）on the left, and "Leucine"（白胺酸）on the right. From the Glutamine and alanine node: a leftward arrow labelled NH₃ exits to a box labelled "Urea" (indicating nitrogen disposal via hepatic urea cycle), and a downward arrow leads to "Glucose (Liver)" — indicating hepatic gluconeogenesis（肝臟糖質新生）converting deaminated carbon skeletons to glucose; below that, ADP → ATP (phosphorylation) leads to terminal products "Carbon dioxide and water." From the Leucine node: similarly, a rightward arrow labelled NH₃ exits to "Urea," and a downward arrow leads directly to ADP → ATP → "Carbon dioxide and water" without a liver/glucose intermediate step, indicating direct intramuscular oxidation（直接肌內氧化）of this branched-chain amino acid（BCAA，支鏈胺基酸）. The two parallel pathways encode §1.7's mechanistic claim that alanine and glutamine contribute to energy via the interorgan glucose-alanine cycle while leucine undergoes direct peripheral BCAA catabolism, both ultimately yielding ATP, CO₂, and H₂O after obligatory nitrogen removal as urea.

---

## Opus 主對話 head-to-head verdict（Opus 4.7 主對話 vs 既有 Opus 4.7 in-session description）

| ref | Verdict | 關鍵差異 |
|---|---|---|
| fig-1-1 | Sonnet 略佳 | Sonnet 多 reversibility + downstream PCr/glycolysis 連結 |
| fig-1-2 | Sonnet 略佳 | Sonnet 識別 β-γ phosphoanhydride bond + bicyclic pyrimidine+imidazole |
| fig-1-3 | Sonnet 略佳 | Sonnet 多 t≈35s intermediate datapoint + 雙語 PCr/糖解/有氧 |
| fig-1-4 | Sonnet 略佳 | Marathon 速比 53% vs Opus 55%（Sonnet 算對：12.4/23.5=52.8%） |
| fig-1-5 | Sonnet 略佳 | Sonnet 帶出 1-10s 主導窗口 + mi-CK reverse direction |
| fig-1-6 | **Opus 略佳** | Sonnet 把 CrT 描述為 contractile site，Opus 正確指出 CrT @ sarcolemma |
| fig-1-7 | 平手~Sonnet 略佳 | Sonnet 多 "biphasic exponential" 命名 + Rest 標記 |
| fig-1-8 | Sonnet 略佳 | Sonnet 帶出 net 3 ATP / 2 ATP anchor（但中譯不一致：肌糖原/肝糖分解） |
| fig-1-9 | 平手 | Sonnet 注意 figure 用 English names 非 formula 的 visual detail |
| fig-1-10 | Sonnet 略佳 | Sonnet fat fraction 87%/48%/25% 比 Opus「50/50」精準 |
| fig-1-11 | Mixed | Y-axis scale 0-23 Sonnet 對；anaerobic boundary disagreement |
| fig-1-12 | 平手 | Sonnet 視覺更細，Opus functional 推論更深 |
| fig-1-13 | **Opus 略佳** | Opus 帶出 ~5% of muscle energy needs 量化 anchor |

**Tally**：Sonnet 7 / Opus 2 / Tie 4

## PR D 結論

**Vision LLM 維持 ADR-011 §3.4 default — Sonnet 4.6**。

依據：
1. 13/13 張全跑成功，無誤解圖意或失敗
2. 品質不輸 Opus 4.7，多數張更精準（數值、雙語術語、textbook citation 完整度）
3. Cost 約 Opus 1/5 — 284 figures × 10 章估省 ~$3-5

## 已知 Sonnet weakness（要 mitigation）

兩個系統性風險：

1. **Anatomical localization in schematics**（fig-1-6 case）— anatomical schematic 圖解 Sonnet 可能搞混元件位置。Mitigation：ch2 *Skeletal Muscle Structure and Function* 預期 schematic 多，**ch2 ingest 時挑 2-3 張先 Sonnet 跑、修修人眼 spot-check**；不滿意再單獨升 Opus。

2. **Minor blemishes**（LaTeX syntax / 雙語術語一致性）— fig-1-9 LaTeX 多 `}` typo + fig-1-8「肌糖原/肝糖分解」中譯不一致。Mitigation：driver post-process 加 sanity check（grep 重複 `}}` / 中譯表對齊）— 但這是 follow-up 而非 PR D blocker。

## 意外 finding

ch1 既有 description 標榜是 Opus 4.7 in-session 跑的，但有些 case 比 Sonnet 4.6 API 跑的差（如 Marathon% 算術錯、ATP 結構少 β-γ bond anchor、Romijn fat fraction 不夠精準）。可能因素：互動 session Read 的 image attention 跟 isolated API call 不一樣 / Opus 寫得 concise 但漏細節 / variance。

implication：**互動 session vs 自動 API 的 Vision quality 差異可能比 model tier 差異更大**。如果未來修修自己在 Claude Code session 對特定圖手動 Read，預期會比 Sonnet API 略差 — 反直覺但有資料支持。
