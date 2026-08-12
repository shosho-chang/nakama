"""音檔停頓圖——斷句的**主判準**（修修 2026-08-12「詞庫永遠修不完」裁決）。

## 為什麼需要這個模組

`words.json` 的字級時間戳是**連續的**：99.2% 的相鄰字 `end[i] == start[i+1]`。
WhisperX align 把靜音攤進了字長，停頓資訊在對齊那一步就沒了。整條 pipeline
因此看不到任何停頓，只能退而用「jieba 詞界」當代理判準——而詞典永遠不會涵蓋
集別詞彙：2026-08-12 安吉 SL3 的「冒牌者」`FREQ=None`，jieba 切成「冒牌｜者」，
於是「…完全沒有冒牌」｜「者的問題…」被四條規則一致判為**乾淨切點**出貨。

詞庫是無底洞（修修：「如果需要建立詞庫的話，那永遠都修不完」）。本模組改問
物理問題：**這裡有沒有停頓**。人講一個詞時中間不會換氣，所以「切點必須落在
靜音上」在物理上就擋掉了詞被切一半，完全不需要知道那是什麼詞。

實測（安吉 20260415，1884 個 cue 切點）：
- 真停頓切點 RMS ~0.0002；全片中位數 0.0047；「冒牌｜者」0.017（比 82.6% 更吵）
- 最吵的 10 個切點，舊四規則 **10 個全部放行**
- 導入靜音判準後 RMS>noisy 的切點 771 → 37（減 95%）

殘餘的 ~2% 是說話者真的沒停頓（安吉語速快時整段連著講），靠封閉類詞素規則
（`subtitle_finalize._HEAD_STICKY`）與人工判讀收尾——**那是封閉集合，不是詞庫**。

## 時鐘

`PauseMap` 只認**音檔時鐘**。cue 時間若不同鐘（緊湊化後的 tight SRT 是
timeline 時鐘），呼叫端要傳 `to_audio`。血淚：本集 `normalized.wav` 有兩份
（episode 根目錄 8/04 與 `program_v2/` 8/07）差 **71.01 秒**，`transcript.srt`
用的是 program_v2 那份——傳錯音檔會整份對錯而且不會報錯，所以有
`sanity_check()`：cue 切點整體必須比 cue 中點安靜，否則 raise。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

SR = 8000  # 單聲道降頻——只要能量包絡，不需要保真
FRAME = 0.02  # 20ms 一格（比最短的字還短）
WIN = 0.06  # 判定切點安靜度時取 ±60ms 內最安靜的一格

# 門檻由**該集自己的靜音底噪**推導，不是硬寫的魔術數字：
# 底噪 = 包絡 P5（真正沒人講話的地方），說話 = 包絡 P50。
QUIET_MULT = 5  # ≤ 底噪×5 → 真停頓，切在這裡一定安全
NOISY_MULT = 20  # > 底噪×20 → 切在連續發聲中間，可疑


class PauseMap:
    """一集的 20ms RMS 包絡 + 由底噪自校準的安靜／吵門檻。"""

    def __init__(self, env: np.ndarray, *, to_audio: Callable[[float], float] | None = None):
        if env.ndim != 1 or not len(env):
            raise ValueError("包絡必須是非空一維陣列")
        self.env = env
        self._to_audio = to_audio
        self.noise_floor = float(np.percentile(env, 5))
        self.speech = float(np.percentile(env, 50))
        self.quiet = self.noise_floor * QUIET_MULT
        self.noisy = self.noise_floor * NOISY_MULT

    def __len__(self) -> int:
        return len(self.env)

    @property
    def duration(self) -> float:
        return len(self.env) * FRAME

    def floor(self, t: float, win: float = WIN) -> float:
        """cue 時間 t 附近最安靜的一格 RMS（越小＝越適合當切點）。"""
        ta = self._to_audio(t) if self._to_audio else t
        a = max(0, int((ta - win) / FRAME))
        b = min(len(self.env), int((ta + win) / FRAME) + 1)
        if a >= b:
            return float("inf")
        return float(self.env[a:b].min())

    def is_quiet(self, t: float) -> bool:
        return self.floor(t) <= self.quiet

    def is_noisy(self, t: float) -> bool:
        return self.floor(t) > self.noisy

    def sanity_check(self, boundaries: list[float], mids: list[float]) -> float:
        """時鐘防呆：切點整體應比 cue 中點安靜。回傳比值（<1 才合理）。

        對錯音檔／對錯時鐘時，切點與中點會統計上無異（比值 ~1）——這是唯一
        能自動抓到「71.01 秒」那類災難的訊號，不做就會整份靜默對錯。
        """
        if len(boundaries) < 20 or len(mids) < 20:
            return 0.0
        b = float(np.median([self.floor(t) for t in boundaries]))
        m = float(np.median([self.floor(t) for t in mids]))
        ratio = b / max(m, 1e-9)
        if ratio > 0.8:
            raise ValueError(
                f"停頓圖時鐘對不上：cue 切點的中位安靜度({b:.5f}) 幾乎等於 "
                f"cue 中點({m:.5f})，比值 {ratio:.2f}。音檔或時鐘映射錯了。"
            )
        return ratio


def build_envelope(audio: Path, cache: Path | None = None, *, force: bool = False) -> np.ndarray:
    """音檔 → 20ms RMS 包絡（ffmpeg 解碼，結果快取成 .npy）。

    ~4700s 的訪談約 235k 格、不到 1MB，首次 ~20s，之後秒載。
    """
    audio = Path(audio)
    if cache is not None:
        cache = Path(cache)
        if cache.exists() and not force:
            env = np.load(cache)
            logger.info("停頓圖快取命中 %s（%.1fs）", cache.name, len(env) * FRAME)
            return env
    if not audio.exists():
        raise FileNotFoundError(f"找不到音檔：{audio}")
    logger.info("解碼音檔算停頓圖：%s", audio)
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 解碼失敗：{p.stderr[:400].decode('utf-8', 'replace')}")
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    f = int(FRAME * SR)
    if len(x) < f:
        raise ValueError(f"音檔太短（{len(x)/SR:.2f}s）")
    env = np.sqrt((x[: len(x) // f * f].reshape(-1, f) ** 2).mean(axis=1))
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, env)
        logger.info("停頓圖 → %s（%.1fs, %d 格）", cache, len(env) * FRAME, len(env))
    return env


def cache_path_for(audio: Path, subs_dir: Path | None = None) -> Path:
    """快取檔名**必須帶音檔名**——同一集可能有多個不同時鐘的 `normalized.wav`
    （本集根目錄與 `program_v2/` 差 71.01s）。共用 `pause_map.npy` 這種名字，
    後來者會讀到別條時鐘的包絡，而且完全不會報錯。
    """
    audio = Path(audio)
    base = subs_dir if subs_dir is not None else audio.parent / "subs"
    return Path(base) / f"pause_map_{audio.stem}.npy"


def _probe_envelope(audio: Path, start: float, dur: float, sr: int = 1000) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(audio),
         "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 取樣失敗：{p.stderr[:300].decode('utf-8', 'replace')}")
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    f = int(FRAME * sr)
    if len(x) < f:
        return np.array([])
    return np.sqrt((x[: len(x) // f * f].reshape(-1, f) ** 2).mean(axis=1))


def media_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe 失敗：{p.stderr[:300]}")
    return float(p.stdout.strip())


def detect_audio_offset(
    ref: Path,
    other: Path,
    *,
    probes: tuple[float, ...] = (0.1, 0.3, 0.55, 0.8),
    window: float = 8.0,
    search: float = 180.0,
    tol: float = 0.05,
) -> float:
    """`other` 相對 `ref` 的時間偏移：`other_t = ref_t + offset`。

    做法是音量包絡互相關——**不靠文字、不靠逐字稿**，所以逐字稿換版本也不影響。
    在多個探測點各量一次，全部一致（離散 ≤ tol）才回傳，否則 raise：偏移不是
    常數代表兩個檔不是同一段錄音的平移（可能中間剪過），硬套會整份對錯。

    起因（2026-08-12）：本集 `normalized.wav` 有兩份差 71.01s，`cuts.json` 用
    根目錄那份、`transcript.srt` 用 `program_v2/` 那份。先前靠文字錨點反推，
    SL3 對得上（169 個錨點全等）但 SL4/SL7 的 SRT 出自更早版本的逐字稿，錨點
    散到 ±0.3s——而停頓判定的窗只有 ±60ms。改量音檔就沒有這個問題。
    """
    dur = min(media_duration(ref), media_duration(other))
    offs: list[float] = []
    for frac in probes:
        t = dur * frac
        a = _probe_envelope(ref, t, window)
        b = _probe_envelope(other, max(0.0, t - search), window + 2 * search)
        if len(a) < 50 or len(b) < len(a) + 10:
            continue
        a = (a - a.mean()) / (a.std() + 1e-9)
        bb = (b - b.mean()) / (b.std() + 1e-9)
        cc = np.correlate(bb, a, mode="valid") / len(a)
        k = int(cc.argmax())
        if float(cc[k]) < 0.5:  # 相關太低＝這個窗沒東西可對（靜音段）
            continue
        offs.append(k * FRAME - min(search, t))
    if len(offs) < 2:
        raise ValueError(f"音檔對齊探測點不足（{len(offs)} 個有效）——兩個檔不是同一段錄音？")
    spread = max(offs) - min(offs)
    off = sorted(offs)[len(offs) // 2]
    logger.info("音檔偏移 %+.3fs（%d 個探測點，離散 %.3fs）", off, len(offs), spread)
    if spread > tol:
        raise ValueError(
            f"音檔偏移不是常數（離散 {spread:.3f}s > {tol}s）：{[round(o,3) for o in offs]}"
            "——兩個檔中間被剪過，不能用單一偏移換算"
        )
    return off


def load_for_episode(
    episode_dir: Path,
    *,
    audio_name: str = "normalized.wav",
    to_audio: Callable[[float], float] | None = None,
) -> PauseMap | None:
    """慣例路徑載入：`<episode>/<audio_name>`，快取在 `<episode>/subs/pause_map.npy`。

    找不到音檔回 None——呼叫端**必須**把這件事回報出來（沒有停頓圖＝退回
    舊的詞典判準，那正是已知會漏詞的路徑，不可當成通過）。
    """
    episode_dir = Path(episode_dir)
    audio = episode_dir / audio_name
    if not audio.exists():
        return None
    env = build_envelope(audio, cache_path_for(audio, episode_dir / "subs"))
    return PauseMap(env, to_audio=to_audio)
