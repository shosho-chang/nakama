"""Robin agent 基本測試。"""

from pathlib import Path

from shared.utils import extract_frontmatter, slugify


def test_slugify_ascii():
    assert slugify("Hello World") == "Hello-World"


def test_slugify_chinese():
    assert slugify("AI 驅動的 Longevity 策略") == "AI-驅動的-Longevity-策略"


def test_slugify_special_chars():
    assert slugify("Test: A/B (v2)") == "Test-AB-v2"


def test_extract_frontmatter():
    content = """---
title: Test
type: source
---

Hello body"""
    fm, body = extract_frontmatter(content)
    assert fm["title"] == "Test"
    assert fm["type"] == "source"
    assert body == "Hello body"


def test_extract_frontmatter_no_fm():
    content = "Just a plain file"
    fm, body = extract_frontmatter(content)
    assert fm == {}
    assert body == "Just a plain file"
