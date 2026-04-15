"""文件分段器 — 將大文件切成適合 LLM 處理的 chunks。

優先按文件結構（markdown headings）切分，
超過 max_chars 的段落再做二次切割（段落 → 句子 → hard split）。
"""

import re

from shared.log import get_logger

logger = get_logger("nakama.robin.chunker")

# Markdown heading pattern（# 到 ######）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# 中文句尾標點
_ZH_SENTENCE_END = re.compile(r"[。！？；]")

# 英文句尾
_EN_SENTENCE_END = re.compile(r"\.\s")


def chunk_document(
    text: str,
    *,
    max_chars: int = 20000,
    overlap_chars: int = 500,
) -> list[dict]:
    """將文件切成 chunks，優先按章節結構切分。

    Args:
        text:          完整文件文字
        max_chars:     每個 chunk 的最大字元數（預設 20,000）
        overlap_chars: chunk 之間的重疊字元數（預設 500）

    Returns:
        list of {"index": int, "text": str, "heading": str}
        - index: 從 1 開始的序號
        - text:  chunk 內容
        - heading: 此 chunk 的標題（來自最近的 heading，或 "段落 N"）
    """
    if not text or not text.strip():
        return []

    # 如果文件夠短，直接回傳單一 chunk
    if len(text) <= max_chars:
        return [{"index": 1, "text": text, "heading": "全文"}]

    # Step 1: 嘗試按 heading 切分
    sections = _split_by_headings(text)

    # Step 2: 合併過短的 sections，切割過長的 sections
    chunks = _balance_sections(sections, max_chars=max_chars, overlap_chars=overlap_chars)

    # Step 3: 加上 index
    for i, chunk in enumerate(chunks, 1):
        chunk["index"] = i

    logger.info(f"文件分段完成：{len(chunks)} chunks（原文 {len(text):,} 字元）")
    return chunks


def _split_by_headings(text: str) -> list[dict]:
    """按 markdown heading 切分文件。

    Returns:
        list of {"text": str, "heading": str, "level": int}
    """
    headings = list(_HEADING_RE.finditer(text))

    if not headings:
        # 沒有 heading，按雙換行切段落
        return _split_by_paragraphs(text)

    sections = []

    # 第一個 heading 之前的內容（如果有）
    if headings[0].start() > 0:
        preamble = text[: headings[0].start()].strip()
        if preamble:
            sections.append({"text": preamble, "heading": "前言", "level": 0})

    # 每個 heading 到下一個 heading 之間的內容
    for i, match in enumerate(headings):
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section_text = text[start:end].strip()
        heading_text = match.group(2).strip()
        level = len(match.group(1))

        if section_text:
            sections.append({"text": section_text, "heading": heading_text, "level": level})

    return sections


def _split_by_paragraphs(text: str) -> list[dict]:
    """按雙換行切段落（fallback，無 heading 時使用）。"""
    paragraphs = re.split(r"\n\n+", text)
    sections = []
    for i, para in enumerate(paragraphs, 1):
        para = para.strip()
        if para:
            sections.append({"text": para, "heading": f"段落 {i}", "level": 0})
    return sections


def _balance_sections(
    sections: list[dict],
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[dict]:
    """合併過短、切割過長的 sections，產出最終 chunks。"""
    chunks = []
    buffer_text = ""
    buffer_heading = ""

    for section in sections:
        section_text = section["text"]
        section_heading = section["heading"]

        # 如果單一 section 就超過 max_chars，需要切割
        if len(section_text) > max_chars:
            # 先把 buffer 裡的東西存起來
            if buffer_text.strip():
                chunks.append({"text": buffer_text.strip(), "heading": buffer_heading})
                buffer_text = ""
                buffer_heading = ""

            # 切割大 section
            sub_chunks = _split_large_section(section_text, section_heading, max_chars=max_chars)
            # 加 overlap
            for j, sc in enumerate(sub_chunks):
                if j > 0 and overlap_chars > 0 and chunks:
                    prev_text = chunks[-1]["text"]
                    overlap = prev_text[-overlap_chars:]
                    sc["text"] = overlap + "\n\n" + sc["text"]
                chunks.append(sc)
            continue

        # 嘗試合併到 buffer
        combined = buffer_text + "\n\n" + section_text if buffer_text else section_text
        if len(combined) <= max_chars:
            buffer_text = combined
            if not buffer_heading:
                buffer_heading = section_heading
        else:
            # buffer 滿了，存起來，開始新 buffer
            if buffer_text.strip():
                chunks.append({"text": buffer_text.strip(), "heading": buffer_heading})

                # Overlap: 從上一個 chunk 尾部取 overlap_chars
                if overlap_chars > 0:
                    tail = buffer_text.strip()[-overlap_chars:]
                    buffer_text = tail + "\n\n" + section_text
                else:
                    buffer_text = section_text
                buffer_heading = section_heading
            else:
                buffer_text = section_text
                buffer_heading = section_heading

    # 別忘了最後的 buffer
    if buffer_text.strip():
        chunks.append({"text": buffer_text.strip(), "heading": buffer_heading})

    return chunks


def _split_large_section(text: str, heading: str, *, max_chars: int) -> list[dict]:
    """切割超過 max_chars 的單一 section。

    優先在段落邊界切，其次在句子邊界切，最後 hard split。
    """
    chunks = []
    remaining = text
    part = 1

    while len(remaining) > max_chars:
        chunk_text = remaining[:max_chars]

        # 嘗試在段落邊界切
        cut = chunk_text.rfind("\n\n")
        if cut > max_chars * 0.5:
            chunk_text = remaining[:cut]
            remaining = remaining[cut:].lstrip("\n")
        else:
            # 嘗試在中文句尾切
            match = None
            for m in _ZH_SENTENCE_END.finditer(chunk_text):
                if m.end() > max_chars * 0.5:
                    match = m
            if match:
                chunk_text = remaining[: match.end()]
                remaining = remaining[match.end() :]
            else:
                # 嘗試在英文句尾切
                match = None
                for m in _EN_SENTENCE_END.finditer(chunk_text):
                    if m.end() > max_chars * 0.5:
                        match = m
                if match:
                    chunk_text = remaining[: match.end()]
                    remaining = remaining[match.end() :]
                else:
                    # Hard split
                    chunk_text = remaining[:max_chars]
                    remaining = remaining[max_chars:]

        sub_heading = f"{heading}（第 {part} 部分）" if part > 1 else heading
        chunks.append({"text": chunk_text.strip(), "heading": sub_heading})
        part += 1

    # 剩餘的
    if remaining.strip():
        sub_heading = f"{heading}（第 {part} 部分）" if part > 1 else heading
        chunks.append({"text": remaining.strip(), "heading": sub_heading})

    return chunks
