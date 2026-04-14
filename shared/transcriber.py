"""語音轉繁體中文字幕：基於 openlrc + faster-whisper 的本地 ASR pipeline。

獨立模組，可被任何 agent 調用。
支援 context 注入（提升專有名詞準確度）和可選的 LLM 校正。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.transcriber")

# 中文標點符號 pattern（用於標點移除）
_ZH_PUNCTUATION = re.compile(r"[，。！？、；：""''（）《》【】…—～·]")

# SRT 時間戳格式
_SRT_TS_FMT = "{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# OpenCC lazy singleton（避免重複載入字典）
_cc_s2t = None


def _get_cc():
    """取得 OpenCC s2t converter（lazy singleton）。"""
    global _cc_s2t
    if _cc_s2t is None:
        from opencc import OpenCC

        _cc_s2t = OpenCC("s2t")
    return _cc_s2t


def _seconds_to_srt_ts(seconds: float) -> str:
    """將秒數轉為 SRT 時間戳格式 HH:MM:SS,mmm。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return _SRT_TS_FMT.format(h=h, m=m, s=s, ms=ms)


def _remove_punctuation(text: str) -> str:
    """移除中文標點符號，保留英文標點和空格。"""
    return _ZH_PUNCTUATION.sub("", text)


def _to_traditional(text: str) -> str:
    """簡體中文轉繁體中文（使用 OpenCC lazy singleton）。"""
    return _get_cc().convert(text)


def _build_initial_prompt(context_files: list[str | Path]) -> str:
    """從 context 檔案中提取內容，組成 initial_prompt。

    讀取每個檔案的前 500 字作為 context，幫助 Whisper 辨識專有名詞。
    """
    if not context_files:
        return "以下是繁體中文的內容。"

    snippets = []
    for fpath in context_files:
        p = Path(fpath)
        if not p.exists():
            logger.warning(f"Context file not found: {fpath}")
            continue
        text = p.read_text(encoding="utf-8")[:500]
        snippets.append(text)

    if not snippets:
        return "以下是繁體中文的內容。"

    combined = "\n".join(snippets)
    return f"以下是繁體中文的內容。相關背景資料：{combined}"


def _extract_hotwords(context_files: list[str | Path]) -> list[str]:
    """從 context 檔案的 frontmatter 或內容中提取專有名詞作為 hotwords。

    簡易策略：提取引號內的詞和大寫英文詞。
    """
    hotwords: list[str] = []

    for fpath in context_files:
        p = Path(fpath)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")[:2000]

        # 提取書名號內的詞
        hotwords.extend(re.findall(r"《(.+?)》", text))
        # 提取雙引號內的短詞（< 20 字元）
        for match in re.findall(r"「(.+?)」", text):
            if len(match) < 20:
                hotwords.append(match)

    return list(set(hotwords))[:50]  # 去重，最多 50 個


def _lrc_to_srt(lrc_path: Path) -> str:
    """將 LRC 格式轉為 SRT 格式字串。

    LRC 格式：[MM:SS.xx] 文字
    SRT 格式：序號 + 時間區間 + 文字
    """
    lines = lrc_path.read_text(encoding="utf-8").strip().splitlines()
    entries: list[tuple[float, str]] = []

    for line in lines:
        match = re.match(r"\[(\d+):(\d+\.\d+)\]\s*(.*)", line)
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()
            if text:
                entries.append((minutes * 60 + seconds, text))

    if not entries:
        logger.warning(f"LRC 檔案無有效字幕行：{lrc_path}")
        return ""

    srt_parts: list[str] = []
    for i, (start, text) in enumerate(entries, 1):
        # 結束時間 = 下一句開始時間，最後一句 +3 秒
        end = entries[i][0] if i < len(entries) else start + 3.0
        srt_parts.append(
            f"{i}\n{_seconds_to_srt_ts(start)} --> {_seconds_to_srt_ts(end)}\n{text}\n"
        )

    return "\n".join(srt_parts)


def _process_srt_line(line: str, *, use_punctuation: bool) -> str:
    """處理單行 SRT 內容：簡轉繁 + 標點控制。只作用於字幕文字行。"""
    stripped = line.strip()
    # 跳過空行、序號行、時間戳行
    if not stripped or stripped.isdigit() or "-->" in stripped:
        return line

    # 簡轉繁
    line = _to_traditional(line)

    # 標點控制
    if not use_punctuation:
        line = _remove_punctuation(line)
        # 移除標點後如果變空行，保留一個空格避免破壞 SRT 結構
        if not line.strip():
            line = " "

    return line


def transcribe(
    audio_path: str | Path,
    *,
    language: str = "zh",
    context_files: list[str | Path] | None = None,
    use_punctuation: bool = False,
    use_llm_correction: bool = False,
    llm_model: str = "claude-haiku-4-5-20251001",
    whisper_model: str = "large-v3",
    compute_type: str = "int8",
    output_dir: str | Path | None = None,
    noise_suppress: bool = False,
) -> Path:
    """語音轉繁體中文 SRT 字幕。

    Args:
        audio_path: 音檔路徑（MP3, WAV, M4A 等）
        language: 語言代碼（預設 "zh"）
        context_files: 參考資料檔案路徑列表（書、報導等），用於提升專有名詞準確度
        use_punctuation: 是否保留標點符號（預設 False）
        use_llm_correction: 是否啟用 LLM 校正（預設 False，啟用會產生 API 成本）
        llm_model: LLM 校正使用的模型
        whisper_model: Whisper 模型大小（tiny/base/small/medium/large-v3）
        compute_type: 計算精度（int8/float16/float32）
        output_dir: 輸出目錄（預設與音檔同目錄）
        noise_suppress: 是否啟用降噪（需要額外依賴）

    Returns:
        SRT 字幕檔的 Path
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    from openlrc import LRCer, TranscriptionConfig, TranslationConfig

    context_files = context_files or []
    output_dir = Path(output_dir) if output_dir else audio_path.parent

    logger.info(f"開始轉寫：{audio_path.name}")
    logger.info(f"模型：{whisper_model}（{compute_type}），語言：{language}")
    if context_files:
        logger.info(f"Context 檔案：{len(context_files)} 個")

    # ── 組裝 ASR 設定 ──
    initial_prompt = _build_initial_prompt(context_files)
    hotwords = _extract_hotwords(context_files)

    asr_options: dict = {
        "language": language,
        "initial_prompt": initial_prompt,
        "beam_size": 5,
        "word_timestamps": True,
        "condition_on_previous_text": True,
    }
    if hotwords:
        # faster-whisper 的 hotwords 參數是 string，用空格分隔
        asr_options["hotwords"] = " ".join(hotwords)
        logger.info(f"Hotwords：{hotwords[:10]}{'...' if len(hotwords) > 10 else ''}")

    transcription_config = TranscriptionConfig(
        asr_options=asr_options,
    )

    # ── 組裝 LLM 設定（可選）──
    translation_config = None
    skip_trans = True

    if use_llm_correction:
        from openlrc import ModelConfig, ModelProvider

        skip_trans = False
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        glossary = {}
        if hotwords:
            glossary = {w: w for w in hotwords}  # 保持原詞不被改動

        translation_config = TranslationConfig(
            chatbot_model=ModelConfig(
                provider=ModelProvider.ANTHROPIC,
                name=llm_model,
                api_key=api_key,
            ),
            glossary=glossary if glossary else None,
        )
        logger.info(f"LLM 校正已啟用：{llm_model}")

    # ── 執行轉寫 ──
    lrcer = LRCer(
        transcription=transcription_config,
        translation=translation_config,
    )

    result_paths = lrcer.run(
        str(audio_path),
        target_lang=language,
        noise_suppress=noise_suppress,
        skip_trans=skip_trans,
    )

    # ── 找到 openlrc 的輸出檔案 ──
    # 優先使用 run() 回傳的路徑
    if result_paths:
        source_path = Path(result_paths[0])
        srt_content = source_path.read_text(encoding="utf-8")
        # 如果回傳的是 LRC，轉換為 SRT
        if source_path.suffix == ".lrc":
            srt_content = _lrc_to_srt(source_path)
    else:
        # Fallback：手動搜尋
        lrc_output = audio_path.with_suffix(".lrc")
        srt_output = audio_path.with_suffix(".srt")

        if srt_output.exists():
            srt_content = srt_output.read_text(encoding="utf-8")
        elif lrc_output.exists():
            srt_content = _lrc_to_srt(lrc_output)
        else:
            raise FileNotFoundError(
                f"openlrc 未產生預期的輸出檔案（找不到 {lrc_output} 或 {srt_output}）"
            )

    # ── 後處理：逐行簡轉繁 + 標點控制（只作用於字幕文字行）──
    processed_lines = [
        _process_srt_line(line, use_punctuation=use_punctuation)
        for line in srt_content.splitlines()
    ]
    srt_content = "\n".join(processed_lines)

    # ── 寫入最終 SRT ──
    output_dir.mkdir(parents=True, exist_ok=True)
    final_srt = output_dir / f"{audio_path.stem}.srt"
    final_srt.write_text(srt_content, encoding="utf-8")

    # 清理 openlrc 的中間檔案（LRC 和原始 SRT）
    for suffix in (".lrc", ".srt"):
        intermediate = audio_path.with_suffix(suffix)
        if intermediate.exists() and intermediate != final_srt:
            intermediate.unlink()

    logger.info(f"完成！SRT 已儲存：{final_srt}")
    return final_srt
