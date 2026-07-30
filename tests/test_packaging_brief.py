# ruff: noqa: E501  — 錯誤訊息與 fixture 含 CJK 長行。
"""packaging_brief.py 測試 — gate 內容速覽的機械層（修修 2026-07-30）。

Coverage:
- 形狀驗證：one_liner 必填、beats 至少一拍、時間碼格式、quotes 欄位
- 雙落點：working set + vault
- **vault root 不存在時 fail loud**（config `/home` 在 Windows 漏成影子目錄的老坑）
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "packaging_brief.py"


def _load():
    spec = importlib.util.spec_from_file_location("packaging_brief_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _payload(**over) -> dict:
    base = {
        "one_liner": "談該不該把大腦外包給 AI",
        "duration": "10:16",
        "beats": [{"at": "03:40", "what": "改用健康當判準"}],
        "quotes": [{"at": "01:35", "speaker": "謝伯讓", "text": "我們直接把能力外包給AI"}],
    }
    base.update(over)
    return base


class TestShape:
    def test_one_liner_required(self):
        mod = _load()
        with pytest.raises(ValueError, match="one_liner"):
            mod.build_brief(_payload(one_liner="  "), "util-L4")

    def test_beats_required(self):
        mod = _load()
        with pytest.raises(ValueError, match="beats"):
            mod.build_brief(_payload(beats=[]), "util-L4")

    def test_bad_timestamp_rejected(self):
        mod = _load()
        with pytest.raises(ValueError, match="mm:ss"):
            mod.build_brief(_payload(beats=[{"at": "3分40秒", "what": "x"}]), "util-L4")

    def test_quote_needs_text(self):
        mod = _load()
        with pytest.raises(ValueError, match=r"quotes\[0\].text"):
            mod.build_brief(_payload(quotes=[{"at": "01:35", "text": " "}]), "util-L4")

    def test_optional_fields_normalised_to_none(self):
        mod = _load()
        out = mod.build_brief({**_payload(), "duration": "", "caution": "  "}, "util-L4")
        assert out["duration"] is None and out["caution"] is None
        assert out["cut_id"] == "util-L4" and out["generated_at"]


class TestWrite:
    def test_dual_lands(self, tmp_path, monkeypatch):
        mod = _load()
        vault = tmp_path / "vault"
        (vault / "Attachments").mkdir(parents=True)
        monkeypatch.setenv("VAULT_PATH", str(vault))
        work = tmp_path / "packaging"

        written = mod.write_brief(work, "util-L4", "20260723-xieboran", _payload())
        assert len(written) == 2
        for p in written:
            assert json.loads(Path(p).read_text(encoding="utf-8"))["cut_id"] == "util-L4"
        assert (work / "briefs" / "util-L4.json").is_file()

    def test_missing_vault_root_fails_loud_and_creates_nothing(self, tmp_path, monkeypatch):
        r"""config `/home/...` 在 Windows 會被解成 E:\home 影子目錄；
        mkdir(parents=True) 會一路建出來、看似成功但 board 永遠讀不到。"""
        mod = _load()
        ghost = tmp_path / "does-not-exist"
        monkeypatch.setenv("VAULT_PATH", str(ghost))
        with pytest.raises(ValueError, match="vault root 不存在"):
            mod.write_brief(tmp_path / "packaging", "util-L4", "20260723-xieboran", _payload())
        assert not ghost.exists()

    def test_cjk_slug_rejected(self, tmp_path, monkeypatch):
        mod = _load()
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setenv("VAULT_PATH", str(vault))
        with pytest.raises(ValueError, match="ASCII"):
            mod.write_brief(tmp_path / "p", "util-L4", "20260723 謝伯讓", _payload())
