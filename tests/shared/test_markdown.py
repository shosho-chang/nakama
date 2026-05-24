"""Tests for shared.markdown — server-side md→HTML render with sanitize."""

from shared.markdown import render_markdown


class TestBasicRender:
    def test_heading(self):
        html = render_markdown("# Title")
        assert "<h1>Title</h1>" in html

    def test_paragraph(self):
        html = render_markdown("hello world")
        assert "<p>hello world</p>" in html

    def test_bold_em(self):
        html = render_markdown("**bold** and *em*")
        assert "<strong>bold</strong>" in html
        assert "<em>em</em>" in html

    def test_unordered_list(self):
        html = render_markdown("- a\n- b\n")
        assert "<ul>" in html
        assert "<li>a</li>" in html
        assert "<li>b</li>" in html

    def test_ordered_list(self):
        html = render_markdown("1. a\n2. b\n")
        assert "<ol>" in html

    def test_fenced_code(self):
        html = render_markdown("```\ncode\n```")
        assert "<pre>" in html
        assert "<code>" in html
        assert "code" in html

    def test_inline_code(self):
        html = render_markdown("use `x()` here")
        assert "<code>x()</code>" in html

    def test_external_link(self):
        html = render_markdown("[link](https://example.com)")
        assert 'href="https://example.com"' in html

    def test_cjk(self):
        html = render_markdown("# 標題\n\n繁體中文段落")
        assert "標題" in html
        assert "繁體中文段落" in html


class TestSanitize:
    def test_strips_script_tag(self):
        html = render_markdown('<script>alert("xss")</script>safe')
        assert "<script" not in html.lower()
        assert "safe" in html

    def test_strips_event_handler(self):
        html = render_markdown('<a href="x" onclick="bad()">link</a>')
        assert "onclick" not in html

    def test_strips_javascript_url(self):
        html = render_markdown("[click](javascript:alert(1))")
        assert 'href="javascript:' not in html.lower()
        assert "href='javascript:" not in html.lower()

    def test_keeps_safe_inline_html(self):
        html = render_markdown("plain text with <em>emphasis</em>")
        assert "<em>emphasis</em>" in html

    def test_strips_iframe(self):
        html = render_markdown('<iframe src="evil"></iframe>')
        assert "<iframe" not in html


class TestWikilink:
    def test_wikilink_resolved(self):
        def resolver(target):
            return f"/bridge/source/{target}" if target.startswith("pubmed-") else None

        html = render_markdown("see [[pubmed-123]]", wikilink_resolver=resolver)
        assert 'href="/bridge/source/pubmed-123"' in html
        assert ">pubmed-123<" in html

    def test_wikilink_broken_no_resolver(self):
        html = render_markdown("see [[unknown]]")
        assert "wikilink-broken" in html
        assert "unknown" in html
        assert "href=" not in html.split("wikilink-broken")[1].split("</")[0]

    def test_wikilink_broken_returns_none(self):
        html = render_markdown("see [[ghost]]", wikilink_resolver=lambda t: None)
        assert "wikilink-broken" in html

    def test_wikilink_with_display_alias(self):
        def resolver(target):
            return "/bridge/attach" if "Attachments" in target else None

        html = render_markdown(
            "[[KB/Attachments/42.pdf|下載的 PDF]]",
            wikilink_resolver=resolver,
        )
        assert 'href="/bridge/attach"' in html
        assert "下載的 PDF" in html

    def test_wikilink_in_list(self):
        def resolver(target):
            return f"/x/{target}"

        html = render_markdown("- [[a]]\n- [[b]]\n", wikilink_resolver=resolver)
        assert 'href="/x/a"' in html
        assert 'href="/x/b"' in html

    def test_wikilink_does_not_break_inline_code(self):
        html = render_markdown("use `[[literal]]` here")
        assert "<code>[[literal]]</code>" in html
        assert "wikilink-broken" not in html

    def test_wikilink_inside_code_fence_untouched(self):
        text = "```\n[[example]]\n```"
        html = render_markdown(text, wikilink_resolver=lambda t: "/x")
        assert "wikilink-broken" not in html
        assert "[[example]]" in html
