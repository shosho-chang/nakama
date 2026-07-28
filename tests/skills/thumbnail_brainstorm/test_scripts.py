# ruff: noqa: E501  — fixture 標題與錯誤訊息含 CJK 長行。
"""thumbnail-brainstorm skill scripts 測試（ADR-054 D8/D9，issue #1069）。

Coverage:
- guest_cutout.sample：機位驗證 fail loud（占比不足 → ValueError、funnel 不被呼叫）
- guest_cutout.sample happy path：驗證過 → funnel 收到 window、回傳 JSON-ready dicts
- guest_cutout.finalize：檔名 = cutout_filename("guest", i, emotion)、落 vault podcast 目錄
- render_still.ensure_recipe_supported：youtube_book fail loud 附指引、未知配方 ValueError
- attach_packages：中間態 packages.json 回填 3 package → 整檔過 S1 validator、雙落點
- attach_packages：CJK PNG 檔名被 schema 擋（整檔驗證 → 兩份都不落）
- thumbnail_playbook：PlaybookPairing 帶 why_they_pair 且 compact formatter 有印
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO / ".claude" / "skills" / "thumbnail-brainstorm" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


guest_cutout = _load("guest_cutout")
render_still = _load("render_still")
attach_packages = _load("attach_packages")


# ---------------------------------------------------------------------------
# guest_cutout.sample — 機位交叉驗證
# ---------------------------------------------------------------------------


def _words_fixture(dominant: int, fraction: float, n: int = 10) -> tuple[list[dict], list[int]]:
    """窗 [0, n) 內 dominant speaker 占 fraction、其餘給另一人。"""
    k = round(n * fraction)
    words = [{"start": float(i), "end": float(i) + 0.9} for i in range(n)]
    speakers = [dominant] * k + [1 - dominant] * (n - k)
    return words, speakers


def test_sample_cam_mismatch_fails_loud_before_funnel(monkeypatch, tmp_path):
    words, speakers = _words_fixture(dominant=0, fraction=0.9)
    monkeypatch.setattr(guest_cutout, "load_word_speakers", lambda _d: (words, speakers))

    funnel_called = False

    async def _fake_run(*a, **k):
        nonlocal funnel_called
        funnel_called = True
        return []

    import shared.thumbnail_funnel as tf

    monkeypatch.setattr(tf, "run", _fake_run)

    with pytest.raises(ValueError, match="director.json"):
        asyncio.run(
            guest_cutout.sample(
                tmp_path, tmp_path / "cam.mp4", (0.0, 10.0), expected_speaker=1, out_dir=tmp_path
            )
        )
    assert not funnel_called


def test_sample_happy_path_passes_window_to_funnel(monkeypatch, tmp_path):
    words, speakers = _words_fixture(dominant=1, fraction=0.8)
    monkeypatch.setattr(guest_cutout, "load_word_speakers", lambda _d: (words, speakers))

    seen: dict = {}

    async def _fake_run(video_path, out_dir, *, mode, window):
        seen.update(video=video_path, out_dir=out_dir, mode=mode, window=window)
        return [
            SimpleNamespace(
                path=tmp_path / "frame_001.png",
                timestamp_sec=3.2,
                sample_kind="periodic",
                sharpness=812.5,
            )
        ]

    import shared.thumbnail_funnel as tf

    monkeypatch.setattr(tf, "run", _fake_run)

    result = asyncio.run(
        guest_cutout.sample(
            tmp_path, tmp_path / "cam.mp4", (0.0, 10.0), expected_speaker=1, out_dir=tmp_path
        )
    )
    assert seen["mode"] == "expression_sample"
    assert seen["window"] == (0.0, 10.0)
    assert result == [
        {
            "path": str(tmp_path / "frame_001.png"),
            "timestamp_sec": 3.2,
            "sample_kind": "periodic",
            "sharpness": 812.5,
        }
    ]


# ---------------------------------------------------------------------------
# guest_cutout.finalize — emotion 檔名 + vault 落點
# ---------------------------------------------------------------------------


def test_finalize_names_file_with_emotion(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    frame = tmp_path / "picked.png"
    frame.write_bytes(b"png")

    async def _fake_exec(*argv, **kwargs):
        Path(argv[argv.index("-o") + 1]).write_bytes(b"cutout")

        async def _comm():
            return b"", b""

        return SimpleNamespace(returncode=0, communicate=_comm)

    monkeypatch.setattr(guest_cutout.asyncio, "create_subprocess_exec", _fake_exec)

    dst = asyncio.run(guest_cutout.finalize(frame, "思考", "20260723-xieboran", 2))
    assert (
        dst
        == vault
        / "Attachments"
        / "cutouts"
        / "podcast"
        / "20260723-xieboran"
        / "guest_v2_thoughtful.png"
    )
    assert dst.exists()


def test_finalize_rejects_unknown_emotion(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    frame = tmp_path / "picked.png"
    frame.write_bytes(b"png")
    with pytest.raises(ValueError):
        asyncio.run(guest_cutout.finalize(frame, "憂鬱", "ep", 1))


# ---------------------------------------------------------------------------
# render_still — visual_recipe routing fail loud
# ---------------------------------------------------------------------------


def test_youtube_book_fails_loud_with_guidance():
    with pytest.raises(NotImplementedError, match="Attachments/cutouts/reference/youtube_book"):
        render_still.ensure_recipe_supported("youtube_book")


def test_unknown_recipe_rejected():
    with pytest.raises(ValueError, match="unknown visual_recipe"):
        render_still.ensure_recipe_supported("tiktok_duet")


@pytest.mark.parametrize("recipe", ["podcast", "youtube_host"])
def test_supported_recipes_pass(recipe):
    render_still.ensure_recipe_supported(recipe)


# ---------------------------------------------------------------------------
# attach_packages — 回填 + 整檔驗證 + 雙落點
# ---------------------------------------------------------------------------


def _title(rank: int, panel_note: str | None = None) -> dict:
    return {
        "text": f"標題 rank {rank}",
        "archetype_id": "T-A3",
        "angle_combo": ["反直覺"],
        "payoff": "看完你會改觀",
        "cite": "srt/punch-L1_r003.srt#12",
        "rank": rank,
        "panel_note": panel_note,
    }


def _midstate_packages_file() -> dict:
    """S4 產出的中間態：titles 齊、packages 空（此時整檔驗證會 fail — 合法輸入）。"""
    return {
        "episode": "20260723 謝伯讓",
        "generated_at": "2026-07-27T12:00:00+08:00",
        "cuts": [
            {
                "cut_id": "punch-L1",
                "format": "long",
                "information_origin": "full_text",
                "visual_recipe": "podcast",
                "aspect": "16:9",
                "titles": [
                    _title(1),
                    _title(2),
                    _title(3),
                    _title(4, "角度重複"),
                    _title(5, "過度誇大"),
                ],
                "packages": [],
                "citations": [],
                "brand_flags": [],
                "title_trace_ref": "packaging/punch-L1/title_trace.json",
            }
        ],
    }


def _spec(n: int, png: Path, vault: Path) -> dict:
    return {
        "title_rank": n,
        "thumbnail": str(png),
        "thumb_archetype_id": "T-V8",
        "joint_pairing_id": "JP-1",
        "host_cutout": str(vault / "Attachments" / "cutouts" / "shosho" / "surprised" / "1.png"),
        "guest_cutout": "Attachments/cutouts/podcast/20260723-xieboran/guest_v1_thoughtful.png",
    }


def test_attach_fills_validates_and_dual_lands(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    working = tmp_path / "packaging"
    working.mkdir()
    (working / "packages.json").write_text(
        json.dumps(_midstate_packages_file(), ensure_ascii=False), encoding="utf-8"
    )

    specs = []
    for n in (1, 2, 3):
        png = working / f"pkg-punch-L1-{n}.png"
        png.write_bytes(b"png")
        specs.append(_spec(n, png, vault))

    out = attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)

    from shared.schemas.packaging import parse_packages

    for path in (working / "packages.json", out):
        parsed = parse_packages(path)
        pkgs = parsed.cuts[0].packages
        assert [p.title_rank for p in pkgs] == [1, 2, 3]
        assert pkgs[0].thumbnail_png == "Attachments/packaging/20260723-xieboran/pkg-punch-L1-1.png"
        assert pkgs[0].host_cutout == "Attachments/cutouts/shosho/surprised/1.png"
    for n in (1, 2, 3):
        assert (
            vault / "Attachments" / "packaging" / "20260723-xieboran" / f"pkg-punch-L1-{n}.png"
        ).exists()


def test_attach_rejects_cjk_png_and_lands_nothing(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    working = tmp_path / "packaging"
    working.mkdir()
    original = json.dumps(_midstate_packages_file(), ensure_ascii=False)
    (working / "packages.json").write_text(original, encoding="utf-8")

    specs = []
    for n, name in enumerate(["pkg-1.png", "pkg-2.png", "謝伯讓封面.png"], start=1):
        png = working / name
        png.write_bytes(b"png")
        specs.append(_spec(n, png, vault))

    with pytest.raises(Exception, match="PNG filename must match"):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)

    assert (working / "packages.json").read_text(encoding="utf-8") == original
    vault_dir = vault / "Attachments" / "packaging" / "20260723-xieboran"
    assert not (vault_dir / "packages.json").exists()
    assert not any(vault_dir.glob("*.png"))


def test_attach_rejects_cutout_outside_vault(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    with pytest.raises(ValueError, match="vault"):
        attach_packages.to_vault_relative(str(tmp_path / "elsewhere" / "x.png"), vault)


# ---------------------------------------------------------------------------
# thumbnail_playbook — why_they_pair 補齊
# ---------------------------------------------------------------------------


def test_pairings_carry_why_they_pair_and_formatter_prints_it():
    from shared.thumbnail_playbook import (
        format_playbook_index_for_prompt,
        load_playbook_index,
    )

    load_playbook_index.cache_clear()
    index = load_playbook_index()
    assert index.joint_pairings, "playbook_data_v1.json 應含 joint_pairings"
    with_why = [p for p in index.joint_pairings if p.why_they_pair]
    assert with_why, "至少一組 pairing 應帶 why_they_pair（JSON 內有此欄位）"
    text = format_playbook_index_for_prompt(index)
    # 注入時截斷至 140 字守 size budget — 驗前綴即可
    assert with_why[0].why_they_pair[:100] in text
