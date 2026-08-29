"""Reader typography controls (修修 2026-08-29).

字級 ± 與字體選擇活在 Reader 工具列。這裡鎖三件會靜默壞掉的事：
控制項存在、reader 模組帶得到 cache-busting 戳記、字體資產真的在 repo 裡。
（實際套用效果活在 foliate iframe，靠瀏覽器驗證，不在單元測試範圍。）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.templating import Jinja2Templates

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = Jinja2Templates(directory=str(_ROOT / "thousand_sunny" / "templates"))


def _render() -> str:
    book = SimpleNamespace(
        book_id="demo",
        title="測試書",
        author="作者",
        mode="monolingual-zh",
        book_version_hash="abc123",
        ingest_status="none",
    )
    tpl = _TEMPLATES.get_template("robin/book_reader.html")
    return tpl.render(book=book, asset_version="deadbeef")


class TestToolbarControls:
    def test_font_size_and_family_controls_render(self):
        body = _render()
        assert 'id="fontSmaller"' in body and 'id="fontLarger"' in body
        assert 'id="fontSizeLabel"' in body
        assert 'id="fontFamily"' in body
        assert 'class="reader-type"' in body

    def test_reader_module_is_cache_busted(self):
        """The reader module used to be linked bare, so a JS fix could sit behind a
        stale edge cache forever (same class of bug as bridge-weekly.js)."""
        body = _render()
        assert "/static/book_reader.js?v=deadbeef" in body


class TestAssetVersionCoversReaderFiles:
    def test_hash_inputs_include_reader_css_and_js(self):
        from thousand_sunny.routers.robin import _shosho_asset_version

        static = _ROOT / "thousand_sunny" / "static"
        before = _shosho_asset_version()
        # touching either reader asset must move the hash, else edge caches go stale
        for target in (static / "shosho" / "book_reader.css", static / "book_reader.js"):
            original = target.read_bytes()
            try:
                target.write_bytes(original + b"\n/* cache-bust probe */\n")
                assert _shosho_asset_version() != before, f"{target.name} not in the hash"
            finally:
                target.write_bytes(original)
        assert _shosho_asset_version() == before  # restored


class TestFontAssets:
    def test_line_seed_webfont_files_exist(self):
        """The reader injects @font-face for these into the chapter iframe — a missing
        file means the picker offers a font that silently falls back."""
        fonts = _ROOT / "thousand_sunny" / "static" / "shosho" / "fonts"
        for stem in ("LINESeedTW_Rg", "LINESeedTW_Bd"):
            assert (fonts / f"{stem}.woff2").exists()
            assert (fonts / f"{stem}.woff").exists()

    def test_injected_font_url_is_absolute(self):
        """Chapters live in blob: documents where a root-relative /static/... never
        resolves — the injected @font-face must carry location.origin."""
        js = (_ROOT / "thousand_sunny" / "static" / "book_reader.js").read_text(encoding="utf-8")
        assert "${location.origin}/static/shosho/fonts/" in js


class TestMobileReading:
    """修修 2026-08-29 手機閱讀：翻頁、精簡工具列（字級／目錄／明暗）、選取即標註。"""

    def test_mobile_only_keeps_three_controls(self):
        body = _render()
        # 手機隱藏的：首頁 / Ingest / 觀看筆記 / 反思 / 快捷鍵 / 刪除 / 字體選單
        hidden = ("back-inbox", "ingest-wrap", "commentsToggle", "kbdHelpBtn", "deleteBookBtn")
        for marker in hidden:
            idx = body.find(marker)
            assert idx != -1, marker
            # the desktop-only class must sit on the same tag as the marker
            tag_start = body.rfind("<", 0, idx)
            tag_end = body.find(">", idx)
            assert "desktop-only" in body[tag_start:tag_end], f"{marker} not desktop-only"
        # 保留的三項 + 返回書架
        assert 'id="fontSmaller"' in body and 'id="fontLarger"' in body
        assert 'id="tocToggle"' in body
        assert "desktop-only" not in body.split('id="tocToggle"')[0][-120:]
        assert "data-theme-toggle-slot" in body  # 明暗切換掛進工具列

    def test_mobile_css_hides_desktop_only_controls(self):
        css = (_ROOT / "thousand_sunny" / "static" / "shosho" / "book_reader.css").read_text(
            encoding="utf-8"
        )
        assert "@media (max-width: 768px)" in css
        assert ".desktop-only { display: none" in css

    def test_reader_paginates_on_mobile_too(self):
        """手機原本是 scrolled（捲動）；改為一律 paginated，單欄。"""
        js = (_ROOT / "thousand_sunny" / "static" / "book_reader.js").read_text(encoding="utf-8")
        assert "setAttribute('flow', 'scrolled')" not in js
        assert "setAttribute('flow', 'paginated')" in js

    def test_selection_popup_listens_to_selectionchange(self):
        """手機長按選字不會送 pointerup —— 少了 selectionchange 就永遠不跳標註選單。"""
        js = (_ROOT / "thousand_sunny" / "static" / "book_reader.js").read_text(encoding="utf-8")
        assert "addEventListener('selectionchange'" in js
        assert "pointercancel" in js  # 長按被選取 UI 接手時要解除 pointerDown
