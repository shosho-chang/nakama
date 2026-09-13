"""人工勘誤只准改修修指名的那幾句，其餘一個字元都不能動。

這一層之所以要 fail closed 得這麼兇：它是整條線上**唯一**沒有機器判準的改動。
語助詞清理有規則、雙審有共識門檻、dual-ASR 有兩家引擎交叉——只有這裡是
「因為修修說是這樣」。所以綁定一鬆，錯字幕就會靜默出片。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from shared.subtitle_corrections import (
    CONTRACT,
    SubtitleCorrectionError,
    apply_corrections_text,
    corrections_path,
    load_corrections,
    open_for_release,
)

SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\n新大附中之前退休的陳永元校長\n\n"
    "2\n00:00:02,000 --> 00:00:03,000\n呃 資優班的課\n\n"
    "3\n00:00:03,000 --> 00:00:04,000\n就用那個龍蝦\n"
)


def _document(corrections: list[dict], **overrides) -> dict:
    payload = {
        "schema_version": 1,
        "contract": CONTRACT,
        "episode_id": "20260721 呂冠緯",
        "release_srt_sha256": "a" * 64,
        "attested_by": "shosho",
        "attested_at": "2026-09-11T02:00:00Z",
        "corrections": corrections,
    }
    payload.update(overrides)
    return payload


def _written(tmp_path: Path, payload: dict, name: str = "doc.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _one(**overrides) -> dict:
    row = {
        "cue": 1,
        "from": "新大附中之前退休的陳永元校長",
        "to": "興大附中之前退休的陳勇延校長",
        "basis": "網查：興大附中前校長陳勇延，已退休",
    }
    row.update(overrides)
    return row


def test_applies_only_the_named_cue(tmp_path: Path) -> None:
    document = load_corrections(_written(tmp_path, _document([_one()])))
    result, stats = apply_corrections_text(SRT, document)

    assert stats["corrections_applied"] == 1
    assert stats["attested_by"] == "shosho"
    assert "興大附中之前退休的陳勇延校長" in result
    # 其餘兩句與時間軸原封不動。
    assert "呃 資優班的課" in result
    assert "就用那個龍蝦" in result
    assert result.count("-->") == 3
    assert "00:00:01,000 --> 00:00:02,000" in result


def test_original_text_mismatch_fails_closed(tmp_path: Path) -> None:
    """勘誤單是對著某一版 release 抄的；對不上就不准硬套。"""
    document = load_corrections(
        _written(tmp_path, _document([_one(**{"from": "師大附中之前退休的陳永元校長"})]))
    )
    with pytest.raises(SubtitleCorrectionError, match="原文與勘誤單不符"):
        apply_corrections_text(SRT, document)


def test_unknown_cue_fails_closed(tmp_path: Path) -> None:
    document = load_corrections(_written(tmp_path, _document([_one(cue=99)])))
    with pytest.raises(SubtitleCorrectionError, match="不在這份 SRT 裡"):
        apply_corrections_text(SRT, document)


def test_release_binding_is_verified(tmp_path: Path) -> None:
    """綁 release.srt 的雜湊——換了一版 release，舊勘誤單一律作廢。"""
    episode = tmp_path / "20260721 呂冠緯"
    episode.mkdir()
    release = episode / "release.srt"
    release.write_text(SRT, encoding="utf-8")
    digest = hashlib.sha256(release.read_bytes()).hexdigest()

    corrections_path(episode).write_text(
        json.dumps(_document([_one()], release_srt_sha256=digest), ensure_ascii=False),
        encoding="utf-8",
    )
    assert open_for_release(episode, release_srt=release).corrections[0].cue == 1

    release.write_text(SRT.replace("龍蝦", "LobeChat"), encoding="utf-8")
    with pytest.raises(SubtitleCorrectionError, match="綁的不是這一版"):
        open_for_release(episode, release_srt=release)


def test_absent_corrections_file_is_not_an_error(tmp_path: Path) -> None:
    episode = tmp_path / "20260721 呂冠緯"
    episode.mkdir()
    release = episode / "release.srt"
    release.write_text(SRT, encoding="utf-8")

    assert open_for_release(episode, release_srt=release) is None


def test_wrong_episode_fails_closed(tmp_path: Path) -> None:
    episode = tmp_path / "20260721 呂冠緯"
    episode.mkdir()
    release = episode / "release.srt"
    release.write_text(SRT, encoding="utf-8")
    digest = hashlib.sha256(release.read_bytes()).hexdigest()
    corrections_path(episode).write_text(
        json.dumps(
            _document([_one()], release_srt_sha256=digest, episode_id="20260901 蘇予昕"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(SubtitleCorrectionError, match="episode_id"):
        open_for_release(episode, release_srt=release)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (_document([_one(), _one()]), "被更正兩次"),
        (_document([_one(to="新大附中之前退休的陳永元校長")]), "from 與 to 相同"),
        (_document([_one(basis="")]), "basis"),
        (_document([]), "沒有任何一筆更正"),
        (_document([_one()], contract="nakama.subtitle_human_corrections.v2"), "contract"),
    ],
)
def test_malformed_documents_are_rejected(tmp_path: Path, payload: dict, match: str) -> None:
    with pytest.raises(SubtitleCorrectionError, match=match):
        load_corrections(_written(tmp_path, payload))


def test_basis_is_mandatory_so_every_change_is_traceable(tmp_path: Path) -> None:
    """「因為修修說是這樣」也要寫下來是哪一種『說』——在場、網查、還是記得。"""
    document = load_corrections(_written(tmp_path, _document([_one()])))
    assert document.corrections[0].basis
    assert document.attested_by == "shosho"
    assert document.attested_at == "2026-09-11T02:00:00Z"
