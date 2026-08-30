"""多軌 mic 能量比對 → 詞級說話者判定（零模型、零 API）。

修修 2026-07-25 回饋：不同說話者的話被塞進同一個 cue，相當難閱讀。
錄音現場每人一軌 mic（如 Audio/1_COMBO-1.wav、2_COMBO-2.wav），比
pyannote 類 diarization 模型可靠得多。

判定用兩狀態 Viterbi：每個詞的證據 = 兩軌 dB 差（重疊搶話時證據自然
趨近 0），說話者切換付轉換成本、但切在語音停頓處成本大減。啟發式
規則（短 run 吸收／邊界推移）實測會被 crosstalk 污染，DP 一次解決：
單字誤翻（證據弱、無停頓）被轉換成本壓掉，邊界自動吸到停頓點。

用途：
- subtitle-gen（cue_builder）：說話者變更處強制斷句，raw.srt 從源頭切對
- run_speaker_split：已校正 transcript.srt 的事後切分（不動校正內容）
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ENV_SR = 4000  # 能量包絡取樣率（Hz）
FRAME = 200  # 50ms RMS 視窗（樣本數）
FRAME_SEC = FRAME / ENV_SR
# mic 軌偵測：activity（噪音底 +25dB 以上的時間占比）≥ 此值才算人聲軌
MIN_ACTIVE_FRACTION = 0.10
_PAD_SEC = 0.03  # 詞區間前後 padding（對齊誤差緩衝）

# --- Viterbi 參數（dB·秒 為成本單位）---
EVIDENCE_CLAMP_DB = 12.0  # 單詞 dB 差證據上限（防爆音主導）
SILENCE_FLOOR_DB = 15.0  # 兩軌都低於噪音底 + 此值 → 無證據（e=0）
SWITCH_PENALTY = 6.0  # 說話者切換基本成本
MAX_WORD_SEC = 0.4  # 單詞能量窗上限（end 被 align 拉長時的截斷）
# 上面那個 0.4s 是為 **WhisperX 詞級** token 調的。沒有 words.json 時，上游給的是
# memo 的**句級 segment**（本集中位長度 1.90s），0.4s 窗等於只看句子的前 21%——
# 句首常還壓著上一位的尾音，整句就被判給他（修修 2026-08-30：來賓講「所以玩其實
# 就是一種學習」時畫面切到主持人 1.68s。前 0.4s 算出 e=+4.98 判主持人，
# 完整 1.64s 窗算出 e=-8.81 判來賓）。
WORD_MEDIAN_MAX_SEC = 0.8  # token 中位長度超過此值 → 輸入是句級 segment，不是詞
SEGMENT_MAX_SEC = 3.0  # 句級 token 的能量窗上限（仍夾在 token 自己的 end 內）
GAP_DISCOUNT = 12.0  # 每秒停頓折抵的切換成本（0.5s 停頓 → 免費切換）
MIN_SWITCH_PENALTY = 0.8  # 折抵後的最低切換成本（連音搶話仍需此證據量）


def _rms_envelope(path: Path) -> np.ndarray:
    """單軌 → 50ms RMS 包絡（ffmpeg decode，全長約 3800s → ~76k frames）。"""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(ENV_SR),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
    ).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt((x[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1))


def _active_fraction(env: np.ndarray) -> float:
    if len(env) == 0:
        return 0.0
    db = 20 * np.log10(env + 1e-9)
    floor = np.percentile(db, 10)
    return float((db > floor + 25).mean())


def detect_mic_tracks(audio_dir: Path) -> list[Path]:
    """Audio/ 內自動找說話者 mic 軌：排除 mix 軌，取 activity 最高的兩軌。

    回傳依檔名排序（軌序穩定 → speaker index 穩定）。找不到兩軌人聲
    回傳空 list（單人或無分軌收音，不做 speaker split）。
    """
    candidates = [p for p in sorted(audio_dir.glob("*.wav")) if "mix" not in p.name.lower()]
    scored: list[tuple[float, Path]] = []
    for p in candidates:
        frac = _active_fraction(_rms_envelope(p))
        logger.info(f"mic 偵測: {p.name} activity {frac * 100:.1f}%")
        if frac >= MIN_ACTIVE_FRACTION:
            scored.append((frac, p))
    if len(scored) < 2:
        return []
    top = sorted(scored, key=lambda t: -t[0])[:2]
    return sorted(p for _, p in top)


def _measure_offset(reference: Path, stem: Path) -> float | None:
    """stem 對 reference 的時間偏移（FFT 互相關，多窗共識）。

    RODECaster 類 live mix 有固定處理延遲（本案實測 +0.167s）——詞時間戳
    在 reference（normalized/mix）時間軸上，直接拿去取 stem 能量會偷看
    未來，輪替邊界一致偏早。回傳 off：reference[t] ≈ stem[t - off]；
    量不到可靠值回傳 None（呼叫端可借用同錄音機另一軌的值）。
    """
    sr = 16000

    def _load(path: Path, start: float, dur: float) -> np.ndarray:
        raw = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "quiet",
                "-ss",
                str(start),
                "-t",
                str(dur),
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                str(sr),
                "-f",
                "f32le",
                "-",
            ],
            capture_output=True,
        ).stdout
        return np.frombuffer(raw, dtype=np.float32)

    measures: list[tuple[float, float]] = []  # (off, peak)
    for at_sec in (600.0, 1800.0, 3000.0):
        a = _load(reference, at_sec, 60)
        b = _load(stem, at_sec, 60)
        if len(a) < sr or len(b) < sr:
            continue
        n = len(a) + len(b)
        corr = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
        corr = np.concatenate([corr[-(len(b) - 1) :], corr[: len(a)]])
        lag = int(np.argmax(np.abs(corr))) - (len(b) - 1)
        peak = float(np.abs(corr).max() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        off = lag / sr
        if peak >= 0.15 and abs(off) <= 2.0:
            measures.append((off, peak))
    if len(measures) >= 2 and max(o for o, _ in measures) - min(o for o, _ in measures) <= 0.01:
        off = float(np.mean([o for o, _ in measures]))  # 多窗一致 → 可靠
    elif measures:
        off = max(measures, key=lambda t: t[1])[0]  # 取 peak 最高的一窗
        if max(p for _, p in measures) < 0.3:
            peaks = [f"{p:.2f}" for _, p in measures]
            logger.warning(f"{stem.name} 偏移量測不可靠（peaks {peaks}）")
            return None
    else:
        logger.warning(f"{stem.name} 偏移量測失敗，視為未知")
        return None
    logger.info(f"stem 偏移: {stem.name} {off:+.4f}s（{len(measures)} 窗）")
    return off


def load_envelopes(mic_paths: list[Path], reference: Path | None = None) -> np.ndarray:
    """mic 軌 → [2, n_frames] RMS 矩陣（frame = 50ms）。

    reference（詞時間戳的音檔，通常 normalized.wav）給定時，量測並補償
    各 stem 的時間偏移，使 envelope 索引落在 reference 時間軸上。
    """
    offsets: list[float | None] = [None] * len(mic_paths)
    if reference is not None:
        offsets = [_measure_offset(reference, p) for p in mic_paths]
        known = [o for o in offsets if o is not None]
        # 同一台錄音機的 stem 偏移必然相同：量不到的軌借用其他軌的值
        fallback = float(np.mean(known)) if known else 0.0
        offsets = [o if o is not None else fallback for o in offsets]
    envs = []
    for p, off in zip(mic_paths, offsets):
        e = _rms_envelope(p)
        k = int(round((off or 0.0) / FRAME_SEC))
        if k > 0:  # reference[t] = stem[t - off] → envelope 右移 k 格
            e = np.concatenate([np.full(k, e[0] if len(e) else 0.0, dtype=e.dtype), e[:-k]])
        elif k < 0:
            e = np.concatenate([e[-k:], np.full(-k, e[-1] if len(e) else 0.0, dtype=e.dtype)])
        envs.append(e)
    n = min(len(e) for e in envs)
    return np.stack([e[:n] for e in envs])


def _evidence_span(durations: list[float]) -> float:
    """依 token 粒度選能量窗上限：詞級 0.4s、句級 3.0s。

    粒度用**中位長度**判（不是平均——長獨白會把平均拉高，中位穩定）。
    WhisperX 詞約 0.2–0.4s；memo 的句級 segment 通常 1.5–2.5s。
    """
    if not durations:
        return MAX_WORD_SEC
    median = float(np.median(durations))
    return SEGMENT_MAX_SEC if median > WORD_MEDIAN_MAX_SEC else MAX_WORD_SEC


def assign_word_speakers(words: list[dict], envelopes: np.ndarray) -> list[int | None]:
    """每個詞 → speaker index（envelopes 軌序 0/1），Viterbi 全域最佳化。

    words: [{"word", "start", "end"}, ...]；缺時間戳的詞承接前一詞判定。
    回傳與 words 等長；整段皆無證據時為 None。
    """
    n_frames = envelopes.shape[1]
    db = 20 * np.log10(envelopes + 1e-9)
    floors = np.percentile(db, 10, axis=1)

    timed = [
        (k, float(w["start"]), float(w["end"]))
        for k, w in enumerate(words)
        if w.get("start") is not None and w.get("end") is not None
    ]
    if not timed:
        return [None] * len(words)

    # WhisperX 的詞 end 常被拉長到下一詞 start（詞間 gap 恆 0），兩個對策：
    # (1) 能量窗上限——超過的部分是靜音或下一位的聲音，不能採
    # (2) 停頓不能信時間戳，改實測「兩軌皆趴在噪音底」的靜音長度
    #
    # 上限依**實測的 token 粒度**選：詞級用 MAX_WORD_SEC，句級用 SEGMENT_MAX_SEC。
    # 粒度是量出來的（中位長度），不是猜的——同一份程式要同時吃 WhisperX 詞與
    # memo 句級 segment，用詞級的窗去看句子會把整句判給句首壓到的那個人。
    max_span = _evidence_span([end - start for _k, start, end in timed])
    both_silent = (db[0] < floors[0] + SILENCE_FLOOR_DB) & (db[1] < floors[1] + SILENCE_FLOOR_DB)

    evidence: list[float] = []
    gaps: list[float] = []  # 與前一詞之間的實測靜音（秒）
    prev_cap: float | None = None  # 前一詞的截斷後結束時間
    for _k, start, end in timed:
        cap_end = min(end, start + max_span)
        f0 = max(0, int((start - _PAD_SEC) / FRAME_SEC))
        f1 = min(n_frames, int((cap_end + _PAD_SEC) / FRAME_SEC) + 1)
        if f1 <= f0:
            e = 0.0
        else:
            level = db[:, f0:f1].mean(axis=1)
            if level[0] < floors[0] + SILENCE_FLOOR_DB and level[1] < floors[1] + SILENCE_FLOOR_DB:
                e = 0.0  # 兩軌皆近無聲：無證據
            else:
                e = float(np.clip(level[0] - level[1], -EVIDENCE_CLAMP_DB, EVIDENCE_CLAMP_DB))
        dur_w = min(max(cap_end - start, 0.08), 0.6)  # 時長權重（防超短詞雜訊、防長詞獨裁）
        evidence.append(e * dur_w)
        if prev_cap is None:
            gaps.append(0.0)
        else:
            g0 = max(0, int(prev_cap / FRAME_SEC))
            g1 = min(n_frames, int(start / FRAME_SEC) + 1)
            span = max(0.0, start - prev_cap)
            gaps.append(float(both_silent[g0:g1].mean()) * span if g1 > g0 else 0.0)
        prev_cap = cap_end

    # Viterbi：cost[spk] 累積；切換成本隨停頓折抵
    n = len(timed)
    cost = np.zeros((n, 2))
    back = np.zeros((n, 2), dtype=np.int8)
    cost[0, 0] = -evidence[0]
    cost[0, 1] = evidence[0]
    for i in range(1, n):
        pen = max(MIN_SWITCH_PENALTY, SWITCH_PENALTY - GAP_DISCOUNT * gaps[i])
        for s in (0, 1):
            emit = -evidence[i] if s == 0 else evidence[i]
            stay = cost[i - 1, s]
            switch = cost[i - 1, 1 - s] + pen
            # tie 取 switch：邊界越晚越好——句尾輕聲字（證據 0）歸把句子講完的人
            if switch <= stay:
                cost[i, s] = switch + emit
                back[i, s] = 1 - s
            else:
                cost[i, s] = stay + emit
                back[i, s] = s
    path = np.zeros(n, dtype=np.int8)
    path[-1] = int(np.argmin(cost[-1]))
    for i in range(n - 2, -1, -1):
        path[i] = back[i + 1, path[i + 1]]

    out: list[int | None] = [None] * len(words)
    for (k, _s, _e), spk in zip(timed, path):
        out[k] = int(spk)
    # 缺時間戳的詞承接前一詞
    last: int | None = None
    for k in range(len(out)):
        if out[k] is None:
            out[k] = last
        else:
            last = out[k]
    return out
