"""字幕產線共用的 ASR / 文字處理 helper（WhisperX model singleton、簡轉繁、
去標點、jieba 繁體詞庫、Whisper initial_prompt、LLM 校正 prompt 與回傳解析）。

呼叫端：subtitle-gen / subtitle-correct / srt_align / gap-fill / relisten 等
script 與 shared 模組。原本的端到端 `transcribe()`（legacy /transcribe：
Auphonic + WhisperX + Opus 校正 + Gemini 仲裁）已退役（#1299）。

引擎選型 rationale：docs/decisions/ADR-013-transcribe-engine-reconsideration.md
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.transcriber")

# 句中標點（中英）→ 替換為空格
# 注意：英文 `,` 在中英 code-switch 文字裡通常是子句斷點而非英文文法逗號，
# 一律當斷點處理；如果未來需要保留英文文法 `,`（如「Paul, my friend」）可加 detection。
# 《》（書名號）與「」（專有名詞）依修修 2026-07-25 裁決保留，不在清除範圍
_ZH_MID_PUNCTUATION = re.compile(r"[，、；：" "''（）【】…—～·,;:]")
# 句尾標點（中英）→ 直接移除
_ZH_END_PUNCTUATION = re.compile(r"[。！？!?]|(?<=\S)\.(?=\s|$)")

# 字幕每行最大字數
# soft：常態目標 / hard：容許 overflow（保 ASCII 英文 compound name 不被切，
# 例 Traveling Village = 17 字）
# 慣例常數：cue_builder / script_align 各自複製一份並註明對齊此值
_MAX_SUBTITLE_CHARS = 14
_MAX_SUBTITLE_HARD = 22

# OpenCC lazy singleton（避免重複載入字典）
_cc_s2t = None

# WhisperX ASR model lazy singleton
_asr_model = None
_asr_model_id = None

# WhisperX align model lazy singleton（word-level timestamp）
_align_model = None
_align_metadata = None
_align_language = None


def _get_cc():
    """取得 OpenCC 簡轉繁 converter（lazy singleton，s2twp = 簡 → 台灣繁體含詞彙）。"""
    global _cc_s2t
    if _cc_s2t is None:
        from opencc import OpenCC

        _cc_s2t = OpenCC("s2twp")
    return _cc_s2t


def _remove_punctuation(text: str) -> str:
    """中文句中標點→空格，句尾標點→移除，再壓縮連續空格。"""
    text = _ZH_MID_PUNCTUATION.sub(" ", text)
    text = _ZH_END_PUNCTUATION.sub("", text)
    return re.sub(r" {2,}", " ", text).strip()


# OpenCC s2twp 過度轉換修正（修修 2026-07-26 抓到「就是隻要」）：
# s2twp 的詞庫把「是只要」誤匹配成「是隻要」等；轉換後把副詞用法的
# 隻X 修回 只X——前面是量詞語境（一隻/這隻/那隻…）時不動
_ZHI_FIX = re.compile(r"(?<![一兩三幾這那每兩隻])隻(?=(要|是|能|會|有|好|不過|剩))")

# 同類第二例（2026-07-29 鄭國威那集抓到，全集 32 處）：s2twp 把「腳本」無條件
# 映射成資訊術語「指令碼」——連輸入本來就是繁體的「腳本」也照轉。本產線處理的是
# **影音訪談**的 ASR 文字，「腳本」一律是分鏡前的影片腳本（大綱→腳本→分鏡→拍攝），
# 不是 shell script；且「指令碼」是書面術語，沒有人這樣講話，ASR 不會原生產出它。
_SCRIPT_FIX = re.compile(r"指令碼")


def _to_traditional(text: str) -> str:
    """簡體中文轉繁體中文（OpenCC lazy singleton + 過度轉換修正）。"""
    converted = _get_cc().convert(text)
    return _SCRIPT_FIX.sub("腳本", _ZHI_FIX.sub("只", converted))


_TW_JIEBA_READY = False


def ensure_tw_jieba() -> None:
    """給 jieba 掛繁體補充詞庫（所有對繁體文本 jieba.cut 的呼叫點都要先呼叫）。

    jieba 內建詞庫是**簡體**——繁體詞（綁架/覺察…）不在庫裡會被逐字切，
    下游任何「詞邊界」邏輯（斷行/斷句/細切）就會把詞攔腰砍（2026-07-26
    「來綁/架我們大腦」案例）。修法：把內建 dict.txt 用 OpenCC s2twp 整批
    轉繁、cache 到 data/jieba_tw_dict.txt（首次 ~10s，之後秒載），
    load_userdict 疊加（簡繁同庫）。"""
    global _TW_JIEBA_READY
    if _TW_JIEBA_READY:
        return
    import jieba

    data_dir = os.environ.get("NAKAMA_DATA_DIR") or (
        Path(__file__).resolve().parent.parent / "data"
    )
    cache = Path(data_dir) / "jieba_tw_dict.txt"
    if not cache.exists():
        logger.info("首次生成 jieba 繁體詞庫（OpenCC 整批轉換，~10s）→ %s", cache)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with jieba.dt.get_dict_file() as f:
            lines = f.read().decode("utf-8").splitlines()
        rows = [ln.split(" ") for ln in lines if ln.strip()]
        tw_words = _to_traditional("\n".join(r[0] for r in rows)).splitlines()
        out = [
            " ".join([tw] + r[1:])
            for tw, r in zip(tw_words, rows)
            if tw != r[0]  # 只收簡繁不同的（相同的內建庫已有）
        ]
        cache.write_text("\n".join(out), encoding="utf-8")
    jieba.load_userdict(str(cache))
    _TW_JIEBA_READY = True


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
        # 去 markdown 標記：hotwords 會進 Whisper initial_prompt，殘留的
        # ** / _ / ` 會在靜音段被 echo 進字幕（2026-07-25 「升級**吧**」幻覺實例）
        text = re.sub(r"[*_`#>]", "", text)

        # 提取書名號內的詞
        hotwords.extend(re.findall(r"《(.+?)》", text))
        # 提取雙引號內的短詞（< 20 字元）
        for match in re.findall(r"「(.+?)」", text):
            if len(match) < 20:
                hotwords.append(match)

    return list(set(hotwords))[:50]  # 去重，最多 50 個


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


def _build_correction_system(host_name: str, show_name: str, context_parts: list[str]) -> str:
    """組校正用 system prompt（subtitle_correct 的 cowork / API 兩條路徑共用）。"""
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
        "- **書名／作品名必須用《》標出**（例：《升級吧 大腦》；書名內部標點仍省略）；"
        "**專有名詞／術語用「」標出**（例：「腦腐」「多巴胺」）——這兩種括號幫助讀者閱讀，"
        "凡辨識得出就要主動補上\n"
        "- 除上述《》「」外，**輸出不要包含其他標點符號（，。、；：？！等）**；"
        "語氣停頓用半形空格分隔即可\n"
        "- **同一行內兩個獨立分句黏在一起時，分界處補半形空格**"
        "（例：「配套的做法那有幾個配套就是」→「配套的做法 那有幾個配套 就是」）——"
        "承接詞（那/就是/然後/可是/所以）起新句是典型分界\n"
        "- **純遲疑語助詞「呃」一律刪除**；行首遲疑的「啊」刪除（句尾語氣的"
        "「累啊」「好處吧」保留）\n"
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
    return system


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


_ASCII_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9'\-]*$")
_BUF_TRAILING_ASCII_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\- ]*$")


def _force_break(text: str, max_chars: int, hard_max: int | None = None) -> list[str]:
    """強制斷行，避免切斷中文詞語與英文單字。

    走 jieba 中文分詞 + 英文 token，每個 chunk greedy 累加到 ≤max_chars 字停。
    若單一 token 已超過 max_chars（罕見：超長英文 / URL），該 token 獨立成 chunk。

    soft / hard 雙閾值：
    - soft = max_chars：常態目標
    - hard = hard_max（預設 max_chars + 8）：當下個 token 是 ASCII 英文 / 接續英文單字的 chunk
      時容許 overflow 到 hard，避免「Traveling Village」這類 compound 被切

    原呼叫端（legacy transcribe() 的句子拆分）已退役；run_short_tighten 的細切
    註解仍以本函式為「jieba 詞邊界斷行」的參考實作。
    """
    import jieba

    ensure_tw_jieba()
    if hard_max is None:
        hard_max = max_chars + 8
    tokens = list(jieba.cut(text, cut_all=False))
    chunks: list[str] = []
    buf = ""
    for tok in tokens:
        if not tok.strip():
            buf += tok
            continue
        # soft fits → take
        if len(buf) + len(tok) <= max_chars:
            buf += tok
            continue
        # soft 不夠但 hard 容許，且 token 是 ASCII 英文 + buf 結尾連續 ASCII 英文
        # （保 compound name "Traveling Village" / "Hell Yes" 不被切，
        #   即使前面緊鄰中文無空格）→ overflow
        is_ascii_english = bool(_ASCII_TOKEN_RE.match(tok))
        buf_ends_english = bool(_BUF_TRAILING_ASCII_RE.search(buf))
        if is_ascii_english and buf_ends_english and len(buf) + len(tok) <= hard_max:
            buf += tok
            continue
        # 真要 break
        if buf.strip():
            chunks.append(buf.strip())
        if len(tok) > hard_max:
            chunks.append(tok)
            buf = ""
        else:
            buf = tok
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


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


def _get_asr_model(
    model_id: str = "large-v3",
    device: str = "cuda",
    initial_prompt: str = "",
):
    """取得 WhisperX 模型（faster-whisper backend，lazy singleton）。

    `initial_prompt` 走 WhisperX 的 `asr_options` — WhisperX 的 transcribe() 不接
    initial_prompt，必須在 load 時就 bake 進去。Singleton key 包含 prompt，
    prompt 變了會 reload model。

    Anti-hallucination 預設：低 SNR / silence 段 Whisper 容易 echo prompt 內容
    或重複前一 segment（觀察到 cue 70 等 10 處輸出「主持人 張修修」吃掉~110s）。
    用 faster-whisper 三件套防：
    - `condition_on_previous_text=False` 不讓上一 segment 文字 propagate
    - `compression_ratio_threshold=2.4` 過度重複 segment 視為 hallucination 丟掉
    - `no_speech_threshold=0.6` silence 段更積極跳過
    """
    global _asr_model, _asr_model_id

    cache_key = (model_id, initial_prompt)
    if _asr_model is not None and _asr_model_id == cache_key:
        return _asr_model

    import whisperx

    logger.info(f"載入 WhisperX 模型: {model_id}（initial_prompt {len(initial_prompt)} 字）")
    asr_options: dict = {
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 2.4,
        "no_speech_threshold": 0.6,
    }
    if initial_prompt:
        asr_options["initial_prompt"] = initial_prompt

    _asr_model = whisperx.load_model(
        model_id,
        device=device,
        compute_type="float16",
        language="zh",
        asr_options=asr_options,
    )
    _asr_model_id = cache_key
    logger.info("WhisperX 模型載入完成")
    return _asr_model


def _get_align_model(language: str, device: str = "cuda"):
    """取得 WhisperX align model（word-level timestamps，lazy singleton）。

    Chinese (zh) 用 wav2vec2 中文對齊模型；align 模型不存在時 raise，
    呼叫端應 catch + fallback 到 segment-level timestamp（不影響純 SRT 輸出）。
    """
    global _align_model, _align_metadata, _align_language
    if _align_model is not None and _align_language == language:
        return _align_model, _align_metadata

    import whisperx

    logger.info(f"載入 WhisperX align 模型: {language}")
    _align_model, _align_metadata = whisperx.load_align_model(
        language_code=language,
        device=device,
    )
    _align_language = language
    return _align_model, _align_metadata


def _build_initial_prompt(
    hotwords: list[str],
    project_context: dict | None,
    host_name: str = "",
    show_name: str = "",
) -> str:
    """組成 Whisper `initial_prompt`（純逗號分隔詞表，非 label 結構）。

    Whisper 接 initial_prompt 作為 LM 偏置；用「主持人：X」「節目：Y」這種 label
    結構在低 SNR / silence 段會 hallucinate 整段 label 文字（觀察到 cue 70 等
    10 處出現「主持人 張修修」吃掉~110s 真實內容）。改純詞表後 Whisper 仍可
    bias vocabulary 但不會 echo 整段 label。
    """
    words: list[str] = []
    if show_name:
        words.append(show_name)
    if host_name:
        words.append(host_name)
    if project_context:
        if guest := project_context.get("guest_name"):
            words.append(guest)
        if topic := project_context.get("topic"):
            words.append(topic)
    if hotwords:
        words.extend(hotwords[:30])
    # de-dupe 保留順序
    seen: set[str] = set()
    deduped = [w for w in words if not (w in seen or seen.add(w))]
    return "、".join(deduped)
