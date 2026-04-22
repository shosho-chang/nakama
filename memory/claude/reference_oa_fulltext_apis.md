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

## PMC 直接 PDF 下載

- URL pattern：`https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/pdf/`
- 只有 OA-licensed 論文能 200 OK；non-OA 會 403/302
- content-type 檢查 `pdf` 關鍵字判斷真 PDF / 還是 HTML landing page
- 下載後用檔案大小 > 1KB 當 sanity check

## Unpaywall（DOI → OA 版本）

- Endpoint：`https://api.unpaywall.org/v2/{doi}?email={email}`
- **Email 是 rate-limit 聯絡用，不是 registration、不會 spam**
- 回應 JSON 的 `best_oa_location.url_for_pdf` 是 OA PDF URL
- 404 表示該 DOI 沒 OA 版本
- 涵蓋率比 PMC 廣（~50-60% of biomedical literature）

## 實務流程

1. 先拿 DOI + PMCID（1 個 API call）
2. 有 PMCID → 先試 PMC 直連 PDF（最可靠）
3. 失敗或無 PMCID → Unpaywall（較廣但非 100%）
4. 都失敗 → 標 `needs_manual`（給 DOI link 讓 user 自己找）

## 實作位置

`agents/robin/pubmed_fulltext.py` 實作上述三層 fallback，`KB/Attachments/pubmed/{pmid}.pdf` 存檔。
