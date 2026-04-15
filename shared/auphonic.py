"""Auphonic REST API 客戶端 — 音頻 normalization + 降噪。

支援多帳號輪詢（免費方案每帳號 2 hr/月），
以及裁切免費方案的頭尾 Jingle。
所有參數從 .env 讀取，可在 normalize() 呼叫時覆寫。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from shared.log import get_logger

logger = get_logger("nakama.auphonic")

_API_BASE = "https://auphonic.com/api"

# Auphonic production status codes
_STATUS_DONE = 3
_STATUS_ERROR = 2


# ── 帳號管理 ──


@dataclass
class AuphonicAccount:
    email: str
    api_key: str


def _load_accounts() -> list[AuphonicAccount]:
    """從環境變數載入 Auphonic 帳號（AUPHONIC_ACCOUNT_1 ~ _5）。"""
    accounts: list[AuphonicAccount] = []
    for i in range(1, 6):
        raw = os.environ.get(f"AUPHONIC_ACCOUNT_{i}", "").strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(",", 1)]
        if len(parts) != 2 or not parts[1]:
            logger.warning(f"AUPHONIC_ACCOUNT_{i} 格式錯誤，應為 email,api_key")
            continue
        accounts.append(AuphonicAccount(email=parts[0], api_key=parts[1]))

    if not accounts:
        raise ValueError("未設定任何 AUPHONIC_ACCOUNT_N，無法使用 Auphonic normalization")
    return accounts


# ── .env 參數讀取 ──


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default).strip()


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key, "").strip()
    if not val:
        return default
    return float(val)


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key, "").strip()
    if not val:
        return default
    return int(val)


def _load_env_defaults() -> dict:
    """從 .env 讀取所有 Auphonic 處理參數，回傳 dict。"""
    return {
        "loudness_target": _env_float("AUPHONIC_LOUDNESS", -16.0),
        "loudness_method": _env_str("AUPHONIC_LOUDNESS_METHOD", "program"),
        "max_peak": _env_str("AUPHONIC_MAX_PEAK", "auto"),
        "denoise": _env_bool("AUPHONIC_DENOISE", True),
        "denoise_method": _env_str("AUPHONIC_DENOISE_METHOD", "dynamic"),
        "denoise_amount": _env_int("AUPHONIC_DENOISE_AMOUNT", 0),
        "deverb_amount": _env_int("AUPHONIC_DEVERB_AMOUNT", -1),
        "debreath_amount": _env_int("AUPHONIC_DEBREATH_AMOUNT", -1),
        "output_format": _env_str("AUPHONIC_OUTPUT_FORMAT", "wav"),
        "output_bitdepth": _env_int("AUPHONIC_OUTPUT_BITDEPTH", 24),
        "output_bitrate": _env_str("AUPHONIC_OUTPUT_BITRATE", ""),
        "leveler": _env_bool("AUPHONIC_LEVELER", True),
        "leveler_strength": _env_int("AUPHONIC_LEVELER_STRENGTH", 100),
        "compressor": _env_str("AUPHONIC_COMPRESSOR", "auto"),
        "filtering": _env_bool("AUPHONIC_FILTERING", True),
        "filter_method": _env_str("AUPHONIC_FILTER_METHOD", "voice_autoeq"),
        "silence_cutter": _env_bool("AUPHONIC_SILENCE_CUTTER", False),
        "filler_cutter": _env_bool("AUPHONIC_FILLER_CUTTER", False),
        "trim_jingle": _env_bool("AUPHONIC_TRIM_JINGLE", True),
        "jingle_seconds": _env_float("AUPHONIC_JINGLE_SECONDS", 6.0),
    }


# ── 工具函式 ──


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"bearer {api_key}"}


def _get_audio_duration(path: Path) -> float:
    """用 ffprobe 取得音檔長度（秒）。"""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def _find_available_account(duration_seconds: float) -> AuphonicAccount:
    """從帳號列表中找到餘額足夠的帳號，優先選離 reset 最近的。

    策略：查詢所有帳號的餘額和 recharge_date，
    篩選出餘額足夠的，再按「距離下次 reset 最近」排序，
    避免快到期的餘額浪費。

    Auphonic 免費方案每月從 recharge_date 起算重新給 2 hr。
    """
    from datetime import datetime, timedelta, timezone

    accounts = _load_accounts()
    duration_hours = max(duration_seconds / 3600, 0.05)

    candidates: list[tuple[float, AuphonicAccount]] = []  # (days_until_reset, account)

    for account in accounts:
        try:
            resp = httpx.get(
                f"{_API_BASE}/user.json",
                headers=_headers(account.api_key),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            credits = data["credits"]

            # 計算距離下次 reset 的天數
            recharge_str = data.get("recharge_date", "")
            if recharge_str:
                recharge_dt = datetime.fromisoformat(recharge_str.replace("Z", "+00:00"))
                next_reset = recharge_dt + timedelta(days=30)
                now = datetime.now(timezone.utc)
                days_left = (next_reset - now).total_seconds() / 86400
            else:
                days_left = 999  # 無 recharge 資訊，排最後

            logger.info(f"{account.email}: 餘額 {credits:.2f} hr, ~{days_left:.0f} 天後 reset")

            if credits >= duration_hours:
                candidates.append((days_left, account))
        except Exception as e:
            logger.warning(f"{account.email}: 查詢失敗 — {e}")
            continue

    if not candidates:
        raise ValueError(
            f"所有 {len(accounts)} 個 Auphonic 帳號餘額不足（需要 {duration_hours:.2f} hr）"
        )

    # 按距離 reset 天數排序，最近的優先
    candidates.sort(key=lambda x: x[0])
    chosen = candidates[0][1]
    logger.info(f"選擇帳號: {chosen.email}")
    return chosen


def _create_production(api_key: str, *, params: dict) -> str:
    """建立 Auphonic production，回傳 UUID。"""
    algorithms = {
        # Loudness
        "normloudness": True,
        "loudnesstarget": str(int(params["loudness_target"])),
        "loudnessmethod": params["loudness_method"],
        # Noise
        "denoise": params["denoise"],
        "denoisemethod": params["denoise_method"],
        "denoiseamount": params["denoise_amount"],
        "deverbamount": params["deverb_amount"],
        "debreathamount": params["debreath_amount"],
        # Leveler
        "leveler": params["leveler"],
        "levelerstrength": params["leveler_strength"],
        "compressor": params["compressor"],
        # Filtering
        "filtering": params["filtering"],
        "filtermethod": params["filter_method"],
        # Cutting
        "silence_cutter": params["silence_cutter"],
        "filler_cutter": params["filler_cutter"],
    }
    if params["max_peak"] != "auto":
        algorithms["maxpeak"] = params["max_peak"]

    # Output format
    output_file: dict = {"format": params["output_format"]}
    if params["output_format"] == "wav" and params["output_bitdepth"]:
        output_file["bitdepth"] = params["output_bitdepth"]
    if params["output_bitrate"]:
        output_file["bitrate"] = params["output_bitrate"]

    payload = {
        "output_files": [output_file],
        "algorithms": algorithms,
    }
    resp = httpx.post(
        f"{_API_BASE}/productions.json",
        json=payload,
        headers={**_headers(api_key), "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    uuid = resp.json()["data"]["uuid"]
    logger.info(f"建立 production: {uuid}")
    return uuid


def _upload_file(api_key: str, uuid: str, audio_path: Path) -> None:
    """上傳音檔到 production。"""
    with open(audio_path, "rb") as f:
        resp = httpx.post(
            f"{_API_BASE}/production/{uuid}/upload.json",
            files={"input_file": (audio_path.name, f)},
            headers=_headers(api_key),
            timeout=300,
        )
    resp.raise_for_status()
    logger.info(f"上傳完成: {audio_path.name}")


def _start_and_wait(
    api_key: str,
    uuid: str,
    *,
    timeout: int = 600,
    poll_interval: int = 5,
) -> dict:
    """開始處理並輪詢直到完成。"""
    resp = httpx.post(
        f"{_API_BASE}/production/{uuid}/start.json",
        headers=_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("開始處理...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        resp = httpx.get(
            f"{_API_BASE}/production/{uuid}.json",
            headers=_headers(api_key),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        status = data["status"]
        status_str = data.get("status_string", str(status))

        if status == _STATUS_DONE:
            logger.info("處理完成")
            return data
        if status == _STATUS_ERROR:
            raise RuntimeError(f"Auphonic 處理失敗: {status_str}")

        logger.debug(f"狀態: {status_str}")

    raise TimeoutError(f"Auphonic 處理超時（{timeout}s）")


def _download_result(api_key: str, production_data: dict, output_path: Path) -> Path:
    """下載處理完的音檔。"""
    output_files = production_data.get("output_files", [])
    if not output_files:
        raise RuntimeError("Auphonic production 沒有輸出檔案")

    uuid = production_data["uuid"]
    output_basename = production_data.get("output_basename", "")
    file_ending = output_files[0].get("ending", ".wav")
    download_url = f"{_API_BASE}/production/{uuid}/download/{output_basename}{file_ending}"

    resp = httpx.get(download_url, headers=_headers(api_key), timeout=300, follow_redirects=True)
    resp.raise_for_status()

    output_path.write_bytes(resp.content)
    logger.info(f"下載完成: {output_path}")
    return output_path


def _trim_jingle(audio_path: Path, jingle_seconds: float) -> Path:
    """用 ffmpeg 裁切頭尾 Jingle，回傳裁切後的檔案路徑。"""
    duration = _get_audio_duration(audio_path)
    end_time = duration - jingle_seconds

    if end_time <= jingle_seconds:
        logger.warning(f"音檔太短（{duration:.1f}s），跳過 Jingle 裁切")
        return audio_path

    trimmed_path = audio_path.with_stem(f"{audio_path.stem}_trimmed")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-ss",
        str(jingle_seconds),
        "-to",
        str(end_time),
        "-c",
        "copy",
        str(trimmed_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    logger.info(f"Jingle 裁切完成: 去掉頭尾各 {jingle_seconds}s → {trimmed_path.name}")
    return trimmed_path


# ── 公開 API ──


def normalize(audio_path: str | Path, *, output_dir: str | Path | None = None, **overrides) -> Path:
    """上傳音檔到 Auphonic 處理（normalization + 降噪），下載結果。

    所有處理參數從 .env 讀取，可用 keyword arguments 覆寫。
    自動從多個帳號中選擇有餘額的。
    免費方案會在頭尾加 Jingle，預設自動裁切。

    Args:
        audio_path: 原始音檔路徑
        output_dir: 輸出目錄（預設與音檔同目錄）
        **overrides: 覆寫 .env 參數，例如 loudness_target=-14, denoise=False

    Returns:
        處理後音檔的 Path
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在: {audio_path}")

    output_dir = Path(output_dir) if output_dir else audio_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # 讀取 .env 預設值，再套用覆寫
    params = _load_env_defaults()
    params.update(overrides)

    logger.info(f"開始 Auphonic normalization: {audio_path.name}")
    logger.info(
        f"參數: loudness={params['loudness_target']} LUFS, "
        f"denoise={params['denoise']} ({params['denoise_method']})"
    )

    # 1. 取得音檔長度，找有餘額的帳號
    duration = _get_audio_duration(audio_path)
    logger.info(f"音檔長度: {duration:.1f}s ({duration / 60:.1f} min)")
    account = _find_available_account(duration)
    logger.info(f"使用帳號: {account.email}")

    # 2. 建立 production + 上傳 + 處理
    uuid = _create_production(account.api_key, params=params)
    _upload_file(account.api_key, uuid, audio_path)
    production_data = _start_and_wait(account.api_key, uuid)

    # 3. 下載結果
    output_path = output_dir / f"{audio_path.stem}_normalized.wav"
    _download_result(account.api_key, production_data, output_path)

    # 4. 裁切 Jingle（免費方案）
    if params["trim_jingle"]:
        output_path = _trim_jingle(output_path, params["jingle_seconds"])

    return output_path
