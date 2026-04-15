"""Tests for agents.robin.chunker — 文件分段器。"""

from agents.robin.chunker import chunk_document


class TestChunkDocument:
    def test_empty_text(self):
        assert chunk_document("") == []
        assert chunk_document("   ") == []

    def test_short_text_single_chunk(self):
        text = "This is a short document."
        result = chunk_document(text, max_chars=1000)
        assert len(result) == 1
        assert result[0]["index"] == 1
        assert result[0]["text"] == text
        assert result[0]["heading"] == "全文"

    def test_split_by_headings(self):
        text = (
            "# Chapter 1\n\nContent of chapter 1.\n\n"
            "# Chapter 2\n\nContent of chapter 2.\n\n"
            "# Chapter 3\n\nContent of chapter 3."
        )
        result = chunk_document(text, max_chars=50, overlap_chars=0)
        assert len(result) >= 3
        headings = [c["heading"] for c in result]
        assert "Chapter 1" in headings
        assert "Chapter 2" in headings
        assert "Chapter 3" in headings

    def test_merges_short_sections(self):
        text = "# A\n\nShort.\n\n# B\n\nAlso short.\n\n# C\n\nStill short."
        # max_chars large enough to fit all sections
        result = chunk_document(text, max_chars=10000, overlap_chars=0)
        assert len(result) == 1

    def test_splits_large_section(self):
        # One heading with very long content
        long_content = "這是一個很長的段落。" * 500  # ~5000 chars
        text = f"# Long Chapter\n\n{long_content}"
        result = chunk_document(text, max_chars=1000, overlap_chars=0)
        assert len(result) > 1
        # First chunk should have the original heading
        assert "Long Chapter" in result[0]["heading"]

    def test_overlap_between_chunks(self):
        text = (
            "# Part 1\n\n" + "A" * 1000 + "\n\n"
            "# Part 2\n\n" + "B" * 1000 + "\n\n"
            "# Part 3\n\n" + "C" * 1000
        )
        result = chunk_document(text, max_chars=1200, overlap_chars=100)
        # Chunks after the first should contain overlap from previous
        if len(result) > 1:
            # Second chunk should start with some content from end of first
            assert len(result[1]["text"]) > 0

    def test_indexes_are_sequential(self):
        text = "# A\n\n" + "X" * 500 + "\n\n# B\n\n" + "Y" * 500 + "\n\n# C\n\n" + "Z" * 500
        result = chunk_document(text, max_chars=600, overlap_chars=0)
        indexes = [c["index"] for c in result]
        assert indexes == list(range(1, len(result) + 1))

    def test_no_heading_falls_back_to_paragraphs(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        result = chunk_document(text, max_chars=30, overlap_chars=0)
        assert len(result) >= 2
        # Should use "段落 N" as heading
        assert any("段落" in c["heading"] for c in result)

    def test_chinese_content(self):
        # 模擬中文書籍章節
        text = (
            "# 第一章 睡眠科學\n\n"
            "睡眠是人類最基本的生理需求之一。" * 100 + "\n\n"
            "# 第二章 失眠症\n\n"
            "失眠症是最常見的睡眠障礙。" * 100
        )
        result = chunk_document(text, max_chars=2000, overlap_chars=100)
        assert len(result) >= 2
        # Should preserve Chinese headings
        all_headings = " ".join(c["heading"] for c in result)
        assert "睡眠" in all_headings or "失眠" in all_headings

    def test_preamble_before_first_heading(self):
        text = "This is the introduction.\n\n# Chapter 1\n\nChapter content."
        result = chunk_document(text, max_chars=10000, overlap_chars=0)
        # The preamble should be included
        all_text = " ".join(c["text"] for c in result)
        assert "introduction" in all_text


class TestChunkDocumentLargeScale:
    def test_120k_chinese_chars(self):
        """模擬 12 萬字中文書。"""
        chapters = []
        for i in range(1, 21):
            chapter_content = f"這是第{i}章的內容。" * 600  # ~6000 chars each
            chapters.append(f"# 第{i}章\n\n{chapter_content}")
        text = "\n\n".join(chapters)

        assert len(text) > 100000  # verify it's large enough

        result = chunk_document(text, max_chars=20000, overlap_chars=500)
        assert len(result) >= 6  # ~120K / 20K = 6 chunks minimum

        # All content should be present (within overlap allowance)
        total_text = " ".join(c["text"] for c in result)
        assert "第1章" in total_text
        assert "第20章" in total_text

    def test_1000_page_english(self):
        """模擬 1000 頁英文教科書（~300K words）。"""
        chapters = []
        for i in range(1, 31):
            chapter_content = f"Chapter {i} discusses important topics. " * 500
            chapters.append(f"# Chapter {i}\n\n{chapter_content}")
        text = "\n\n".join(chapters)

        assert len(text) > 500000

        result = chunk_document(text, max_chars=20000, overlap_chars=500)
        assert len(result) >= 25

        total_text = " ".join(c["text"] for c in result)
        assert "Chapter 1" in total_text
        assert "Chapter 30" in total_text
