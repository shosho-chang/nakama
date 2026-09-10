"""Tests for shared.wikilink — the single Obsidian wikilink parser.

Regression origin (修修 2026-09-10): a project named ``[Pod] 蘇予昕`` was written
back as ``[[[Pod] 蘇予昕]]`` and read by three independent parsers, which returned
``Pod] 蘇予昕``, the raw string, and ``Pod] 蘇予昕`` respectively — none matching
the real file. The task page grew a 「找不到專案檔」 ghost option and a second
reassign stacked the filename prefix.
"""

from __future__ import annotations

import pytest

from shared.wikilink import strip_wikilink


class TestStripWikilink:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("[[電子報]]", "電子報"),
            ("  [[電子報]]  ", "電子報"),
            ("[[Projects/肌酸的妙用]]", "Projects/肌酸的妙用"),  # path kept; caller trims
            ("[[Projects/肌酸的妙用|肌酸]]", "Projects/肌酸的妙用"),  # alias dropped
            ("[[電子報|本週]]", "電子報"),
            ("電子報", "電子報"),  # bare name, no link
            ("見 [[電子報]] 的說明", "電子報"),  # embedded link in prose
        ],
    )
    def test_common_shapes(self, raw: str, want: str):
        assert strip_wikilink(raw) == want

    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("[[[Pod] 蘇予昕]]", "[Pod] 蘇予昕"),  # THE regression
            ("[[前綴[中]後綴]]", "前綴[中]後綴"),
            ("[[結尾有括號]]]]", "結尾有括號]]"),  # exactly two off each end
            ("[[[[雙層]]]]", "[[雙層]]"),
        ],
    )
    def test_names_containing_brackets(self, raw: str, want: str):
        """Unwrapping takes exactly two characters off each end — never a
        character class, which is what ate the leading ``[`` before."""
        assert strip_wikilink(raw) == want

    @pytest.mark.parametrize("raw", ["", "   ", None, 123, [], {}, "[[]]", "[[  ]]"])
    def test_blank_and_non_string_give_empty(self, raw):
        assert strip_wikilink(raw) == ""

    def test_unclosed_link_is_returned_verbatim(self):
        # Not a link we can resolve — hand it back rather than guessing.
        assert strip_wikilink("[[未關閉") == "[[未關閉"


def test_all_three_call_sites_agree_on_a_bracketed_name():
    """The bug was three parsers disagreeing. Pin that they no longer can."""
    from gateway.handlers.nami import _task_project
    from shared.project_writer import task_project
    from shared.weekly_indexer import _strip_wikilink

    fm = {"projects": ["[[[Pod] 蘇予昕]]"]}
    assert task_project(fm) == "[Pod] 蘇予昕"
    assert _task_project(fm) == "[Pod] 蘇予昕"
    assert _strip_wikilink("[[[Pod] 蘇予昕]]") == "[Pod] 蘇予昕"
