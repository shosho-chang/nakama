"""Auphonic REST API 客戶端 — 音頻 normalization + 降噪。

支援多帳號輪詢（免費方案每帳號 2 hr/月），
以及裁切免費方案的頭尾 Jingle。
所有參數從 .env 讀取，可在 normalize() 呼叫時覆寫。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from shared.log import get_logger

logger = get_logger("nakama.auphonic")

_API_BASE = "https://auphonic.com/api"

# Auphonic production status codes
_STATUS_DONE = 3
_STATUS_ERROR = 2
_RUNNING_STATUSES = {1, 4, 5, 6, 7, 8, 12, 13, 14, 15}
_PRODUCTION_RECEIPT_NAME = "auphonic-production.v1.json"


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


def _strip_inline_comment(val: str) -> str:
    """python-dotenv 不保證剝 inline `#` 註解（空值時整段註解會被當值讀），手動處理。"""
    idx = val.find("#")
    if idx != -1:
        val = val[:idx]
    return val.strip()


def _env_str(key: str, default: str) -> str:
    raw = os.environ.get(key)
    if raw is None:
        return default.strip()
    return _strip_inline_comment(raw)


def _env_bool(key: str, default: bool) -> bool:
    val = _strip_inline_comment(os.environ.get(key, "")).lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


def _env_float(key: str, default: float) -> float:
    val = _strip_inline_comment(os.environ.get(key, ""))
    if not val:
        return default
    return float(val)


def _env_int(key: str, default: int) -> int:
    val = _strip_inline_comment(os.environ.get(key, ""))
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
        "filter_method": _env_str("AUPHONIC_FILTER_METHOD", "autoeq"),
        "silence_cutter": _env_bool("AUPHONIC_SILENCE_CUTTER", False),
        "filler_cutter": _env_bool("AUPHONIC_FILLER_CUTTER", False),
        "trim_jingle": _env_bool("AUPHONIC_TRIM_JINGLE", True),
        "jingle_seconds": _env_float("AUPHONIC_JINGLE_SECONDS", 6.0),
    }


# ── 工具函式 ──


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"bearer {api_key}"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _params_sha256(params: dict) -> str:
    payload = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_production_receipt(path: Path, receipt: dict, *, stage: str) -> dict:
    updated = {
        **receipt,
        "stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return updated


def _load_bound_production_receipt(
    path: Path,
    *,
    source_path: Path,
    source_sha256: str,
    source_size_bytes: int,
    params_sha256: str,
) -> dict | None:
    if not path.exists():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Auphonic production receipt 無法讀取: {path}") from exc
    expected = {
        "schema": "nakama.auphonic-production.v1",
        "source_path": str(source_path.resolve()),
        "source_sha256": source_sha256,
        "source_size_bytes": source_size_bytes,
        "params_sha256": params_sha256,
    }
    drift = [key for key, value in expected.items() if receipt.get(key) != value]
    if drift:
        raise RuntimeError(
            "Auphonic production receipt 與本次輸入不一致，拒絕建立重複 production: "
            + ", ".join(drift)
        )
    if not receipt.get("production_uuid") or not receipt.get("account_email"):
        raise RuntimeError("Auphonic production receipt 缺少 production UUID 或 account identity")
    return receipt


def _account_for_receipt(receipt: dict) -> AuphonicAccount:
    email = receipt["account_email"]
    for account in _load_accounts():
        if account.email == email:
            return account
    raise RuntimeError(f"Auphonic production receipt 的帳號目前未設定: {email}")


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
    # encoding 明示 utf-8：Windows console codepage（cp1252/cp950）解不了
    # CJK 檔名（ffprobe JSON 內含路徑），會炸 UnicodeDecodeError
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
    )
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
    if resp.status_code >= 400:
        logger.error(f"Auphonic {resp.status_code} body: {resp.text}")
        logger.error(f"Payload was: {payload}")
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
    timeout: int = 3600,
    poll_interval: int = 5,
    start: bool = True,
) -> dict:
    """開始處理並輪詢直到完成。"""
    if start:
        resp = httpx.post(
            f"{_API_BASE}/production/{uuid}/start.json",
            headers=_headers(api_key),
            timeout=30,
        )
        resp.raise_for_status()
        logger.info("開始處理...")
    else:
        logger.info(f"續接既有 production: {uuid}")

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


def _get_production_data(api_key: str, uuid: str) -> dict:
    resp = httpx.get(
        f"{_API_BASE}/production/{uuid}.json",
        headers=_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def _download_result(api_key: str, production_data: dict, output_path: Path) -> Path:
    """下載處理完的音檔。"""
    output_files = production_data.get("output_files", [])
    if not output_files:
        raise RuntimeError("Auphonic production 沒有輸出檔案")

    download_url = output_files[0].get("download_url")
    if not download_url:
        raise RuntimeError("Auphonic output_files 缺 download_url")

    partial_path = output_path.with_suffix(output_path.suffix + ".part")
    with httpx.stream(
        "GET",
        download_url,
        headers=_headers(api_key),
        timeout=300,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        with partial_path.open("wb") as handle:
            for chunk in resp.iter_bytes(chunk_size=8 * 1024 * 1024):
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(partial_path, output_path)
    logger.info(f"下載完成: {output_path}")
    return output_path


def _trim_jingle(audio_path: Path, jingle_seconds: float) -> Path:
    """用 ffmpeg 裁切頭尾 Jingle（固定秒數 fallback），回傳裁切後的檔案路徑。

    ⚠️ Auphonic 免費方案 Jingle 實測**不是**固定 6 秒（2026-07-25 量到頭 6.409s /
    總長 12.817s）——固定裁會讓時間軸偏移原始錄影約 0.4s。有原始檔可對齊時
    一律走 `_align_trim`（交叉相關、sample 級），本函式僅當 fallback。
    """
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


_ALIGN_SR = 16000  # 對齊用解碼取樣率


def _decode_mono(path: Path, duration: float, start: float = 0.0) -> "object":
    """解碼片段為 16kHz mono float32（numpy array），交叉相關用。"""
    import numpy as np

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        str(_ALIGN_SR),
        "-f",
        "f32le",
        "-",
    ]
    out = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def _find_offset(haystack, needle) -> tuple[float, float]:
    """needle 在 haystack 中的起始秒數與正規化相關峰值。"""
    import numpy as np

    n = len(haystack) + len(needle)
    corr = np.fft.irfft(np.fft.rfft(haystack, n) * np.conj(np.fft.rfft(needle, n)), n)
    k = int(np.argmax(np.abs(corr[: len(haystack)])))
    seg = haystack[k : k + len(needle)]
    peak = float(abs(corr[k]) / (np.linalg.norm(needle) * np.linalg.norm(seg) + 1e-9))
    return k / _ALIGN_SR, peak


def _align_trim(audio_path: Path, source_path: Path, jingle_seconds: float) -> Path:
    """把 Auphonic 輸出與**原始檔**交叉相關對齊後精確裁掉 Jingle。

    輸出時間軸 = 原始錄影（sample 級），量測驗證兩個位置偏移一致才採用；
    numpy 不在 / 相關峰值過低 / 偏移不一致 → 退回固定秒數 `_trim_jingle`。
    """
    try:
        import numpy  # noqa: F401
    except ImportError:
        logger.warning("numpy 不可用，Jingle 裁切退回固定秒數（時間軸可能偏移 <1s）")
        return _trim_jingle(audio_path, jingle_seconds)

    try:
        src_dur = _get_audio_duration(source_path)
        # 頭部：原始前 20s 在輸出前 40s 中定位
        head_off, head_peak = _find_offset(
            _decode_mono(audio_path, 40.0), _decode_mono(source_path, 20.0)
        )
        # 驗證：原始中段 10s 應出現在輸出 head_off + 中段（偏移一致才可信）
        mid = min(60.0, src_dur / 2)
        mid_off, mid_peak = _find_offset(
            _decode_mono(audio_path, 30.0, start=head_off + mid - 15.0),
            _decode_mono(source_path, 10.0, start=mid),
        )
        mid_drift = abs((head_off + mid - 15.0 + mid_off) - (head_off + mid))
        if head_peak < 0.5 or mid_peak < 0.5 or mid_drift > 0.05:
            logger.warning(
                f"對齊不可信（peak {head_peak:.2f}/{mid_peak:.2f} drift {mid_drift:.3f}s），"
                "退回固定秒數裁切"
            )
            return _trim_jingle(audio_path, jingle_seconds)

        trimmed_path = audio_path.with_stem(f"{audio_path.stem}_trimmed")
        # 重編碼裁切（-c copy 會對齊 packet 邊界產生 <0.1s 誤差）；PCM 重編碼無損
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(audio_path),
                "-ss",
                f"{head_off:.6f}",
                "-t",
                f"{src_dur:.6f}",
                "-c:a",
                "pcm_s24le",
                str(trimmed_path),
            ],
            capture_output=True,
            check=True,
        )
        logger.info(
            f"Jingle 對齊裁切完成: 頭 {head_off:.3f}s（peak {head_peak:.2f}）→ "
            f"{trimmed_path.name}（時間軸 = 原始檔）"
        )
        return trimmed_path
    except Exception as e:
        logger.warning(f"對齊裁切失敗（{type(e).__name__}: {e}），退回固定秒數裁切")
        return _trim_jingle(audio_path, jingle_seconds)


def find_existing_production(filename: str, duration_seconds: float) -> tuple[str, dict] | None:
    """在所有帳號找同名且時長吻合的已完成 production（重下載不耗額度）。

    額度不足時的復原路徑：回傳 (api_key, production_data)，找不到回傳 None。
    """
    for account in _load_accounts():
        try:
            resp = httpx.get(
                f"{_API_BASE}/productions.json",
                headers=_headers(account.api_key),
                params={"limit": 20},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception:
            continue
        for prod in resp.json().get("data", []):
            meta = prod.get("metadata") or {}
            title = meta.get("title") or ""
            if prod.get("status") != _STATUS_DONE:
                continue
            in_file = Path(prod.get("input_file") or title or "").name
            if in_file and Path(filename).stem in in_file:
                # 時長吻合（輸出 = 原始 + Jingle ~13s，容忍 30s）
                length = float(prod.get("length", 0) or 0)
                if length and abs(length - duration_seconds) > 30:
                    continue
                logger.info(
                    f"找到既有 production {prod.get('uuid')}（帳號 {account.email}），"
                    "重下載不耗額度"
                )
                return account.api_key, prod
    return None


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
    output_path = output_dir / f"{audio_path.stem}_normalized.wav"
    source_sha256 = _sha256_file(audio_path)
    source_size_bytes = audio_path.stat().st_size
    params_digest = _params_sha256(params)
    receipt_path = output_dir / _PRODUCTION_RECEIPT_NAME
    receipt = _load_bound_production_receipt(
        receipt_path,
        source_path=audio_path,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        params_sha256=params_digest,
    )
    wait_timeout = max(1800, int(duration))

    if receipt is not None:
        account = _account_for_receipt(receipt)
        uuid = receipt["production_uuid"]
        production_data = _get_production_data(account.api_key, uuid)
        status = int(production_data.get("status", -1))
        if status == _STATUS_ERROR:
            raise RuntimeError(
                f"Auphonic 既有 production 失敗: {production_data.get('status_string', status)}"
            )
        if status == _STATUS_DONE:
            logger.info(f"既有 production 已完成，直接下載: {uuid}")
        elif status in _RUNNING_STATUSES:
            receipt = _write_production_receipt(receipt_path, receipt, stage="processing")
            production_data = _start_and_wait(
                account.api_key,
                uuid,
                timeout=wait_timeout,
                start=False,
            )
        else:
            if receipt.get("stage") == "created":
                _upload_file(account.api_key, uuid, audio_path)
                receipt = _write_production_receipt(receipt_path, receipt, stage="uploaded")
            receipt = _write_production_receipt(receipt_path, receipt, stage="processing")
            production_data = _start_and_wait(
                account.api_key,
                uuid,
                timeout=wait_timeout,
            )
        _download_result(account.api_key, production_data, output_path)
        _write_production_receipt(receipt_path, receipt, stage="downloaded")
        if params["trim_jingle"]:
            output_path = _align_trim(output_path, audio_path, params["jingle_seconds"])
        return output_path

    try:
        account = _find_available_account(duration)
    except ValueError:
        # 額度不足 → 先找既有 production（同檔重跑場景；重下載不耗額度）
        existing = find_existing_production(audio_path.name, duration)
        if existing is None:
            raise
        api_key, production_data = existing
        _download_result(api_key, production_data, output_path)
        if params["trim_jingle"]:
            output_path = _align_trim(output_path, audio_path, params["jingle_seconds"])
        return output_path
    logger.info(f"使用帳號: {account.email}")

    # 2. 建立 production + 上傳 + 處理
    uuid = _create_production(account.api_key, params=params)
    receipt = _write_production_receipt(
        receipt_path,
        {
            "schema": "nakama.auphonic-production.v1",
            "source_path": str(audio_path.resolve()),
            "source_sha256": source_sha256,
            "source_size_bytes": source_size_bytes,
            "params_sha256": params_digest,
            "params": params,
            "production_uuid": uuid,
            "account_email": account.email,
        },
        stage="created",
    )
    _upload_file(account.api_key, uuid, audio_path)
    receipt = _write_production_receipt(receipt_path, receipt, stage="uploaded")
    receipt = _write_production_receipt(receipt_path, receipt, stage="processing")
    production_data = _start_and_wait(account.api_key, uuid, timeout=wait_timeout)

    # 3. 下載結果
    _download_result(account.api_key, production_data, output_path)
    _write_production_receipt(receipt_path, receipt, stage="downloaded")

    # 4. 裁切 Jingle（免費方案外加；對齊原始檔還原時間軸）
    if params["trim_jingle"]:
        output_path = _align_trim(output_path, audio_path, params["jingle_seconds"])

    return output_path
