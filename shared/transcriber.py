"""語音轉繁體中文字幕：基於 FunASR Paraformer-zh 的本地 ASR pipeline。

獨立模組，可被任何 agent 調用。
支援 Auphonic 雲端前處理（normalization + 降噪）、
FunASR 本地辨識（VAD + 時間戳 + 標點 + Hotword），
以及可選的 LLM 校正（Pinyin 輔助 + JSON diff + QC 報告）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.transcriber")

# 中文句中標點 → 替換為空格
_ZH_MID_PUNCTUATION = re.compile(r"[，、；：" "''（）《》【】…—～·]")
# 中文句尾標點 → 直接移除
_ZH_END_PUNCTUATION = re.compile(r"[。！？]")

# FunASR 字級 timestamp 不覆蓋的字元（標點、空白、全形括號等）
# 用於 `_funasr_to_srt` 將 text char 位置映射到 timestamp 索引。
_FUNASR_NO_TS_CHAR = re.compile(r"[，、。！？；：,.!?;:\s\"'“”‘’（）()《》【】…—~～·]")

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


def _add_pinyin(text: str) -> str:
    """為中文文字加上拼音標注，輔助 LLM 辨識同音字。

    範例：'蘇味行銷' → '蘇味行銷 (sū wèi xíng xiāo)'
    純英文或純數字不加 pinyin。
    """
    if not re.search(r"[\u4e00-\u9fff]", text):
        return text
    from pypinyin import Style, pinyin

    py = " ".join(p[0] for p in pinyin(text, style=Style.TONE))
    return f"{text} ({py})"


def _extract_project_context(project_file: str | Path) -> dict:
    """從 LifeOS Podcast Project 檔案提取校正 context。

    解析 YAML frontmatter 取得 guest、category，
    並提取 Research Dropbox、Script、Keywords Research 等 section 的純文字。
    跳過 DataviewJS / Templater code blocks。

    Returns:
        dict: guest_name, topic, context_text
    """
    p = Path(project_file)
    if not p.exists():
        logger.warning(f"Project 檔案不存在：{p}")
        return {"guest_name": "", "topic": "", "context_text": ""}

    raw = p.read_text(encoding="utf-8")
    result: dict = {
        "guest_name": "",
        "topic": p.stem,  # 檔名即主題
        "context_text": "",
    }

    # ── 解析 frontmatter ──
    fm_match = re.match(r"^---\s*\n(.*?)\n---", raw, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        for line in fm_text.splitlines():
            if line.startswith("guest:"):
                result["guest_name"] = line.split(":", 1)[1].strip()
            elif line.startswith("search_topic:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    result["topic"] = val

    # ── 提取有用 section（跳過 code blocks）──
    useful_sections = {
        "Research Dropbox",
        "Script",
        "Keywords Research",
        "Description",
        "Description / Show Notes",
        "專案筆記",
    }

    sections_text: list[str] = []
    in_code_block = False
    capturing = False

    for line in raw.splitlines():
        # code block 狀態機
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        # 跳過 Templater 區塊
        if line.strip().startswith("<%"):
            continue

        # 偵測 section header
        header_match = re.match(r"^##\s+(?:[^\w]*\s*)?(.+)", line)
        if header_match:
            section_name = header_match.group(1).strip()
            # 移除 emoji prefix
            section_name = re.sub(r"^[\U0001f300-\U0001f9ff\s]+", "", section_name).strip()
            capturing = any(s in section_name for s in useful_sections)
            continue

        if capturing and line.strip():
            sections_text.append(line.strip())

    result["context_text"] = "\n".join(sections_text)
    return result


def _write_qc_report(path: Path, items: list[dict]) -> None:
    """將 QC 項目寫成 Markdown 報告。

    支援兩種格式（自動偵測）：
    - 新版（含 `verdict`）：多模態仲裁結果
    - 舊版（僅 original/suggestion/reason）：單輪 Opus uncertainties
    """
    lines = ["# QC 報告 — 需人工確認\n"]
    for item in items:
        risk = item.get("risk", "medium")
        line_no = item.get("line", "?")
        has_verdict = "verdict" in item

        if has_verdict:
            verdict = item.get("verdict", "?")
            confidence = item.get("confidence", 0.0)
            header = f"## [{risk.upper()} | {verdict} | conf {confidence:.2f}] Line {line_no}"
            lines.append(header)
            lines.append(f"- **ASR 原文**：{item.get('original', '')}")
            lines.append(f"- **Opus 建議**：{item.get('suggestion', '')}")
            lines.append(f"- **Opus 理由**：{item.get('reason', '')}")
            lines.append(f"- **Gemini 仲裁**：{item.get('final_text', '')}")
            lines.append(f"- **Gemini 理由**：{item.get('gemini_reasoning', '')}")
        else:
            lines.append(f"## [{risk.upper()}] Line {line_no}")
            lines.append(f"- **原文**：{item.get('original', '')}")
            lines.append(f"- **建議**：{item.get('suggestion', '')}")
            lines.append(f"- **理由**：{item.get('reason', '')}")

        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


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


_QC_CONFIDENCE_THRESHOLD = 0.6


def _apply_arbitration_verdicts(
    corrections: dict[int, str],
    uncertainties: list[dict],
    verdicts: list,  # list[ArbitrationVerdict]
) -> tuple[dict[int, str], list[dict]]:
    """依 Gemini 仲裁結果更新 corrections dict，並產出 QC 清單。

    - keep_original  → 從 corrections 移除（還原 ASR 原文）
    - accept_suggestion → 寫入 corrections[line] = opus_suggestion
      （因為 Pass 1 prompt 叫 Opus「不要硬改」uncertain 項目，典型情況下
      suggestion 只在 uncertainties dict，不在 corrections — 必須顯式寫入）
    - other → corrections 覆寫為 final_text
    - uncertain → 從 corrections 移除 + 進 QC
    - refused → 同 uncertain（從 corrections 移除 + 進 QC）；
      拒答由 arbiter 偵測 reasoning 含「無關/無法判斷」等 meta 字樣後覆蓋產生
    - 任何 confidence < _QC_CONFIDENCE_THRESHOLD → 進 QC（即便已套用）
    """
    uncertain_by_line = {u.get("line"): u for u in uncertainties}
    qc_items: list[dict] = []

    for v in verdicts:
        line = v.line
        original_dict = uncertain_by_line.get(line, {})
        asr_original = original_dict.get("original", "")
        opus_suggestion = original_dict.get("suggestion", "")
        opus_reason = original_dict.get("reason", "")
        risk = original_dict.get("risk", "medium")

        if v.verdict == "keep_original":
            corrections.pop(line, None)
            final_text = asr_original
        elif v.verdict == "accept_suggestion":
            if opus_suggestion:
                corrections[line] = opus_suggestion
                final_text = opus_suggestion
            else:
                corrections.pop(line, None)
                final_text = asr_original
        elif v.verdict == "other":
            corrections[line] = v.final_text
            final_text = v.final_text
        else:  # uncertain 或 refused（拒答） — 保守採 ASR 原文並進 QC
            corrections.pop(line, None)
            final_text = asr_original

        if v.verdict in ("uncertain", "refused") or v.confidence < _QC_CONFIDENCE_THRESHOLD:
            qc_items.append(
                {
                    "line": line,
                    "original": asr_original,
                    "suggestion": opus_suggestion,
                    "reason": opus_reason,
                    "risk": risk,
                    "verdict": v.verdict,
                    "final_text": final_text,
                    "gemini_reasoning": v.reasoning,
                    "confidence": v.confidence,
                }
            )

    return corrections, qc_items


def _correct_with_llm(
    srt_content: str,
    *,
    context_files: list[str | Path],
    project_context: dict | None = None,
    model: str = "claude-opus-4-7",
    host_name: str = "",
    show_name: str = "",
    audio_path: Path | None = None,
    use_arbitration: bool = True,
    run_id: int | None = None,
) -> tuple[str, list[dict]]:
    """用 Claude 校正 ASR 逐字稿（Pinyin 輔助 + JSON diff + 三輪校對 + 多模態仲裁）。

    流程：
    1. Pass 1 Opus 校正：帶 pinyin 的編號逐字稿 → corrections + uncertainties
    2. 若有 `audio_path` 且 `use_arbitration=True` 且有 uncertainties：
       → Gemini 2.5 Pro audio 對 uncertain 片段仲裁
       → 依 verdict 更新 corrections、低信心 verdict 進 QC
    3. 否則走舊流程：uncertainties 直接進 QC

    Returns:
        tuple: (corrected_srt, qc_items)
            - 有仲裁時 qc_items 含 verdict/final_text/gemini_reasoning/confidence
            - 無仲裁時 qc_items 為原始 uncertainties（保持向下相容）
    """
    from shared.anthropic_client import ask_claude

    entries = _extract_srt_texts(srt_content)
    if not entries:
        logger.warning("SRT 無文字行，跳過 LLM 校正")
        return srt_content, []

    # ── 帶 pinyin 的編號文字 ──
    numbered_lines = []
    for seq, text in entries:
        numbered_lines.append(f"[{seq}] {_add_pinyin(text)}")
    numbered_text = "\n".join(numbered_lines)

    # ── 組裝 context ──
    context_parts: list[str] = []

    # Project context（自動從 LifeOS Project 提取）
    if project_context:
        guest = project_context.get("guest_name", "")
        topic = project_context.get("topic", "")
        proj_text = project_context.get("context_text", "")
        if guest:
            context_parts.append(f"來賓姓名：{guest}")
        if topic:
            context_parts.append(f"主題：{topic}")
        if proj_text:
            context_parts.append(f"背景資料：\n{proj_text[:3000]}")

    # 手動 context files
    manual_context = _load_context_text(context_files)
    if manual_context:
        context_parts.append(manual_context)

    # ── System Prompt ──
    system = "你是資深繁體中文（台灣）字幕校正專家，專精 Podcast 訪談字幕。\n"

    if host_name or show_name:
        system += "\n## 節目資訊\n"
        if show_name:
            system += f"- 節目名稱：{show_name}\n"
        if host_name:
            system += f"- 主持人：{host_name}\n"

    system += (
        "\n## 任務\n"
        "校正語音辨識（ASR）產出的逐字稿。每行格式為 [序號] 文字 (拼音)。\n"
        "拼音是原始文字的讀音，可幫助你判斷 ASR 的同音字錯誤。\n"
        "\n## 三輪校對思路（在心中依序執行，最終只輸出結果）\n"
        "1. 機械校正：同音字/近音字替換、繁體用字統一、術語表比對\n"
        "2. 語意校正：上下文不通順、人名/稱謂前後不一致、英文專有名詞修正\n"
        "3. 交付檢核：專有名詞全文一致性、確認沒有過度修改\n"
        "\n## 核心原則\n"
        "- 不改變原意、不新增內容\n"
        "- 術語表/參考資料的寫法為最高優先\n"
        "- 保持口語自然感，不改成書面語\n"
        "- 不確定的修正必須放入 uncertain 清單，不要硬改\n"
        "- **輸出文字不要包含任何標點符號（，。、；：？！等）**；語氣停頓用半形空格分隔即可\n"
        "\n## 輸出格式（嚴格 JSON，不要加 ```json 標記）\n"
        "{\n"
        '  "corrections": {"序號": "校正後文字", ...},\n'
        '  "uncertain": [\n'
        '    {"line": 序號, "original": "原文", "suggestion": "建議", '
        '"reason": "判斷理由", "risk": "high|medium|low"}\n'
        "  ]\n"
        "}\n\n"
        "只輸出 JSON，不要加任何說明。corrections 只包含有修改的行。"
    )

    if context_parts:
        system += "\n\n## 參考資料\n" + "\n\n".join(context_parts)

    prompt = f"請校正以下語音辨識逐字稿：\n\n{numbered_text}"

    logger.info(f"LLM 校正：{len(entries)} 行，模型 {model}")
    raw = ask_claude(prompt, system=system, model=model, max_tokens=16384)

    # ── 解析 JSON 回傳 ──
    corrected, uncertainties = _parse_llm_response(raw, len(entries))

    if corrected:
        logger.info(f"LLM Pass 1 完成：{len(corrected)} 行修正，{len(uncertainties)} 項 uncertain")
    else:
        logger.warning("LLM Pass 1 無修改")

    # ── Pass 2：多模態仲裁（可選）──
    qc_items: list[dict] = uncertainties
    should_arbitrate = use_arbitration and uncertainties and audio_path is not None

    if should_arbitrate:
        try:
            from shared.multimodal_arbiter import arbitrate_uncertain

            pre_arb_srt = _replace_srt_texts(srt_content, corrected)
            verdicts = arbitrate_uncertain(
                audio_path,
                pre_arb_srt,
                uncertainties,
                run_id=run_id,
            )
            corrected, qc_items = _apply_arbitration_verdicts(corrected, uncertainties, verdicts)
            logger.info(f"多模態仲裁完成：{len(verdicts)} 個 verdict，{len(qc_items)} 項進 QC")
        except Exception as e:
            logger.warning(f"多模態仲裁失敗，退回舊流程：{type(e).__name__}: {e}")
            qc_items = uncertainties
    elif use_arbitration and uncertainties and audio_path is None:
        logger.info("無 audio_path，跳過多模態仲裁")

    return _replace_srt_texts(srt_content, corrected), qc_items


def _parse_llm_response(raw: str, total_entries: int) -> tuple[dict[int, str], list[dict]]:
    """解析 LLM 校正回傳，支援 JSON 格式 + regex fallback。"""
    # 嘗試 JSON 解析
    try:
        # 移除可能的 markdown code fence
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        data = json.loads(cleaned)
        corrections = {int(k): v for k, v in data.get("corrections", {}).items()}
        uncertainties = data.get("uncertain", [])
        return corrections, uncertainties
    except (json.JSONDecodeError, ValueError, AttributeError):
        logger.warning("JSON 解析失敗，嘗試 regex fallback")

    # Fallback: 原本的 [N] text 格式
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

    return corrected, []


def _get_ts_values(ts_item) -> tuple[int, int]:
    """從時間戳項目中取得 (start_ms, end_ms)。

    FunASR 時間戳格式可能是：
    - List/tuple: [start_ms, end_ms]
    - Dict: {"start_time": ms, "end_time": ms}
    """
    if isinstance(ts_item, dict):
        return ts_item["start_time"], ts_item["end_time"]
    return ts_item[0], ts_item[1]


def _funasr_char_to_ts_idx(text: str, n_ts: int) -> list[int]:
    """建立 `text` 每個字元位置 → FunASR timestamp 索引的映射。

    FunASR 的 timestamp 陣列只覆蓋**可發音字**（跳過標點/空白/全形括號），
    所以不能直接用 `char_idx` 當 timestamp 索引（長音檔會線性漂移 ~10%）。

    策略：
    1. 數出 text 裡所有可發音字元的位置（content chars）
    2. 用線性比例把第 k 個 content char 映射到 timestamp[k * n_ts / n_content]
       — 處理 FunASR 對英文單字的 tokenization 可能跟單字元計數不一致
    3. 標點/空白位置繼承前一個可發音字的索引
    """
    n = len(text)
    if n == 0 or n_ts == 0:
        return [0] * n

    content_positions = [i for i, ch in enumerate(text) if not _FUNASR_NO_TS_CHAR.match(ch)]
    n_content = len(content_positions)
    if n_content == 0:
        return [0] * n

    scale = n_ts / n_content
    result = [0] * n
    c_idx = 0
    last_ts = 0
    content_set = set(content_positions)
    # 建立 content_position → ts_idx 的 lookup
    content_to_ts: dict[int, int] = {}
    for k, pos in enumerate(content_positions):
        content_to_ts[pos] = min(n_ts - 1, int(k * scale))

    for i in range(n):
        if i in content_set:
            last_ts = content_to_ts[i]
        result[i] = last_ts
        c_idx += 1  # not strictly needed but keeps signature symmetric

    return result


def _funasr_to_srt(results: list[dict]) -> str:
    """將 FunASR 辨識結果轉為 SRT 格式字串。

    FunASR 輸出格式：[{"text": "...", "timestamp": [[start_ms, end_ms], ...]}]

    時間戳類型：
    - sentence_info: per-sentence（最佳，直接用）
    - 字級時間戳: len(timestamps) 對應可發音字（跳過標點），用 `_funasr_char_to_ts_idx` 對齊
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
            # 字級時間戳：FunASR 只給可發音字的 timestamp，必須把
            # text char 位置正確映射到 timestamp 索引（不能直接用 char_idx）。
            char_to_ts_idx = _funasr_char_to_ts_idx(text, len(timestamps))

            search_from = 0
            for sentence in sentences:
                if not sentence:
                    continue
                # 找句子在 text 裡的位置（_split_sentences 可能做過 strip()）
                pos = text.find(sentence, search_from)
                if pos < 0:
                    stripped = sentence.strip()
                    pos = text.find(stripped, search_from) if stripped else -1
                    if pos >= 0:
                        sentence_len = len(stripped)
                    else:
                        # 找不到就 fallback：用當前 search_from 估位置
                        pos = search_from
                        sentence_len = len(sentence)
                else:
                    sentence_len = len(sentence)

                start_char = pos
                end_char = min(pos + sentence_len - 1, len(text) - 1)
                search_from = pos + sentence_len

                ts_start = char_to_ts_idx[start_char]
                ts_end = char_to_ts_idx[end_char]

                start_ms, _ = _get_ts_values(timestamps[ts_start])
                _, end_ms = _get_ts_values(timestamps[ts_end])

                start_ts = _seconds_to_srt_ts(start_ms / 1000)
                end_ts = _seconds_to_srt_ts(end_ms / 1000)
                srt_parts.append(f"{seq}\n{start_ts} --> {end_ts}\n{sentence}\n")
                seq += 1
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
                    # Step 3: 強制斷行（不切斷英文單字）
                    result.extend(_force_break(clause, _MAX_SUBTITLE_CHARS))
    return result


def _force_break(text: str, max_chars: int) -> list[str]:
    """強制斷行，但不切斷英文單字。

    遇到英文字詞時，往前找最近的中文字元或空格斷行。
    如果整段都是英文且超過上限，則在空格處斷。
    """
    chunks: list[str] = []
    while len(text) > max_chars:
        cut = max_chars
        # 如果切點落在英文字母中間，往前找安全斷點
        if cut < len(text) and re.match(r"[a-zA-Z]", text[cut]):
            # 往前找非英文字母的位置
            safe = cut
            while safe > 0 and re.match(r"[a-zA-Z]", text[safe - 1]):
                safe -= 1
            if safe > 0:
                cut = safe
            else:
                # 整段開頭都是英文，往後找單字結尾
                cut = max_chars
                while cut < len(text) and re.match(r"[a-zA-Z]", text[cut]):
                    cut += 1
        chunk = text[:cut].strip()
        if chunk:
            chunks.append(chunk)
        text = text[cut:].strip()
    if text.strip():
        chunks.append(text.strip())
    return chunks


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


def _process_srt_line(line: str) -> str:
    """處理單行 SRT 內容：簡轉繁 + 去標點（句中→空格、句尾→刪除）。只作用於字幕文字行。"""
    stripped = line.strip()
    # 跳過空行、序號行、時間戳行
    if not stripped or stripped.isdigit() or "-->" in stripped:
        return line

    line = _to_traditional(line)
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
    project_file: str | Path | None = None,
    use_llm_correction: bool = False,
    llm_model: str = "claude-opus-4-7",
    asr_model: str = "paraformer-zh",
    normalize_audio: bool = True,
    output_dir: str | Path | None = None,
    host_name: str = "張修修",
    show_name: str = "不正常人類研究所",
    use_multimodal_arbitration: bool = True,
    run_id: int | None = None,
) -> Path:
    """語音轉繁體中文 SRT 字幕。

    Args:
        audio_path: 音檔路徑（MP3, WAV, M4A 等）
        language: 語言代碼（預設 "zh"）
        context_files: 參考資料檔案路徑列表（書、報導等），用於提升專有名詞準確度
        project_file: LifeOS Podcast Project 檔案路徑，自動提取來賓/主題/術語 context
        use_llm_correction: 是否啟用 LLM 校正（預設 False，啟用會產生 API 成本）
        llm_model: LLM 校正使用的模型（預設 Opus，~$0.40/小時音檔）
        asr_model: FunASR 模型 ID（預設 paraformer-zh）
        normalize_audio: 是否先用 Auphonic 做 normalization（預設 True）
        output_dir: 輸出目錄（預設與音檔同目錄）
        host_name: 主持人名稱（預設「張修修」）
        show_name: 節目名稱（預設「不正常人類研究所」）
        use_multimodal_arbitration: 是否在 LLM 校正後用 Gemini 2.5 Pro audio 仲裁 uncertain 片段
            （僅在 use_llm_correction=True 時生效，多 $0.05–0.20/hr）
        run_id: cost tracking 用的 run id（傳給 Gemini 客戶端）

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

    # ── 後處理：逐行簡轉繁 + 去標點（Pass 1：FunASR 輸出）──
    srt_content = "\n".join(_process_srt_line(line) for line in srt_content.splitlines())

    # ── LLM 校正（可選）──
    if use_llm_correction:
        proj_ctx = _extract_project_context(project_file) if project_file else None
        srt_content, qc_items = _correct_with_llm(
            srt_content,
            context_files=context_files,
            project_context=proj_ctx,
            model=llm_model,
            host_name=host_name,
            show_name=show_name,
            audio_path=audio_path,
            use_arbitration=use_multimodal_arbitration,
            run_id=run_id,
        )
        if qc_items:
            qc_path = output_dir / f"{audio_path.stem}.qc.md"
            _write_qc_report(qc_path, qc_items)
            logger.info(f"QC 報告：{len(qc_items)} 項待確認 → {qc_path}")

        # ── Pass 2：LLM/Gemini 可能在校正時加回標點，最終輸出前再過濾一次 ──
        srt_content = "\n".join(_process_srt_line(line) for line in srt_content.splitlines())

    # ── 寫入最終 SRT ──
    final_srt = output_dir / f"{audio_path.stem}.srt"
    final_srt.write_text(srt_content, encoding="utf-8")

    logger.info(f"完成！SRT 已儲存：{final_srt}")
    return final_srt
