"""PDF → Markdown 轉換。

- 本地 PDF：pymupdf4llm（正文版面）+ pdfplumber（精確表格，可選）
- 遠端 URL：Firecrawl API
"""

import os
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.shared.pdf_parser")


def parse_pdf(file_path: str | Path, *, with_tables: bool = False) -> str:
    """將本地 PDF 轉為 LLM-ready Markdown。

    Args:
        file_path:   PDF 檔案路徑
        with_tables: True 時追加 pdfplumber 精確表格（研究論文建議啟用）

    Returns:
        Markdown 格式的全文文字

    Raises:
        FileNotFoundError: 檔案不存在
        ValueError: 不是 PDF 檔案
        RuntimeError: 解析失敗
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF 檔案不存在：{file_path}")
    if file_path.suffix.lower() != ".pdf":
        raise ValueError(f"非 PDF 檔案：{file_path.suffix}")

    try:
        import pymupdf4llm
    except ImportError as e:
        raise RuntimeError("pymupdf4llm 未安裝。請執行：pip install pymupdf4llm") from e

    logger.info(f"開始解析 PDF：{file_path.name}（with_tables={with_tables}）")

    try:
        md_text = pymupdf4llm.to_markdown(str(file_path))
    except Exception as e:
        raise RuntimeError(f"PDF 解析失敗：{e}") from e

    # 基本清理：移除多餘空行
    lines = md_text.split("\n")
    cleaned = []
    blank_count = 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    result = "\n".join(cleaned).strip()

    if with_tables:
        tables_md = extract_tables(file_path)
        if tables_md:
            result = result + "\n\n---\n\n## 表格（pdfplumber 精確版）\n\n" + tables_md
            logger.info(f"已附加 {tables_md.count('|---|')} 個精確表格")

    logger.info(f"PDF 解析完成：{len(result):,} 字元")
    return result


def extract_tables(file_path: str | Path) -> str:
    """用 pdfplumber 提取 PDF 中所有表格，回傳 Markdown 格式字串。

    適合含大量資料表的研究論文和教科書。

    Args:
        file_path: PDF 檔案路徑

    Returns:
        所有表格的 Markdown 字串（表格間以水平線分隔），無表格時回傳空字串
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError("pdfplumber 未安裝。請執行：pip install pdfplumber") from e

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF 檔案不存在：{file_path}")

    table_blocks: list[str] = []
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                tables = page.extract_tables()
                for table in tables:
                    md = _table_to_markdown(table)
                    if md:
                        table_blocks.append(f"*第 {page_num} 頁*\n\n{md}")
    except Exception as e:
        logger.warning(f"pdfplumber 表格提取失敗：{e}")
        return ""

    return "\n\n---\n\n".join(table_blocks)


def _table_to_markdown(table: list[list]) -> str:
    """將 pdfplumber 原始表格（list of list）轉為 Markdown 表格。"""
    if not table:
        return ""
    # 清理 None，過濾完全空白的行
    rows = [[str(cell or "").strip().replace("\n", " ") for cell in row] for row in table]
    rows = [r for r in rows if any(c for c in r)]
    if len(rows) < 2:
        return ""

    header = rows[0]
    col_count = len(header)
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * col_count) + " |",
    ]
    for row in rows[1:]:
        # 補齊欄位數
        padded = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(padded[:col_count]) + " |")

    return "\n".join(lines)


def parse_pdf_url(url: str, *, mode: str = "auto") -> str:
    """將 Web 上的 PDF 轉為 Markdown（使用 Firecrawl）。

    需要 FIRECRAWL_API_KEY 環境變數。

    Args:
        url:  PDF 的 URL
        mode: 解析模式 — "auto" / "fast" / "ocr"

    Returns:
        Markdown 格式的全文文字

    Raises:
        RuntimeError: Firecrawl API 呼叫失敗或未設定 API key
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY 未設定，無法解析遠端 PDF")

    try:
        from firecrawl import FirecrawlApp
    except ImportError as e:
        raise RuntimeError("firecrawl-py 未安裝。請執行：pip install firecrawl-py") from e

    logger.info(f"透過 Firecrawl 解析遠端 PDF：{url}（mode={mode}）")

    try:
        app = FirecrawlApp(api_key=api_key)
        result = app.scrape_url(
            url,
            params={
                "formats": ["markdown"],
                "parsers": [{"type": "pdf", "mode": mode}],
            },
        )
        md_text = result.markdown or ""
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Firecrawl PDF 解析失敗：{e}") from e

    logger.info(f"遠端 PDF 解析完成：{len(md_text):,} 字元")
    return md_text


def get_pdf_page_count(file_path: str | Path) -> int:
    """取得 PDF 的頁數（用於費用預估）。"""
    file_path = Path(file_path)
    try:
        import pymupdf

        doc = pymupdf.open(str(file_path))
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0
