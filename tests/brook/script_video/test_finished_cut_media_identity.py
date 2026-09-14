"""媒體身分有三種，不是兩種——品牌 badge 是第三種。

2026-09-14：badge 的鋪軌接線補上之後，第一次真的跑到 Resolve 就死在
`Resolve media object is neither the Master nor content-addressed`。接線那一輪的
驗證是離線投影，沒有經過媒體識別這一關，所以看不出來。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._composition import (
    _ProductionMediaIdentityResolver,
)
from agents.brook.script_video.finished_cut_production._materialization_fusion import (
    VerifiedEditorialMasterContract,
)

EPISODE_ID = "20260901 蘇予昕"
MASTER_HASH = "a" * 64
MASTER_DIGEST = "b" * 64


@dataclass(frozen=True)
class _FakeMediaPoolItem:
    """Resolve 的 media pool item 在這一層只被問一件事：檔案路徑。"""

    path: Path

    def GetClipProperty(self) -> dict[str, str]:  # noqa: N802 - Resolve 的命名
        return {"File Path": str(self.path)}


class _FakeMasterCache:
    def __init__(self, master_path: Path) -> None:
        self._master_path = master_path

    def load(
        self,
        *,
        episode_id: str,
        editorial_master_content_hash: str,
    ) -> VerifiedEditorialMasterContract:
        return VerifiedEditorialMasterContract(
            episode_id=episode_id,
            editorial_master_content_hash=editorial_master_content_hash,
            master_media_path=self._master_path,
            master_media_sha256=MASTER_DIGEST,
            master_media_bytes=self._master_path.stat().st_size,
            resolve_project_name=episode_id,
            editorial_master_timeline_name=episode_id,
            editorial_master_timeline_uid="00000000-0000-0000-0000-000000000000",
            frame_rate=30.0,
            duration_sec=6146.944,
        )


class _UnusedAssetStore:
    """母帶與 badge 兩條路都不該問到 Active Store。"""

    def resolve_active_asset(self, reference: str) -> object:
        raise AssertionError(f"Active Store should not be consulted: {reference}")


def _resolver(tmp_path: Path) -> tuple[_ProductionMediaIdentityResolver, Path, Path]:
    master = tmp_path / "master.mp4"
    master.write_bytes(b"editorial master")
    badge_root = tmp_path / "assets" / "broll"
    badge_root.mkdir(parents=True)
    resolver = _ProductionMediaIdentityResolver(
        editorial_master=_FakeMasterCache(master),
        episode_id=EPISODE_ID,
        editorial_master_content_hash=MASTER_HASH,
        assets=_UnusedAssetStore(),
        brand_badge_root=badge_root,
    )
    return resolver, master, badge_root


def test_the_master_keeps_its_receipted_digest(tmp_path: Path) -> None:
    resolver, master, _ = _resolver(tmp_path)

    assert resolver.digest_for(_FakeMediaPoolItem(master)) == MASTER_DIGEST


def test_a_declared_brand_badge_is_identified_by_its_bytes(tmp_path: Path) -> None:
    """badge 不在 Active Store，檔名也不是 sha256——沒有東西可查，只能算。"""
    resolver, _, badge_root = _resolver(tmp_path)
    badge = badge_root / "brand-badge-8s.mov"
    badge.write_bytes(b"eight seconds of brand")

    expected = hashlib.sha256(b"eight seconds of brand").hexdigest()
    assert resolver.digest_for(_FakeMediaPoolItem(badge)) == expected
    # 一次物化會問同一支三次（鋪軌、鋪完覆驗、快照），答案要一樣。
    assert resolver.digest_for(_FakeMediaPoolItem(badge)) == expected


def test_a_badge_named_file_outside_this_episode_is_not_a_badge(tmp_path: Path) -> None:
    """只比檔名的話，別處的同名檔就矇混過去了。"""
    resolver, _, _ = _resolver(tmp_path)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    impostor = elsewhere / "brand-badge-7s.mov"
    impostor.write_bytes(b"not this episode's badge")

    with pytest.raises(ValueError, match="neither the Master nor content-addressed"):
        resolver.digest_for(_FakeMediaPoolItem(impostor))


def test_an_undeclared_file_in_the_badge_folder_is_not_a_badge(tmp_path: Path) -> None:
    """只比目錄的話，任何丟進 `assets/broll/` 的檔案都會取得媒體身分。"""
    resolver, _, badge_root = _resolver(tmp_path)
    stray = badge_root / "some-stock-clip.mov"
    stray.write_bytes(b"not a badge")

    with pytest.raises(ValueError, match="neither the Master nor content-addressed"):
        resolver.digest_for(_FakeMediaPoolItem(stray))
