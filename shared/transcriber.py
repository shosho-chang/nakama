"""語音轉繁體中文字幕：基於 FunASR Paraformer-zh 的本地 ASR pipeline。

獨立模組，可被任何 agent 調用。
支援 Auphonic 雲端前處理（normalization + 降噪）、
FunASR 本地辨識（VAD + 時間戳 + 標點 + Hotword），
以及可選的 LLM 校正。
"""

from __future__ import annotations

import re
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.transcriber")

# 中文句中標點 → 替換為空格
_ZH_MID_PUNCTUATION = re.compile(r"[，、；：" "''（）《》【】…—～·]")
# 中文句尾標點 → 直接移除
_ZH_END_PUNCTUATION = re.compile(r"[。！？]")

# 中文句尾標點（用於拆分句子）
_SENTENCE_END = re.compile(r"([。！？])")

# 中文逗號等次級斷點（用於長句再拆分）
_CLAUSE_BREAK = re.compile(r"([，、；：])")

# 字幕每行最大字數
_MAX_SUBTITLE_CHARS = 20

# SRT 時間戳格式
_SRT_TS_FMT = "{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# OpenCC lazy singleton（避免重複載入字典）
_cc_s2t = None

# FunASR model lazy singleton
_asr_model = None
_asr_model_id = None


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
    """中文句中標點→空格，句尾標點→移除，再壓縮連續空格。"""
    text = _ZH_MID_PUNCTUATION.sub(" ", text)
    text = _ZH_END_PUNCTUATION.sub("", text)
    return re.sub(r" {2,}", " ", text).strip()


def _to_traditional(text: str) -> str:
    """簡體中文轉繁體中文（使用 OpenCC lazy singleton）。"""
    return _get_cc().convert(text)


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


def _load_context_text(context_files: list[str | Path]) -> str:
    """讀取 context 檔案內容，用於 LLM 校正的 system prompt。"""
    if not context_files:
        return ""

    parts = []
    for fpath in context_files:
        p = Path(fpath)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")[:3000]
        parts.append(f"--- {p.name} ---\n{text}")

    return "\n\n".join(parts)


def _extract_srt_texts(srt_content: str) -> list[tuple[int, str]]:
    """從 SRT 內容中提取 (序號, 文字) 列表，跳過時間戳和空行。"""
    entries: list[tuple[int, str]] = []
    lines = srt_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 找序號行
        if line.isdigit():
            seq = int(line)
            # 下一行應是時間戳
            if i + 1 < len(lines) and "-->" in lines[i + 1]:
                # 再下一行是文字（可能多行）
                text_lines = []
                j = i + 2
                while j < len(lines) and lines[j].strip():
                    text_lines.append(lines[j].strip())
                    j += 1
                if text_lines:
                    entries.append((seq, " ".join(text_lines)))
                i = j
                continue
        i += 1
    return entries


def _replace_srt_texts(srt_content: str, corrected: dict[int, str]) -> str:
    """將校正後的文字替換回 SRT，保持時間戳不變。"""
    lines = srt_content.splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.isdigit() and i + 1 < len(lines) and "-->" in lines[i + 1]:
            seq = int(line)
            result.append(lines[i])  # 序號
            result.append(lines[i + 1])  # 時間戳
            # 跳過原始文字行
            j = i + 2
            while j < len(lines) and lines[j].strip():
                j += 1
            # 插入校正後文字
            if seq in corrected:
                result.append(corrected[seq])
            else:
                # 沒有校正的行，保留原文
                for k in range(i + 2, j):
                    result.append(lines[k])
            # 加空行分隔
            result.append("")
            i = j + 1 if j < len(lines) and not lines[j].strip() else j
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _correct_with_llm(
    srt_content: str,
    *,
    context_files: list[str | Path],
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """用 Claude 一次讀完整份逐字稿，統一校正。

    策略：將整份 SRT 的文字行（帶序號）一次送給 Claude，
    連同使用者提供的 context（書、報導、人名等），
    讓 Claude 在理解全文的前提下統一修正專有名詞和錯字。
    """
    from shared.anthropic_client import ask_claude

    # 提取文字行
    entries = _extract_srt_texts(srt_content)
    if not entries:
        logger.warning("SRT 無文字行，跳過 LLM 校正")
        return srt_content

    # 組裝編號文字
    numbered_text = "\n".join(f"[{seq}] {text}" for seq, text in entries)

    # 載入 context
    context_text = _load_context_text(context_files)

    system = (
        "你是字幕校正專家。你的任務是校正語音辨識（ASR）產生的繁體中文逐字稿。\n\n"
        "## 校正規則\n"
        "1. 修正 ASR 常見錯誤：同音字、斷句錯誤、漏字\n"
        "2. 專有名詞必須全文統一（人名、書名、術語、品牌名）\n"
        "3. 保持口語自然感，不要改成書面語\n"
        "4. 不要增刪內容，只修正錯誤\n"
        "5. 每行的序號 [N] 必須保留，不可合併或拆分行\n\n"
        "## 輸出格式\n"
        "逐行輸出，格式與輸入相同：\n"
        "[序號] 校正後的文字\n\n"
        "只輸出校正後的內容，不要加任何說明。\n"
        "如果某行不需要修改，原樣輸出即可。"
    )

    if context_text:
        system += (
            "\n\n## 參考資料\n"
            "以下是這次訪談相關的背景資料，用於判斷正確的專有名詞拼寫：\n\n" + context_text
        )

    prompt = f"請校正以下語音辨識逐字稿：\n\n{numbered_text}"

    logger.info(f"LLM 校正：{len(entries)} 行，模型 {model}")
    raw = ask_claude(prompt, system=system, model=model, max_tokens=8192, temperature=0.1)

    # 解析回傳
    corrected: dict[int, str] = {}
    for line in raw.strip().splitlines():
        match = re.match(r"\[(\d+)\]\s*(.*)", line)
        if match:
            seq = int(match.group(1))
            text = match.group(2).strip()
            if text:
                corrected[seq] = text

    if not corrected:
        logger.warning("LLM 校正回傳無法解析，使用原始文字")
        return srt_content

    logger.info(f"LLM 校正完成：{len(corrected)}/{len(entries)} 行已處理")

    return _replace_srt_texts(srt_content, corrected)


def _get_ts_values(ts_item) -> tuple[int, int]:
    """從時間戳項目中取得 (start_ms, end_ms)。

    FunASR 時間戳格式可能是：
    - List/tuple: [start_ms, end_ms]
    - Dict: {"start_time": ms, "end_time": ms}
    """
    if isinstance(ts_item, dict):
        return ts_item["start_time"], ts_item["end_time"]
    return ts_item[0], ts_item[1]


def _funasr_to_srt(results: list[dict]) -> str:
    """將 FunASR 辨識結果轉為 SRT 格式字串。

    FunASR 輸出格式：[{"text": "...", "timestamp": [[start_ms, end_ms], ...]}]

    時間戳類型：
    - sentence_info: per-sentence（最佳，直接用）
    - 字級時間戳: len(timestamps) ≈ len(text)，用句尾標點拆分並對齊
    - 句級時間戳: len(timestamps) == len(sentences)，直接配對
    """
    srt_parts: list[str] = []
    seq = 1

    for item in results:
        text = item.get("text", "").strip()
        if not text:
            continue

        # 優先使用 sentence_info（含逐句文字 + 時間戳）
        sentence_info = item.get("sentence_info")
        if sentence_info:
            for info in sentence_info:
                s_text = info.get("text", "").strip()
                if not s_text:
                    continue
                start_s = info["start"] / 1000
                end_s = info["end"] / 1000
                start_ts = _seconds_to_srt_ts(start_s)
                end_ts = _seconds_to_srt_ts(end_s)
                srt_parts.append(f"{seq}\n{start_ts} --> {end_ts}\n{s_text}\n")
                seq += 1
            continue

        timestamps = item.get("timestamp", [])
        if not timestamps:
            srt_parts.append(f"{seq}\n00:00:00,000 --> 00:00:00,000\n{text}\n")
            seq += 1
            continue

        sentences = _split_sentences(text)

        # 判斷時間戳類型：字級（≈字數）還是句級（≈句數）
        is_char_level = len(timestamps) > len(sentences) * 2

        if is_char_level:
            # 字級時間戳：用字元位置對齊句子的起止時間
            char_idx = 0
            for sentence in sentences:
                # 句子在原文中的起始字元位置
                start_char = char_idx
                end_char = char_idx + len(sentence) - 1

                # 對齊到 timestamp 陣列（可能有標點不佔 timestamp）
                ts_start = min(start_char, len(timestamps) - 1)
                ts_end = min(end_char, len(timestamps) - 1)

                start_ms, _ = _get_ts_values(timestamps[ts_start])
                _, end_ms = _get_ts_values(timestamps[ts_end])

                start_ts = _seconds_to_srt_ts(start_ms / 1000)
                end_ts = _seconds_to_srt_ts(end_ms / 1000)
                srt_parts.append(f"{seq}\n{start_ts} --> {end_ts}\n{sentence}\n")
                seq += 1

                char_idx += len(sentence)
        elif len(sentences) == len(timestamps):
            # 句級時間戳：完美對齊
            for sentence, ts in zip(sentences, timestamps):
                start_ms, end_ms = _get_ts_values(ts)
                start_ts = _seconds_to_srt_ts(start_ms / 1000)
                end_ts = _seconds_to_srt_ts(end_ms / 1000)
                srt_parts.append(f"{seq}\n{start_ts} --> {end_ts}\n{sentence}\n")
                seq += 1
        else:
            # Fallback：用首尾 timestamp 包整段
            start_ms, _ = _get_ts_values(timestamps[0])
            _, end_ms = _get_ts_values(timestamps[-1])
            start_ts = _seconds_to_srt_ts(start_ms / 1000)
            end_ts = _seconds_to_srt_ts(end_ms / 1000)
            srt_parts.append(f"{seq}\n{start_ts} --> {end_ts}\n{text}\n")
            seq += 1

    return "\n".join(srt_parts)


def _split_sentences(text: str) -> list[str]:
    """拆分文字為字幕段落，每段不超過 _MAX_SUBTITLE_CHARS 字。

    策略：
    1. 先用句尾標點（。！？）拆分
    2. 超過上限的再用逗號（，、；：）拆分
    3. 仍超過的強制在上限處斷行
    """
    # Step 1: 用句尾標點拆分
    raw_sentences = _split_by_pattern(_SENTENCE_END, text)

    # Step 2: 長句用逗號再拆
    result: list[str] = []
    for s in raw_sentences:
        if len(s) <= _MAX_SUBTITLE_CHARS:
            result.append(s)
        else:
            clauses = _split_by_pattern(_CLAUSE_BREAK, s)
            for clause in clauses:
                if len(clause) <= _MAX_SUBTITLE_CHARS:
                    result.append(clause)
                else:
                    # Step 3: 強制斷行
                    for i in range(0, len(clause), _MAX_SUBTITLE_CHARS):
                        chunk = clause[i : i + _MAX_SUBTITLE_CHARS]
                        if chunk.strip():
                            result.append(chunk)
    return result


def _split_by_pattern(pattern: re.Pattern, text: str) -> list[str]:
    """用正規表達式 pattern 拆分文字，將分隔符黏回前一段。"""
    parts = pattern.split(text)
    segments: list[str] = []
    i = 0
    while i < len(parts):
        segment = parts[i]
        if i + 1 < len(parts) and pattern.match(parts[i + 1]):
            segment += parts[i + 1]
            i += 2
        else:
            i += 1
        segment = segment.strip()
        if segment:
            segments.append(segment)
    return segments


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


def _get_asr_model(model_id: str):
    """取得 FunASR 模型（lazy singleton，避免重複載入）。"""
    global _asr_model, _asr_model_id

    if _asr_model is not None and _asr_model_id == model_id:
        return _asr_model

    from funasr import AutoModel

    logger.info(f"載入 FunASR 模型: {model_id}")
    _asr_model = AutoModel(
        model=model_id,
        vad_model="fsmn-vad",
        punc_model="ct-punc-c",
    )
    _asr_model_id = model_id
    logger.info("FunASR 模型載入完成")
    return _asr_model


def transcribe(
    audio_path: str | Path,
    *,
    language: str = "zh",
    context_files: list[str | Path] | None = None,
    use_punctuation: bool = False,
    use_llm_correction: bool = False,
    llm_model: str = "claude-haiku-4-5-20251001",
    asr_model: str = "paraformer-zh",
    normalize_audio: bool = True,
    output_dir: str | Path | None = None,
) -> Path:
    """語音轉繁體中文 SRT 字幕。

    Args:
        audio_path: 音檔路徑（MP3, WAV, M4A 等）
        language: 語言代碼（預設 "zh"）
        context_files: 參考資料檔案路徑列表（書、報導等），用於提升專有名詞準確度
        use_punctuation: 是否保留標點符號（預設 False）
        use_llm_correction: 是否啟用 LLM 校正（預設 False，啟用會產生 API 成本）
        llm_model: LLM 校正使用的模型（預設 Haiku，~$0.09/小時音檔）
        asr_model: FunASR 模型 ID（預設 paraformer-zh）
        normalize_audio: 是否先用 Auphonic 做 normalization（預設 True）
        output_dir: 輸出目錄（預設與音檔同目錄）

    Returns:
        SRT 字幕檔的 Path
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    context_files = context_files or []
    output_dir = Path(output_dir) if output_dir else audio_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"開始轉寫：{audio_path.name}")

    # ── Auphonic 前處理（可選）──
    if normalize_audio:
        try:
            from shared.auphonic import normalize

            audio_path = normalize(audio_path, output_dir=output_dir)
            logger.info(f"Auphonic normalization 完成：{audio_path.name}")
        except Exception as e:
            logger.warning(f"跳過 Auphonic normalization: {e}")

    # ── FunASR 辨識 ──
    model = _get_asr_model(asr_model)

    hotwords = _extract_hotwords(context_files)
    hotword_str = " ".join(hotwords) if hotwords else ""
    if hotwords:
        logger.info(f"Hotwords: {hotwords[:10]}{'...' if len(hotwords) > 10 else ''}")

    logger.info(f"開始 ASR 辨識（模型: {asr_model}）")
    results = model.generate(
        input=str(audio_path),
        batch_size_s=300,
        hotword=hotword_str,
    )

    if not results or not results[0].get("text"):
        raise RuntimeError("FunASR 辨識結果為空")

    logger.info(f"ASR 辨識完成，文字長度: {len(results[0]['text'])}")

    # ── 轉為 SRT ──
    srt_content = _funasr_to_srt(results)

    # ── 後處理：逐行簡轉繁 + 標點控制 ──
    processed_lines = [
        _process_srt_line(line, use_punctuation=use_punctuation)
        for line in srt_content.splitlines()
    ]
    srt_content = "\n".join(processed_lines)

    # ── LLM 校正（可選）──
    if use_llm_correction:
        srt_content = _correct_with_llm(
            srt_content,
            context_files=context_files,
            model=llm_model,
        )

    # ── 寫入最終 SRT ──
    final_srt = output_dir / f"{audio_path.stem}.srt"
    final_srt.write_text(srt_content, encoding="utf-8")

    logger.info(f"完成！SRT 已儲存：{final_srt}")
    return final_srt
