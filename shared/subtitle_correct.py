"""SRT 校正統一入口（SRT-in / SRT-out）— 吃任何來源的既有字幕檔。

兩種模式（依參考材料自動選擇）：
- **scripted**：有完整逐字稿（照稿錄影）→ difflib 對稿，文字以稿為準、
  時間軸保留原 SRT，零 LLM 零成本。cue 切分不變。
- **llm**：參考有限（訪綱、準備報告）→ Opus 分段（chunked）校正 +
  可選 Gemini 多模態仲裁。與 /transcribe pipeline 共用 prompt 與
  仲裁機器（shared/transcriber.py、shared/multimodal_arbiter.py）。

相較 transcriber._correct_with_llm 的差異：
- 輸入是 SRT 字串而非 pipeline 內部狀態 → 外部工具產的字幕也能校
- 參考資料每檔上限 20000 字元（transcriber 舊路徑是 3000，訪綱/報告會被砍）
- 長逐字稿分 chunk 送（每 chunk 獨立 max_tokens），不再有單次 16384 截斷風險
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from shared.transcriber import (
    _add_pinyin,
    _apply_arbitration_verdicts,
    _build_correction_system,
    _extract_srt_texts,
    _parse_llm_response,
    _replace_srt_texts,
)

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 150  # 每次 LLM 呼叫的 cue 數
DEFAULT_REF_CHAR_CAP = 20000  # 每份參考資料的注入上限（字元）
SCRIPTED_CUE_MATCH_THRESHOLD = 0.5  # cue 內字元對齊率低於此值 → 保留原文並進報告

# 對稿模式重建 cue 文字時的標點清理（SRT house style：無標點、停頓用空格）
_PUNCT_RE = re.compile(r"[，。、；：？！…—,.;:!?\"'“”‘’()（）\[\]【】]")


# ── 參考資料載入 ──


def load_refs_text(ref_files: list[str | Path], char_cap: int = DEFAULT_REF_CHAR_CAP) -> str:
    """讀參考資料檔，每檔截至 char_cap 並標注截斷。"""
    parts: list[str] = []
    for fpath in ref_files:
        p = Path(fpath)
        if not p.exists():
            logger.warning(f"參考資料不存在，略過: {p}")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > char_cap
        body = text[:char_cap]
        note = f"\n（⋯已截斷，原文 {len(text)} 字元）" if truncated else ""
        parts.append(f"--- {p.name} ---\n{body}{note}")
    return "\n\n".join(parts)


# ── LLM 模式 ──


def correct_srt_llm(
    srt_content: str,
    *,
    ref_files: list[str | Path] | None = None,
    model: str = "claude-opus-4-7",
    host_name: str = "",
    show_name: str = "",
    audio_path: Path | None = None,
    use_arbitration: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    ref_char_cap: int = DEFAULT_REF_CHAR_CAP,
    run_id: int | None = None,
) -> tuple[str, list[dict], dict]:
    """Opus 分段校正既有 SRT（+ 可選 Gemini 仲裁）。

    Returns:
        (corrected_srt, qc_items, stats)
    """
    from shared.llm import ask

    entries = _extract_srt_texts(srt_content)
    if not entries:
        logger.warning("SRT 無文字行，跳過校正")
        return srt_content, [], {"cues": 0, "chunks": 0, "corrections": 0}

    context_parts: list[str] = []
    refs_text = load_refs_text(ref_files or [], char_cap=ref_char_cap)
    if refs_text:
        context_parts.append(refs_text)
    system = _build_correction_system(host_name, show_name, context_parts)

    corrections: dict[int, str] = {}
    uncertainties: list[dict] = []
    chunks = [entries[i : i + chunk_size] for i in range(0, len(entries), chunk_size)]
    logger.info(f"LLM 校正: {len(entries)} cues / {len(chunks)} chunks（模型 {model}）")

    for idx, chunk in enumerate(chunks, start=1):
        numbered = "\n".join(f"[{seq}] {_add_pinyin(text)}" for seq, text in chunk)
        prompt = f"請校正以下語音辨識逐字稿：\n\n{numbered}"
        raw = ask(prompt, system=system, model=model, max_tokens=16384)
        chunk_corr, chunk_unc = _parse_llm_response(raw, len(chunk))
        # 防 LLM 越界：只收本 chunk 序號範圍內的修正
        valid_seqs = {seq for seq, _ in chunk}
        chunk_corr = {k: v for k, v in chunk_corr.items() if k in valid_seqs}
        chunk_unc = [u for u in chunk_unc if u.get("line") in valid_seqs]
        corrections.update(chunk_corr)
        uncertainties.extend(chunk_unc)
        logger.info(
            f"chunk {idx}/{len(chunks)}: {len(chunk_corr)} 修正, {len(chunk_unc)} uncertain"
        )

    qc_items: list[dict] = uncertainties
    if use_arbitration and uncertainties and audio_path is not None:
        try:
            from shared.multimodal_arbiter import arbitrate_uncertain

            pre_arb_srt = _replace_srt_texts(srt_content, corrections)
            verdicts = arbitrate_uncertain(audio_path, pre_arb_srt, uncertainties, run_id=run_id)
            corrections, qc_items = _apply_arbitration_verdicts(
                corrections, uncertainties, verdicts
            )
            logger.info(f"多模態仲裁完成: {len(verdicts)} verdicts, {len(qc_items)} 進 QC")
        except Exception as e:
            logger.warning(f"多模態仲裁失敗，退回單輪結果: {type(e).__name__}: {e}")
            qc_items = uncertainties
    elif use_arbitration and uncertainties and audio_path is None:
        logger.info("無音檔，跳過多模態仲裁")

    stats = {
        "mode": "llm",
        "cues": len(entries),
        "chunks": len(chunks),
        "corrections": len(corrections),
        "uncertain": len(uncertainties),
        "qc": len(qc_items),
        "arbitrated": use_arbitration and bool(uncertainties) and audio_path is not None,
    }
    return _replace_srt_texts(srt_content, corrections), qc_items, stats


# ── 對稿（scripted）模式 ──


def _normalize_stream(text: str) -> tuple[str, list[int]]:
    """NFKC 正規化 + 只留 CJK/英數字元，回傳 (正規化字串, 每字元對應的原文 index)。"""
    norm_chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        for nch in unicodedata.normalize("NFKC", ch):
            if nch.isalnum():
                norm_chars.append(nch.lower())
                index_map.append(i)
    return "".join(norm_chars), index_map


def _clean_cue_text(text: str) -> str:
    """稿上文字轉 SRT house style：標點 → 空格、頭尾去空白、空格塌縮。"""
    cleaned = _PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def correct_srt_scripted(srt_content: str, script_text: str) -> tuple[str, list[dict], dict]:
    """用完整逐字稿校正既有 SRT：文字以稿為準、時間軸與 cue 切分保留。

    演算法：SRT 全部 cue 文字串成字元流 ↔ 稿字元流跑 SequenceMatcher；
    每個 cue 依其字元 span 對應到稿上的區段，重建文字。對齊率低於
    SCRIPTED_CUE_MATCH_THRESHOLD 的 cue 保留 ASR 原文並列入報告。

    Returns:
        (corrected_srt, flagged_items, stats)
    """
    entries = _extract_srt_texts(srt_content)
    if not entries:
        return srt_content, [], {"cues": 0}

    # ASR 字元流（記每個正規化字元屬於哪個 cue）
    asr_norm_parts: list[str] = []
    cue_spans: list[tuple[int, int, int]] = []  # (seq, start, end) in 正規化流
    pos = 0
    for seq, text in entries:
        norm, _ = _normalize_stream(text)
        asr_norm_parts.append(norm)
        cue_spans.append((seq, pos, pos + len(norm)))
        pos += len(norm)
    asr_stream = "".join(asr_norm_parts)

    script_stream, script_index_map = _normalize_stream(script_text)

    matcher = SequenceMatcher(None, asr_stream, script_stream, autojunk=False)
    # asr index → script index（只記 matching blocks 內的）
    asr_to_script: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            asr_to_script[block.a + offset] = block.b + offset

    corrections: dict[int, str] = {}
    flagged: list[dict] = []
    matched_total = 0
    prev_script_end = 0  # 單調防重複：cue 間的稿區段不回頭

    for (seq, a_start, a_end), (_, orig_text) in zip(cue_spans, entries):
        cue_len = a_end - a_start
        if cue_len == 0:
            continue
        matched_idx = [asr_to_script[i] for i in range(a_start, a_end) if i in asr_to_script]
        matched_total += len(matched_idx)
        coverage = len(matched_idx) / cue_len

        if coverage < SCRIPTED_CUE_MATCH_THRESHOLD:
            flagged.append(
                {
                    "line": seq,
                    "original": orig_text,
                    "coverage": round(coverage, 2),
                    "reason": "cue 與稿對齊率過低（可能是稿外內容或 ASR 錯太多），保留 ASR 原文",
                }
            )
            continue

        s_start = max(min(matched_idx), prev_script_end)
        s_end = max(matched_idx) + 1
        if s_end <= s_start:
            flagged.append(
                {
                    "line": seq,
                    "original": orig_text,
                    "coverage": round(coverage, 2),
                    "reason": "稿區段與前一 cue 重疊（順序異常），保留 ASR 原文",
                }
            )
            continue
        prev_script_end = s_end

        raw_start = script_index_map[s_start]
        raw_end = script_index_map[s_end - 1] + 1
        new_text = _clean_cue_text(script_text[raw_start:raw_end])
        if new_text and new_text != orig_text:
            corrections[seq] = new_text

    overall_coverage = matched_total / len(asr_stream) if asr_stream else 0.0
    if overall_coverage < 0.5:
        logger.warning(
            f"整體對齊率僅 {overall_coverage:.0%} — 逐字稿與這份 SRT 差異很大，結果請人工確認"
        )

    stats = {
        "mode": "scripted",
        "cues": len(entries),
        "corrections": len(corrections),
        "flagged": len(flagged),
        "coverage": round(overall_coverage, 3),
    }
    return _replace_srt_texts(srt_content, corrections), flagged, stats


# ── 報告 ──


def build_correction_report(stats: dict, qc_items: list[dict]) -> str:
    """校正報告 Markdown：統計 + 需人工確認清單（兩種模式共用）。"""
    lines = ["# 字幕校正報告\n"]
    lines.append("## 統計\n")
    for key, label in [
        ("mode", "模式"),
        ("cues", "cue 總數"),
        ("chunks", "LLM chunks"),
        ("corrections", "修正行數"),
        ("uncertain", "uncertain 行數"),
        ("qc", "進 QC 行數"),
        ("flagged", "低對齊率行數"),
        ("coverage", "整體對齊率"),
        ("arbitrated", "多模態仲裁"),
    ]:
        if key in stats:
            lines.append(f"- {label}：{stats[key]}")
    lines.append("")

    if qc_items:
        lines.append("## 需人工確認\n")
        for item in qc_items:
            line_no = item.get("line", "?")
            if "verdict" in item:  # LLM + 仲裁
                lines.append(
                    f"### [{item.get('risk', 'medium').upper()} | {item['verdict']}"
                    f" | conf {item.get('confidence', 0.0):.2f}] Line {line_no}"
                )
                lines.append(f"- **ASR 原文**：{item.get('original', '')}")
                lines.append(f"- **Opus 建議**：{item.get('suggestion', '')}")
                lines.append(f"- **Opus 理由**：{item.get('reason', '')}")
                lines.append(f"- **仲裁採用**：{item.get('final_text', '')}")
                lines.append(f"- **Gemini 理由**：{item.get('gemini_reasoning', '')}")
            elif "coverage" in item:  # scripted 低對齊
                lines.append(f"### [coverage {item['coverage']}] Line {line_no}")
                lines.append(f"- **ASR 原文**：{item.get('original', '')}")
                lines.append(f"- **原因**：{item.get('reason', '')}")
            else:  # LLM 無仲裁
                lines.append(f"### [{item.get('risk', 'medium').upper()}] Line {line_no}")
                lines.append(f"- **原文**：{item.get('original', '')}")
                lines.append(f"- **建議**：{item.get('suggestion', '')}")
                lines.append(f"- **理由**：{item.get('reason', '')}")
            lines.append("")
    else:
        lines.append("## 需人工確認\n\n（無）\n")

    return "\n".join(lines)
