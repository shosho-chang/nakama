"""PDF bytes → 給 LLM 讀的純文字。

pypdf 抽中文 PDF 常見兩種排版殘渣：字與字之間插空白（「訪 綱 ：」）、以及一行
只有一個字的直排化換行。兩者都只影響排版、不影響內容，清掉後 LLM 讀起來跟原文一樣。
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

# 中日文字 + 全形標點（康熙部首 / CJK 部首補充、CJK 符號、統一表意文字、全形 ASCII）。
# 有些 PDF 會把「一」「又」編成康熙部首字（⼀ U+2F00），也要算進來。
_CJK = r"[\u2e80-\u2fdf\u3000-\u303f\u3400-\u9fff\uff00-\uffef]"


class PdfTextError(Exception):
    """PDF 讀不出文字（加密、壞檔、純圖片掃描檔）。"""


def clean_cjk_text(text: str) -> str:
    # 中文字之間的空白 / 換行都是排版殘渣
    text = re.sub(rf"(?<={_CJK})\s+(?={_CJK})", "", text)
    # 中文與數字 / 英文之間被拆開的換行（「53\n萬」「年\n4」）
    text = re.sub(rf"(?<={_CJK})\n(?=[0-9A-Za-z])|(?<=[0-9A-Za-z])\n(?={_CJK})", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    # 條列符號前補換行，保留清單結構
    text = re.sub(r"\s*●", "\n●", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def pdf_to_text(data: bytes) -> str:
    """抽出 PDF 全部頁面的文字並清理；讀不到任何文字時 raise ``PdfTextError``。"""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PdfTextError("PDF 有密碼保護")
        raw = "\n".join((page.extract_text() or "") for page in reader.pages)
    except PdfTextError:
        raise
    except Exception as e:  # pypdf 對壞檔丟的例外種類很多
        raise PdfTextError(f"PDF 無法解析：{e}") from e
    text = clean_cjk_text(raw)
    if not text:
        raise PdfTextError("PDF 沒有可抽取的文字（可能是掃描圖片）")
    return text
