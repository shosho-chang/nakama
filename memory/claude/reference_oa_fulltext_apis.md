---
name: Open Access 全文 API 使用模式
description: PMC + Unpaywall 合法下載學術論文 OA PDF 的 API 細節
type: reference
originSessionId: ea82060e-3d51-44bc-a470-e61162514715
---
PubMed 以外的學術 OA 全文取得方案（合法、免錢、不走 sci-hub）。

## NCBI E-utilities（PMC ID + DOI 查詢）

- Endpoint：`https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&rettype=xml`
- XML 裡抓 `<ArticleId IdType="doi">` + `<ArticleId IdType="pmc">`
- Rate limit：無 key 3/s、有 `PUBMED_API_KEY` 10/s
- User-Agent 建議帶 email 聯絡方式

## PMC NCBI 直接 PDF 下載（已不可靠）

- URL pattern：`https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/pdf/`
- **2024 後常回 HTML landing page**（redirect 鏈到 `pmc.ncbi.nlm.nih.gov/articles/.../pdf/xxx.pdf` 但 content-type 是 `text/html`）
- content-type 檢查 `pdf` 是唯一可靠判斷
- 保留這條路是為了極少數仍 work 的 case，但實務上大部分 OA 文章這條會掛

## Europe PMC 鏡像（推薦優先於 PMC NCBI）

- URL pattern：`https://europepmc.org/articles/PMC{pmcid}?pdf=render`
- 經 302 redirect 到 `europepmc.org/api/getPdf?pmcid=PMC{id}` → 直接 `application/pdf`
- **不需過 publisher IdP cookie flow**，VPS 固定 IP 友善
- EBI/EMBL-EBI 官方鏡像，涵蓋所有 PMC OA 論文

## Unpaywall（DOI → OA 版本）

- Endpoint：`https://api.unpaywall.org/v2/{doi}?email={email}`
- **Email 是 rate-limit 聯絡用，不是 registration、不會 spam**
- 回應 JSON 的 `best_oa_location.url_for_pdf` 是 OA PDF URL
- 404 表示該 DOI 沒 OA 版本
- 涵蓋率比 PMC 廣（~50-60% of biomedical literature）
- **陷阱**：publisher 版 URL（e.g. `www.nature.com/articles/*.pdf`）通常要過 publisher IdP cookie flow（`idp.nature.com/authorize` → set cookie → 302 → 最終 GCS）。VPS 固定 IP 常被 publisher CDN 認 bot 擋下，本機能 work 但 VPS 失敗。優先走 Europe PMC 可避開

## 實務流程（4 層 fallback，PR #84 2026-04-23 後）

1. NCBI efetch XML 抓 DOI + PMCID（1 個 API call）
2. 有 PMCID → PMC NCBI `/pdf/`（通常失敗但快速試）
3. PMC NCBI 失敗 → Europe PMC（**主力**：VPS 友善、回真 PDF）
4. 還失敗 + 有 DOI → Unpaywall（較廣，但 publisher URL 在 VPS 可能被擋）
5. 都失敗 → 標 `needs_manual`（給 DOI link 讓 user 自己找）

## 實作位置

`agents/robin/pubmed_fulltext.py` 實作上述三層 fallback，`KB/Attachments/pubmed/{pmid}.pdf` 存檔。
