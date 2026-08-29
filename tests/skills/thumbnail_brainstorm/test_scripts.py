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
import base64
import hashlib
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
render_request = _load("render_request")


# ---------------------------------------------------------------------------
# guest_cutout.sample — 機位交叉驗證
# ---------------------------------------------------------------------------


def test_render_request_preserves_author_book_layer(tmp_path):
    vault = tmp_path / "vault"
    recipe = {
        "book_cover": "Attachments/packaging/episode/book-cover.png",
        "book_cover_opacity": 0.42,
        "book_cover_brightness": 0.38,
        "book_cover_height_pct": 100,
    }

    variables, images = render_request._book_cover_layer(recipe, vault)

    assert images["book_cover_data_url"] == str(
        vault / "Attachments/packaging/episode/book-cover.png"
    )
    assert variables == {
        "book_cover_opacity": 0.42,
        "book_cover_brightness": 0.38,
        "book_cover_height_pct": 100.0,
    }


def test_render_request_builds_lossless_n2_reaction_spec(tmp_path):
    vault = tmp_path / "vault"
    cut_dir = vault / "Attachments" / "cutouts" / "podcast" / "episode"
    request = {
        "composition": "thumbnail_reaction",
        "center_visual_asset": "Attachments/packaging/episode/center-r2.png",
        "center_geometry": {
            "width_pct": 56.5,
            "height_px": 430,
            "x_pct": 52,
            "y_pct": 47.5,
        },
    }
    host = {"height_pct": 140, "x_pct": -26.6, "y_pct": -34.5}
    guest = {"height_pct": 113.8, "x_pct": -25.4, "y_pct": -1.3}

    spec = render_request._build_reaction_spec(
        request,
        vault=vault,
        cut_dir=cut_dir,
        host_name="host.png",
        guest_name="guest.png",
        host=host,
        guest=guest,
    )

    assert spec["images"]["prop_image_data_url"] == str(
        vault / "Attachments/packaging/episode/center-r2.png"
    )
    assert spec["variables"]["prop_width_pct"] == 56.5
    assert spec["variables"]["prop_height_px"] == 430.0
    assert spec["variables"]["prop_center_x_pct"] == 52.0
    assert spec["variables"]["prop_center_y_pct"] == 47.5
    assert spec["variables"]["frame_style"] == "skew"
    assert spec["variables"]["host_height_pct"] == 140


def test_render_request_persists_n2_receipt_and_recipe(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    ep_vault = vault / "Attachments" / "packaging" / "episode"
    cut_dir = vault / "Attachments" / "cutouts" / "podcast" / "episode"
    working = tmp_path / "episode" / "packaging"
    for path in (ep_vault, cut_dir, working):
        path.mkdir(parents=True)
    packages = {
        "episode": "Episode",
        "cuts": [
            {
                "cut_id": "value-L01",
                "titles": [{"rank": 2, "text": "title"}],
                "packages": [
                    {"title_rank": rank, "thumbnail_png": f"old-{rank}.png"}
                    for rank in (1, 2, 3)
                ],
            }
        ],
    }
    for path in (ep_vault / "packages.json", working / "packages.json"):
        path.write_text(json.dumps(packages), encoding="utf-8")
    center = ep_vault / "center-value-L01-r2.png"
    center.write_bytes(b"center")
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text("{}", encoding="utf-8")

    args = SimpleNamespace(
        packaging_dir=working,
        cut_id="value-L01",
        episode_slug="episode",
        out_suffix="",
    )
    request = {
        "composition": "thumbnail_reaction",
        "title_rank": 2,
        "host_cutout": "Attachments/cutouts/podcast/episode/host.png",
        "guest_cutout": "Attachments/cutouts/podcast/episode/guest.png",
        "big_text": [],
        "center_visual_asset": "Attachments/packaging/episode/center-value-L01-r2.png",
        "center_geometry": {"width_pct": 53, "height_px": 455, "x_pct": 50, "y_pct": 50},
        "requested_at": "2026-08-27T00:00:00+00:00",
        "geometry_manual": True,
    }

    def fake_run(command):
        if "render_still.py" in " ".join(command):
            assert "thumbnail_reaction" in command
            (working / "pkg-value-L01-2.png").write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stderr="", stdout="QA PASS\n")

    def fake_plan(**kwargs):
        spec = json.loads(Path(kwargs["spec"]["render_spec"]).read_text(encoding="utf-8"))
        assert spec["composition"] == "thumbnail_reaction"
        return SimpleNamespace(
            payload={
                "thumbnail_png": "Attachments/packaging/episode/pkg-value-L01-2.png",
                "center_visual_asset": (
                    "Attachments/packaging/episode/center-value-L01-r2.png"
                ),
            },
            center_name=center.name,
            center_source=center,
            sidecar_name="pkg-value-L01-2.png.composition.json",
            sidecar_source=sidecar,
            receipt_name="value-L01-r2.json",
        )

    monkeypatch.setattr(render_request, "_run", fake_run)
    monkeypatch.setattr(render_request, "build_receipt_plan", fake_plan)

    result = render_request._render_reaction_request(
        args=args,
        vault=vault,
        ep_vault=ep_vault,
        cut_dir=cut_dir,
        req=request,
        package_rank=2,
        host_name="host.png",
        guest_name="guest.png",
        host={"height_pct": 140, "x_pct": -20, "y_pct": -10},
        guest={"height_pct": 120, "x_pct": -15, "y_pct": 0},
        entry=None,
        approval={"approvals": []},
        approval_path=ep_vault / "approval.json",
    )

    assert result == 0
    assert (ep_vault / "composition_receipts" / "value-L01-r2.json").is_file()
    assert (working / "composition_receipts" / "value-L01-r2.json").is_file()
    for path in (ep_vault / "packages.json", working / "packages.json"):
        saved = json.loads(path.read_text(encoding="utf-8"))
        recipe = saved["cuts"][0]["packages"][1]["render_recipe"]
        assert recipe["composition"] == "thumbnail_reaction"
        assert recipe["center_geometry"]["height_px"] == 455


def test_render_request_updates_only_selected_package_recipe():
    data = {
        "cuts": [
            {
                "cut_id": "full",
                "packages": [
                    {"title_rank": rank, "thumbnail_png": f"old-{rank}.png", "render_recipe": {"title_rank": rank}}
                    for rank in (1, 2, 3)
                ],
            }
        ]
    }
    request = {
        "title_rank": 3,
        "rendered_png": "new-3.png",
        "host_cutout": "host-3.png",
        "guest_cutout": "guest-3.png",
    }

    render_request._update_selected_package(data, "full", 3, request)

    assert data["cuts"][0]["packages"][0]["thumbnail_png"] == "old-1.png"
    assert data["cuts"][0]["packages"][1]["thumbnail_png"] == "old-2.png"
    assert data["cuts"][0]["packages"][2]["thumbnail_png"] == "new-3.png"
    assert data["cuts"][0]["packages"][2]["render_recipe"] == request


def test_render_request_syncs_selected_recipe_to_working_and_vault(tmp_path):
    paths = [tmp_path / "working.json", tmp_path / "vault.json"]
    original = {
        "cuts": [
            {
                "cut_id": "full",
                "packages": [
                    {"title_rank": rank, "thumbnail_png": f"old-{rank}.png"}
                    for rank in (1, 2, 3)
                ],
            }
        ]
    }
    for path in paths:
        path.write_text(json.dumps(original), encoding="utf-8")
    request = {
        "title_rank": 3,
        "rendered_png": "new-3.png",
        "host_cutout": "host-3.png",
        "guest_cutout": "guest-3.png",
    }

    render_request._write_selected_package(paths, "full", 3, request)

    results = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert results[0] == results[1]
    assert results[0]["cuts"][0]["packages"][0]["thumbnail_png"] == "old-1.png"
    assert results[0]["cuts"][0]["packages"][2]["render_recipe"] == request


def test_render_request_ignores_null_legacy_approval_request():
    request = {"requested_at": "2026-08-21T08:05:28+00:00"}

    assert not render_request._matches_legacy_approval_request(
        {"render_request": None}, request
    )
    assert render_request._matches_legacy_approval_request(
        {"render_request": dict(request)}, request
    )


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
    episode_dir = tmp_path / "20260723 xieboran"
    episode_dir.mkdir()
    frame = tmp_path / "picked.png"
    frame.write_bytes(b"png")

    async def _fake_exec(*argv, **kwargs):
        Path(argv[argv.index("-o") + 1]).write_bytes(b"cutout")

        async def _comm():
            return b"", b""

        return SimpleNamespace(returncode=0, communicate=_comm)

    monkeypatch.setattr(guest_cutout.asyncio, "create_subprocess_exec", _fake_exec)

    dst = asyncio.run(
        guest_cutout.finalize(
            frame,
            "思考",
            "20260723-xieboran",
            2,
            episode_dir=episode_dir,
            engine="hyperframes",
            grade=False,
        )
    )
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
    # 雙落點：vault 是 canonical，episode 資料夾是可見性鏡射（修修 2026-08-06）
    assert (episode_dir / "packaging" / "cutouts" / "guest_v2_thoughtful.png").exists()


def test_finalize_rejects_unknown_emotion(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    frame = tmp_path / "picked.png"
    frame.write_bytes(b"png")
    with pytest.raises(ValueError):
        asyncio.run(guest_cutout.finalize(frame, "憂鬱", "ep", 1, episode_dir=tmp_path))


def _real_png(path: Path, w: int = 8, h: int = 8) -> None:
    from PIL import Image

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # 左上角一顆紅點 — flip/crop 斷言用
    im.putpixel((0, 0), (255, 0, 0, 255))
    im.save(path)


def test_finalize_host_role_crop_flip_grade(monkeypatch, tmp_path):
    """host 檔名前綴 + crop/flip/grade 管線（BiRefNet mock 成 copy）。"""
    from PIL import Image

    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    episode_dir = tmp_path / "ep-x-dir"
    episode_dir.mkdir()
    frame = tmp_path / "picked.png"
    _real_png(frame)

    monkeypatch.setattr(
        guest_cutout, "_remove_bg_birefnet", lambda src, dst: dst.write_bytes(src.read_bytes())
    )

    dst = asyncio.run(
        guest_cutout.finalize(
            frame,
            "認真",
            "ep-x",
            1,
            episode_dir=episode_dir,
            role="host",
            crop=(0.0, 0.0, 0.5, 0.5),
            flip=True,
        )
    )
    assert dst.name == "host_v1_serious.png"
    im = Image.open(dst)
    assert im.size == (4, 4)  # crop 生效
    r, g, b, a = im.getpixel((3, 0))  # flip 後紅點在右上
    assert a == 255 and r > g and r > b  # grade 會微調數值，只驗色相方向


def test_sample_host_skips_speaker_validation(monkeypatch, tmp_path):
    """--role host：不呼叫 speaker 分析、funnel 照跑（反應臉常在來賓說話窗）。"""

    def _boom(_d):
        raise AssertionError("host 不應計算 word_speakers")

    monkeypatch.setattr(guest_cutout, "load_word_speakers", _boom)

    async def _fake_run(video_path, out_dir, *, mode, window):
        return []

    import shared.thumbnail_funnel as tf

    monkeypatch.setattr(tf, "run", _fake_run)
    result = asyncio.run(
        guest_cutout.sample(tmp_path, tmp_path / "cam.mp4", (0.0, 10.0), 0, tmp_path, role="host")
    )
    assert result == []


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


def test_render_v2_passes_spec_to_worker(monkeypatch, tmp_path):
    """設計系統 v1 路徑：spec JSON → render_thumbnail(composition, variables, images)。"""
    import agents.brook.script_video.render_workers.thumbnail_worker as tw

    seen: dict = {}

    async def _fake(composition, *, variables, images, out_png, video_dir=None):
        seen.update(composition=composition, variables=variables, images=images, out=out_png)
        return out_png

    monkeypatch.setattr(tw, "render_thumbnail", _fake)
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "variables": {"title_lines": ["戒手機", "＝戒毒。"], "highlight_text": "戒毒"},
                "images": {"host_cutout_data_url": str(tmp_path / "h.png")},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = asyncio.run(render_still._render_v2("thumbnail_full", spec, tmp_path / "o.png"))
    assert out == tmp_path / "o.png"
    assert seen["composition"] == "thumbnail_full"
    assert seen["variables"]["highlight_text"] == "戒毒"
    assert seen["images"]["host_cutout_data_url"] == tmp_path / "h.png"


def test_author_interview_book_cover_layer_is_documented_and_supported():
    skill = (_REPO / ".claude/skills/thumbnail-brainstorm/SKILL.md").read_text(
        encoding="utf-8"
    )
    composition = (_REPO / "video/compositions/thumbnail_full/index.html").read_text(
        encoding="utf-8"
    )
    assert "N1 作者／新書訪談" in skill
    assert "book_cover_data_url" in skill
    assert 'id="book-layer"' in composition
    assert '"book_cover_data_url"' in composition
    assert "book_cover_opacity" in composition
    assert "book_cover_brightness" in composition


def test_render_thumbnail_missing_image_fails_loud(tmp_path):
    from agents.brook.script_video.render_workers.thumbnail_worker import render_thumbnail

    with pytest.raises(FileNotFoundError, match="host_cutout_data_url"):
        asyncio.run(
            render_thumbnail(
                "thumbnail_full",
                variables={},
                images={"host_cutout_data_url": tmp_path / "nope.png"},
                out_png=tmp_path / "o.png",
            )
        )


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
    host = vault / "Attachments" / "cutouts" / "shosho" / "surprised" / "1.png"
    guest = (
        vault
        / "Attachments"
        / "cutouts"
        / "podcast"
        / "20260723-xieboran"
        / "guest_v1_thoughtful.png"
    )
    center = png.parent / f"center-source-{n}.png"
    for path, payload in ((host, b"host"), (guest, b"guest")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    # 中央卡素材要能被開啟量長寬比——直式硬塞進橫卡是 2026-08-29 抓到的實際缺口。
    _write_center_png(center, 1600, 900)
    variables = {"caption": ""}
    images = {
        "prop_image_data_url": str(center),
        "host_cutout_data_url": str(host),
        "guest_cutout_data_url": str(guest),
    }
    render_spec = png.with_suffix(".render.json")
    render_spec.write_text(
        json.dumps({"composition": "thumbnail_reaction", "variables": variables, "images": images}),
        encoding="utf-8",
    )
    merged = dict(variables)
    for name, raw in images.items():
        path = Path(raw)
        merged[name] = (
            f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        )
    sidecar = {
        "schema": "nakama.thumbnail_composition_measurement.v1",
        "composition": "thumbnail_reaction",
        "renderer": {"name": "hyperframes", "version": "0.6.42"},
        "composition_sha256": hashlib.sha256(
            (_REPO / "video" / "compositions" / "thumbnail_reaction" / "index.html").read_bytes()
        ).hexdigest(),
        "canvas": {"width": 1280, "height": 720},
        "bboxes": {
            "protected_center_bbox": {"x": 301, "y": 132.5, "width": 678, "height": 455},
            "host_bbox": {"x": 0, "y": 40, "width": 380, "height": 680},
            "guest_bbox": {"x": 900, "y": 40, "width": 380, "height": 680},
            "title_bbox": None,
        },
        "assets": {
            name: {
                "path": str(Path(raw).resolve()),
                "sha256": hashlib.sha256(Path(raw).read_bytes()).hexdigest(),
            }
            for name, raw in images.items()
        },
        "variables_sha256": hashlib.sha256(
            json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "png_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
    }
    png.with_suffix(png.suffix + ".composition.json").write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    return {
        "title_rank": n,
        "thumbnail": str(png),
        "thumb_archetype_id": "T-V8",
        "joint_pairing_id": "JP-1",
        "host_cutout": str(host),
        "guest_cutout": "Attachments/cutouts/podcast/20260723-xieboran/guest_v1_thoughtful.png",
        "render_spec": str(render_spec),
        "center_provenance": {
            "supply": "envato",
            "source": "https://elements.envato.com/photo-placeholder-ABC123",
            "query": "empty office desk late night",
            "why": "扣回 04:21 那個 beat：沒有人在的辦公桌就是「工作被接管」的畫面",
        },
    }


def _write_center_png(path: Path, width: int, height: int) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), (120, 120, 120)).save(path)


def _attach_fixture(monkeypatch, tmp_path):
    """三個 spec 的正常 attach 現場；壞掉的那一個由呼叫端改第 1 筆。"""
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
    return vault, working, specs


def _rewrite_center(spec: dict, size: tuple[int, int]) -> None:
    """換掉中央卡素材的尺寸，並把 sidecar 的 hash 重新對齊（只留長寬比這一個變因）。"""
    render_spec_path = Path(spec["render_spec"])
    render_spec = json.loads(render_spec_path.read_text(encoding="utf-8"))
    center = Path(render_spec["images"]["prop_image_data_url"])
    _write_center_png(center, *size)

    thumbnail = Path(spec["thumbnail"])
    sidecar_path = thumbnail.with_suffix(thumbnail.suffix + ".composition.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    payload = center.read_bytes()
    sidecar["assets"]["prop_image_data_url"]["sha256"] = hashlib.sha256(payload).hexdigest()
    merged = dict(render_spec["variables"])
    for name, raw in render_spec["images"].items():
        merged[name] = (
            "data:image/png;base64,"
            + base64.b64encode(Path(raw).read_bytes()).decode("ascii")
        )
    sidecar["variables_sha256"] = hashlib.sha256(
        json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")


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
        receipt = (
            vault
            / "Attachments"
            / "packaging"
            / "20260723-xieboran"
            / "composition_receipts"
            / f"punch-L1-r{n}.json"
        )
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        assert payload["schema"] == "nakama.long_thumbnail_composition.v3"
        assert payload["center_provenance"]["supply"] == "envato"
        assert payload["center_provenance"]["why"].startswith("扣回 04:21")
        assert payload["thumbnail_sha256"]
        assert payload["measurement_sidecar_sha256"]
        assert payload["protected_center_bbox"]["x"] == 301.0
        assert (vault / payload["center_visual_asset"]).is_file()


def test_attach_rejects_portrait_center_frame(monkeypatch, tmp_path):
    """Long-highlight N2 is a wide card behind the two people, never a portrait slit."""
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
    sidecar_path = Path(specs[0]["thumbnail"] + ".composition.json")
    evidence = json.loads(sidecar_path.read_text())
    evidence["bboxes"]["protected_center_bbox"] = {
        "x": 480,
        "y": 72,
        "width": 320,
        "height": 576,
    }
    sidecar_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="horizontal"):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)


def test_attach_allows_people_in_front_of_horizontal_center_frame(monkeypatch, tmp_path):
    """The house style intentionally extends the wide orange card behind both cutouts."""
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
        spec = _spec(n, png, vault)
        evidence_path = Path(spec["thumbnail"] + ".composition.json")
        evidence = json.loads(evidence_path.read_text())
        evidence["bboxes"].update(
            {
                "protected_center_bbox": {"x": 301, "y": 132.5, "width": 678, "height": 455},
                "host_bbox": {"x": -654, "y": -120, "width": 1237, "height": 1142},
                "guest_bbox": {"x": 400, "y": -145, "width": 1377, "height": 1177},
            }
        )
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        specs.append(spec)

    out = attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)

    assert out.is_file()


def test_attach_full_program_n1_does_not_require_long_highlight_sidecar(
    monkeypatch, tmp_path
):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    working = tmp_path / "packaging"
    working.mkdir()
    data = _midstate_packages_file()
    data["cuts"][0]["cut_id"] = "full"
    (working / "packages.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    specs = []
    host = vault / "Attachments/cutouts/podcast/episode/host.png"
    guest = vault / "Attachments/cutouts/podcast/episode/guest.png"
    host.parent.mkdir(parents=True)
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")
    for n in (1, 2, 3):
        png = working / f"pkg-full-{n}.png"
        png.write_bytes(b"png")
        specs.append(
            {
                "title_rank": n,
                "thumbnail": str(png),
                "thumb_archetype_id": "T-V7",
                "joint_pairing_id": "author-book-n1",
                "host_cutout": str(host),
                "guest_cutout": str(guest),
            }
        )

    out = attach_packages.attach(working, "full", "episode", specs)
    assert out.is_file()
    assert (
        len(
            json.loads((working / "packages.json").read_text(encoding="utf-8"))["cuts"][0][
                "packages"
            ]
        )
        == 3
    )
    assert not (vault / "Attachments/packaging/episode/composition_receipts").exists()


@pytest.mark.parametrize("failure", ["missing-sidecar", "png-tamper", "asset-path-drift"])
def test_attach_composition_evidence_failures_land_nothing(monkeypatch, tmp_path, failure):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    working = tmp_path / "packaging"
    working.mkdir()
    original = json.dumps(_midstate_packages_file(), ensure_ascii=False)
    (working / "packages.json").write_text(original, encoding="utf-8")
    specs = []
    for n in (1, 2, 3):
        png = working / f"pkg-punch-L1-{n}.png"
        png.write_bytes(b"png")
        specs.append(_spec(n, png, vault))
    sidecar_path = Path(specs[0]["thumbnail"] + ".composition.json")
    if failure == "missing-sidecar":
        sidecar_path.unlink()
    elif failure == "png-tamper":
        Path(specs[0]["thumbnail"]).write_bytes(b"tampered")
    else:
        evidence = json.loads(sidecar_path.read_text())
        if failure == "asset-path-drift":
            evidence["assets"]["prop_image_data_url"]["path"] = str(tmp_path / "wrong.png")
        sidecar_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises((FileNotFoundError, ValueError)):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)

    assert (working / "packages.json").read_text(encoding="utf-8") == original
    vault_dir = vault / "Attachments" / "packaging" / "20260723-xieboran"
    assert not vault_dir.exists()


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


def test_attach_tolerates_other_long_cut_still_draft(monkeypatch, tmp_path):
    """同集其他長片還沒配封面（packages 空）時，本支仍要落得了地。

    ADR-054 D14 逐支處理 → 一集內同時存在「已完成」與「只有標題」的 cut 是
    設計本意。舊版整檔驗證會因為別支 packages != 3 而失敗，2026-07-29 謝伯讓集
    踩到，被迫手動搬檔繞過。
    """
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    working = tmp_path / "packaging"
    working.mkdir()

    data = _midstate_packages_file()
    draft = json.loads(json.dumps(data["cuts"][0]))  # deep copy
    draft["cut_id"] = "story-L1"
    draft["title_trace_ref"] = "packaging/story-L1/title_trace.json"
    data["cuts"].append(draft)
    (working / "packages.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    specs = []
    for n in (1, 2, 3):
        png = working / f"pkg-punch-L1-{n}.png"
        png.write_bytes(b"png")
        specs.append(_spec(n, png, vault))

    out = attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)

    written = json.loads(Path(out).read_text(encoding="utf-8"))
    done = next(c for c in written["cuts"] if c["cut_id"] == "punch-L1")
    pending = next(c for c in written["cuts"] if c["cut_id"] == "story-L1")
    assert len(done["packages"]) == 3
    assert pending["packages"] == []  # 草稿原樣保留，沒被動到


def test_attach_still_rejects_incomplete_target_cut(monkeypatch, tmp_path):
    """放寬只針對『其他支』——本支自己 packages 不足 3 仍必須擋下。"""
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    working = tmp_path / "packaging"
    working.mkdir()
    (working / "packages.json").write_text(
        json.dumps(_midstate_packages_file(), ensure_ascii=False), encoding="utf-8"
    )

    png = working / "pkg-punch-L1-1.png"
    png.write_bytes(b"png")

    with pytest.raises(Exception):  # pydantic ValidationError
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", [_spec(1, png, vault)])

    assert not (vault / "Attachments" / "packaging").exists() or not list(
        (vault / "Attachments" / "packaging" / "20260723-xieboran").glob("*.png")
    )


def test_attach_refuses_a_center_card_with_no_provenance(monkeypatch, tmp_path):
    """沒有來歷的中央卡不准落地——不然沒有人說得出「為什麼是這張圖」。"""
    _, working, specs = _attach_fixture(monkeypatch, tmp_path)
    del specs[0]["center_provenance"]
    with pytest.raises(ValueError, match="center_provenance"):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)


def test_attach_refuses_a_provenance_reason_too_short_to_mean_anything(monkeypatch, tmp_path):
    _, working, specs = _attach_fixture(monkeypatch, tmp_path)
    specs[0]["center_provenance"]["why"] = "配合主題"
    with pytest.raises(ValueError, match="why"):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)


def test_attach_refuses_an_unknown_supply_channel(monkeypatch, tmp_path):
    """供給順序是封閉集合（SKILL.md 紅線 5）——真人一律不准 AI 生成。"""
    _, working, specs = _attach_fixture(monkeypatch, tmp_path)
    specs[0]["center_provenance"]["supply"] = "ai_generated"
    with pytest.raises(ValueError, match="supply"):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)


def test_attach_refuses_a_portrait_center_source(monkeypatch, tmp_path):
    """punch-L04 rank 1 的實際缺口：1080×1920 直式塞進 678×455 的橫卡。"""
    _, working, specs = _attach_fixture(monkeypatch, tmp_path)
    _rewrite_center(specs[0], (1080, 1920))
    with pytest.raises(ValueError, match="必須是橫式"):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)


def test_attach_refuses_a_panorama_that_would_be_mostly_cropped_away(monkeypatch, tmp_path):
    """橫式但比例差太遠一樣不行——cover 會把兩側大部分裁掉。"""
    _, working, specs = _attach_fixture(monkeypatch, tmp_path)
    _rewrite_center(specs[0], (6000, 900))
    with pytest.raises(ValueError, match="留得下"):
        attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)


def test_attach_keeps_a_landscape_center_close_to_the_card_ratio(monkeypatch, tmp_path):
    """正常橫式素材不該被這道新檢查誤擋。"""
    _, working, specs = _attach_fixture(monkeypatch, tmp_path)
    _rewrite_center(specs[0], (3840, 2160))
    attach_packages.attach(working, "punch-L1", "20260723-xieboran", specs)
