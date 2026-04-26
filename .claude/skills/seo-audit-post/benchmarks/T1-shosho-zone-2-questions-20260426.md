---
type: seo-audit-report
schema_version: 1
audit_target: https://shosho.tw/blog/zone-2-common-questions/
target_site: wp_shosho
focus_keyword: zone 2 訓練
fetched_at: '2026-04-26T11:39:28.079850+00:00'
phase: 1.5 (deterministic + llm)
generated_by: seo-audit-post (Slice D.2)
pagespeed_strategy: mobile
llm_level: haiku
gsc_section: skipped (GSC_PROPERTY_SHOSHO not set)
kb_section: skipped (--no-kb)
summary:
  total: 41
  pass: 26
  warn: 10
  fail: 0
  skip: 5
  overall_grade: A
---

# SEO Audit — 別再算錯 Zone 2！最新論文公布 14 位國際運動專家共識，常見 8 大疑問一次講清楚！ - 張修修的不正常人生

## 1. Summary

| 類別 | Pass | Warn | Fail | Skip |
|---|---|---|---|---|
| Fetch | 1 | 0 | 0 | 0 |
| Metadata | 4 | 1 | 0 | 0 |
| OpenGraph | 4 | 0 | 0 | 0 |
| Headings | 3 | 0 | 0 | 0 |
| Images | 2 | 3 | 0 | 0 |
| Structure | 3 | 0 | 0 | 0 |
| Schema | 3 | 1 | 0 | 1 |
| Performance | 0 | 0 | 0 | 3 |
| Semantic | 6 | 5 | 0 | 1 |
| **Total** | **26** | **10** | **0** | **5** |

**Overall grade: A**

## 2. Critical Fixes（必修）

（無）

## 3. Warnings（建議修）

### [M2] meta description 長度 150-160 字符

- **Actual**: len=89
- **Expected**: 150 ≤ len ≤ 160
- **Fix**: 加長到 150 字以上，自然帶入 focus keyword

### [I1] 所有 img 有非空 alt

- **Actual**: 9/12 張缺 alt
- **Expected**: 0 張缺 alt
- **Fix**: 補 alt 描述（含 focus keyword 自然出現）

### [I4] lazy loading 覆蓋（首屏外 ≥ 80%）

- **Actual**: 7/9 = 78%
- **Expected**: ≥ 80%
- **Fix**: 補 loading="lazy" 在非首屏 img；首屏 img 保 eager 給 LCP

### [I5] WebP/AVIF modern format 比例 ≥ 50%

- **Actual**: 2/8 = 25%
- **Expected**: ≥ 50%
- **Fix**: 主圖優化 WebP；舊 jpg/png 重新生成（SEOPress WebP plugin 可批次）

### [SC2] BreadcrumbList schema 存在

- **Actual**: 無 BreadcrumbList
- **Expected**: 存在
- **Fix**: 加麵包屑 schema（Home > Category > Article）增加 SERP rich result

### [L6] E-E-A-T Expertise：作者 bio / 引用 / credentials

- **Actual**: 作者張修修有隱含運動經驗背景（自稱 46 歲、長期跑者、踩台等），但無明確 credentials / 證照 / 學歷展示；引用論文作者（Inigo Mujika、Stephen Seiler）但未明確說明自身專業資格。
- **Expected**: 可見作者背景 + 至少 1 個權威引用 / 學歷 / 證照
- **Fix**: 建議在文首或尾部補充作者 bio，說明運動科學或認證背景（如教練證、運動員資歷等）。

### [L7] E-E-A-T Authoritativeness：被引用 / 外部 mention 提示

- **Actual**: 內文提及「我發布那支破百萬觀看」、「距離我發布...正好滿兩年啦」，隱示頻道知名度；未見明確外部引用標記或媒體 mention。
- **Expected**: 內文或 author footer 提示該作者 / 站被外部引用
- **Fix**: 若有外媒報導或業界引用，可在 footer 或 bio 明確陳列，強化 authority 信號。

### [L10] Schema 與內容一致性 / Internal link 機會

- **Actual**: Article schema headline ≈ H1（可見）；內文多次提及「上一篇文章」、「上集影片」但未見完整內部連結；KB context 無提供，無法檢測相關頁面是否被連。
- **Expected**: Article schema headline ≈ <h1>；KB 中相關頁面已被連結
- **Fix**: 將「上一篇文章」、「上集 VO2 Max 影片」、「之前的 zone 2 影片」等改為可點擊的內部連結，加強 internal link density。

### [L11] Medical references / DOI / PubMed / 衛福部 / WHO 等權威源引用率

- **Actual**: 文末提及「這篇研究/論文」但無明確 DOI、PubMed 連結或作者全名 citation；引用 Inigo Mujika、Stephen Seiler 為權威人物但未直接連結到其論文或機構。
- **Expected**: 至少 2-3 個外連到權威來源（PubMed / DOI / 衛福部 / WHO 等）
- **Fix**: 補充該篇論文的 DOI 或完整引用資訊（作者、年份、期刊）；為 Mujika、Seiler 等添加 PubMed/Google Scholar 外連；至少 2-3 個權威出處連結。

### [L12] Last reviewed date / 醫師審稿標記 / 內容更新頻率

- **Actual**: 文首顯示「2025-03-21」發布日期；內文無「最後更新」日期標記、醫師審稿標記或版本提示。
- **Expected**: 可見最後更新日期 / 醫師審稿 by / 內容版本標記
- **Fix**: 在文末補充「最後更新：[日期]」或加註「本文由 [醫師/教練名字] 審稿」標記，強化 YMYL freshness 信號；考慮添加 article:modified_time meta tag。

## 4. Info（觀察）

- [O3] og:url 等於 canonical — og:url == canonical == https://shosho.tw/blog/zone-2-common-questions/
- [O4] twitter:card 存在 — summary
- [H3] H 結構合理（內文 ≥ 1 個 H2） — 10 個 H2
- [I2] alt 長度 < 125 字符 — 全 12 張 alt 在 125 字內
- [I4] lazy loading 覆蓋（首屏外 ≥ 80%） — 7/9 = 78%
- [I5] WebP/AVIF modern format 比例 ≥ 50% — 2/8 = 25%
- [S3] external links ≥ 1（權威源） — 16 external links
- [SC2] BreadcrumbList schema 存在 — 無 BreadcrumbList
- [SC3] Author schema（E-E-A-T 強化） — author=Mad url=https://shosho.tw/author/mad/
- [L7] E-E-A-T Authoritativeness：被引用 / 外部 mention 提示 — 內文提及「我發布那支破百萬觀看」、「距離我發布...正好滿兩年啦」，隱示頻道知名度；未見明確外部引用標記或媒體 mention。

## 5. PageSpeed Insights Summary

- **Performance**: — (mobile)
- **SEO**: —
- **Best Practices**: —
- **Accessibility**: —

Core Web Vitals:
- LCP: —
- INP: — (CrUX 無 field data)
- CLS: —

## 6. GSC Ranking（last 28 days）

跳過：skipped (GSC_PROPERTY_SHOSHO not set)

## 7. Internal Link Suggestions（via Robin KB）

跳過：skipped (--no-kb)
