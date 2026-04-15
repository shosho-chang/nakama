"""PDF → Markdown 轉換，使用 PyMuPDF4LLM（本地解析，不需 GPU）。

本地 PDF 使用 pymupdf4llm 解析；
Web URL 上的 PDF 可選擇用 Firecrawl（需 API key）。
"""

import os
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.shared.pdf_parser")


def parse_pdf(file_path: str | Path, *, mode: str = "auto") -> str:
    """將本地 PDF 轉為 LLM-ready Markdown。

    使用 pymupdf4llm 做本地解析，支援文字型和掃描型 PDF。

    Args:
        file_path: PDF 檔案路徑
        mode: 解析模式（目前僅影響 OCR 行為）
              - "auto": 預設，自動偵測是否需要 OCR
              - "fast": 純文字擷取，跳過 OCR
              - "ocr": 強制對所有頁面做 OCR

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

    logger.info(f"開始解析 PDF：{file_path.name}（mode={mode}）")

    try:
        # pymupdf4llm.to_markdown() 回傳 LLM-ready markdown
        # 自動處理 headers、tables、images、multi-column layout
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
    logger.info(f"PDF 解析完成：{len(result):,} 字元")
    return result


def parse_pdf_url(url: str, *, mode: str = "auto") -> str:
    """將 Web 上的 PDF 轉為 Markdown（使用 Firecrawl）。

    需要 FIRECRAWL_API_KEY 環境變數。

    Args:
        url: PDF 的 URL
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
        md_text = result.get("markdown", "")
    except Exception as e:
        raise RuntimeError(f"Firecrawl PDF 解析失敗：{e}") from e

    logger.info(f"遠端 PDF 解析完成：{len(md_text):,} 字元")
    return md_text


def get_pdf_page_count(file_path: str | Path) -> int:
    """取得 PDF 的頁數（用於費用預估）。

    Args:
        file_path: PDF 檔案路徑

    Returns:
        頁數
    """
    file_path = Path(file_path)
    try:
        import pymupdf

        doc = pymupdf.open(str(file_path))
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0
