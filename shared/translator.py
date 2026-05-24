"""雙語翻譯模組：Claude Sonnet + 台灣術語表，輸出雙語 Markdown。

使用方式：
    from shared.translator import translate_document, add_glossary_term

    bilingual_md = translate_document(original_text)
    add_glossary_term("mitophagy", "粒線體自噬")
"""

import json
import re
from pathlib import Path

import yaml

from shared.llm import ask
from shared.log import get_logger

logger = get_logger("nakama.shared.translator")

_GLOSSARY_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "robin" / "translation_tw_glossary.yaml"
)
# Model 解析交給 router：caller 走 ask(task="translate") 即可吃到
# DEFAULT_MODELS["translate"] / MODEL_<AGENT>_TRANSLATE override（ADR-026）。
_TRANSLATE_TASK = "translate"
_BATCH_SIZE = 20
_BATCH_MAX_TOKENS = 16384
_SEGMENT_MAX_TOKENS = 4096


def load_glossary() -> dict[str, str]:
    """讀取台灣術語表，回傳 {英文: 台灣中文} dict。

    合併 terms（人工維護）與 user_terms（Robin 自動學習），
    user_terms 的值優先覆蓋 terms。
    """
    if not _GLOSSARY_PATH.exists():
        return {}
    with _GLOSSARY_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    merged = dict(data.get("terms", {}))
    merged.update(data.get("user_terms", {}))
    return merged


def add_glossary_term(english: str, zh_tw: str) -> None:
    """新增或更新一條學習術語到 user_terms 區塊。

    只更新 YAML 末尾的 user_terms: 區塊，不覆寫主 terms: 區塊，
    確保人工維護的 section 注釋永遠不被破壞。

    呼叫方注意：翻譯 API 成本由呼叫方透過 set_current_agent() 歸因，
    此 shared module 本身不設定 agent context。
    """
    text = _GLOSSARY_PATH.read_text(encoding="utf-8") if _GLOSSARY_PATH.exists() else ""
    data = yaml.safe_load(text) or {}
    user_terms: dict = dict(data.get("user_terms", {}))
    user_terms[english.lower()] = zh_tw
    user_terms_yaml = yaml.dump(
        {"user_terms": dict(sorted(user_terms.items()))},
        allow_unicode=True,
        default_flow_style=False,
    )
    if "user_terms:" in text:
        idx = text.index("user_terms:")
        text = text[:idx] + user_terms_yaml
    else:
        text = text.rstrip() + "\n\n" + user_terms_yaml
    _GLOSSARY_PATH.write_text(text, encoding="utf-8")
    logger.info(f"術語表更新：{english} → {zh_tw}")


def split_paragraphs(text: str) -> list[str]:
    """將文字切分成段落陣列（以兩個以上換行為分隔符）。"""
    paragraphs = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in paragraphs if p.strip()]


# Reference-list heading detection. A heading matches when, after stripping
# markdown ``#`` / leading numbering / trailing punctuation, every token is
# either in the reference whitelist below or a connective (``and``/``&``/``,``/
# ``or``/``、``). This catches compound forms like ``References and Notes``
# (Science journal default) or ``Bibliography & Further Reading`` without
# enumerating every permutation.
# 修修 2026-05-24: any article with a reference list, don't translate refs.
_REFERENCE_TOKENS: frozenset[str] = frozenset(
    t.casefold()
    for t in (
        "references",
        "reference",
        "bibliography",
        "works",
        "cited",
        "literature",
        "sources",
        "source",
        "citations",
        "citation",
        "notes",
        "note",
        "further",
        "reading",
        # CJK reference-list heading words (no whitespace tokenization, matched
        # against the heading as a whole via _CJK_REFERENCE_HEADINGS below).
    )
)
_CJK_REFERENCE_HEADINGS: frozenset[str] = frozenset(
    (
        "參考文獻",
        "文獻",
        "註釋",
        "注釋",
        "參考",
        "引用",
    )
)
_CONNECTIVE_TOKENS: frozenset[str] = frozenset(("and", "or", "&", ",", "、"))
_REF_HEADING_RE = re.compile(
    r"^(?P<hashes>#{2,3})\s+(?:\d+[.)]\s+|§\s*)?(?P<text>[^\n#]+?)\s*[:：.。]?\s*$",
    re.MULTILINE,
)


def _normalise_heading(heading: str) -> str:
    """Strip trailing punctuation / whitespace from a heading."""
    return heading.strip().rstrip(":：.。 ").casefold()


def _is_reference_heading(heading: str) -> bool:
    """Return True if ``heading`` (already markdown-stripped) names a reference
    list. A heading matches when every token is a reference word or a
    connective. Single-word CJK headings match by exact lookup.
    """
    norm = _normalise_heading(heading)
    if not norm:
        return False
    if norm in _CJK_REFERENCE_HEADINGS:
        return True
    # ``References and Notes`` → ["references", "and", "notes"]. Split on
    # whitespace and ASCII punctuation that acts as a connective.
    tokens = re.split(r"[\s,/&]+", norm)
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    has_ref_word = False
    for tok in tokens:
        if tok in _REFERENCE_TOKENS:
            has_ref_word = True
        elif tok in _CONNECTIVE_TOKENS:
            continue
        else:
            return False
    return has_ref_word


def split_off_reference_section(text: str) -> tuple[str, str]:
    """Split ``text`` at the first reference-list heading.

    Returns ``(body, ref_section)`` where ``ref_section`` is the heading and
    everything after it (passed through untranslated), and ``body`` is the
    content before that heading. Returns ``(text, "")`` if no reference
    heading is detected.

    See ``_is_reference_heading`` for the detection rule.
    """
    for match in _REF_HEADING_RE.finditer(text):
        if _is_reference_heading(match.group("text")):
            return text[: match.start()].rstrip(), text[match.start() :]
    return text, ""


def _build_system_prompt(glossary: dict[str, str]) -> str:
    """建立翻譯 system prompt，注入台灣術語表。"""
    base = (
        "你是一位專業學術翻譯員，專精生命科學、睡眠醫學、運動科學和營養學。"
        "使用**台灣繁體中文**，遵循台灣學術界術語習慣（非中國大陸用語）。"
        "保留英文人名、機構名、期刊名不翻譯。保留 Markdown 標題符號（#）、粗體（**）、連結等格式。"
        "數字與單位保留英文（如 p < 0.05、95% CI、mg/kg）。"
    )
    if not glossary:
        return base
    terms_str = "\n".join(f"- {en} → {zh}" for en, zh in sorted(glossary.items()))
    return f"{base}\n\n**術語對照表（必須嚴格遵守，不得使用其他譯名）：**\n{terms_str}"


def translate_segments(
    segments: list[str],
    *,
    model: str | None = None,
    glossary: dict[str, str] | None = None,
) -> list[str]:
    """批次翻譯段落陣列，回傳等長譯文陣列。

    Args:
        segments: 原文段落陣列
        model:    翻譯模型。``None`` 走 router ``task="translate"`` 解析
                  （MODEL_<AGENT>_TRANSLATE > MODEL_<AGENT> > DEFAULT_MODELS["translate"]）。
        glossary: 術語表（None 時自動載入）

    Returns:
        與 segments 等長的譯文陣列，失敗段落為空字串
    """
    if not segments:
        return []

    if glossary is None:
        glossary = load_glossary()
    system = _build_system_prompt(glossary)

    numbered = "\n\n".join(f"[{i + 1}]\n{seg}" for i, seg in enumerate(segments))
    prompt = (
        f"請將以下 {len(segments)} 段學術文字翻譯成台灣繁體中文。\n"
        '回傳純 JSON 陣列，格式：[{"index": 1, "translation": "..."}, ...]\n'
        "不要有任何其他說明或 Markdown 包裝。\n\n"
        f"{numbered}"
    )

    response = ask(
        prompt,
        system=system,
        model=model,
        task=_TRANSLATE_TASK,
        max_tokens=_BATCH_MAX_TOKENS,
    )

    try:
        json_match = re.search(r"\[[\s\S]*\]", response)
        if not json_match:
            raise ValueError("回應中找不到 JSON 陣列")
        results: list[dict] = json.loads(json_match.group())
        translations = {item["index"]: item["translation"] for item in results}
        return [translations.get(i + 1, "") for i in range(len(segments))]
    except Exception as e:
        logger.error(f"批次翻譯解析失敗（{e}），降級逐段翻譯")
        return _translate_one_by_one(segments, system=system, model=model)


def _translate_one_by_one(segments: list[str], *, system: str, model: str | None) -> list[str]:
    """降級方案：逐段翻譯（批次解析失敗時使用）。"""
    results = []
    for i, seg in enumerate(segments):
        try:
            t = ask(
                f"翻譯成台灣繁體中文（只回傳譯文，不要其他說明）：\n\n{seg}",
                system=system,
                model=model,
                task=_TRANSLATE_TASK,
                max_tokens=_SEGMENT_MAX_TOKENS,
            )
            results.append(t.strip())
        except Exception as e:
            logger.error(f"段落 {i + 1} 翻譯失敗：{e}")
            results.append("")
    return results


def format_bilingual_markdown(originals: list[str], translations: list[str]) -> str:
    """組合雙語 Markdown：每段原文後接 blockquote 格式的譯文。

    格式：
        原文段落

        > 台灣繁體中文譯文

    Args:
        originals:    原文段落陣列
        translations: 譯文段落陣列（與 originals 等長）

    Returns:
        雙語 Markdown 字串
    """
    pairs = []
    for orig, trans in zip(originals, translations):
        if trans:
            trans_quoted = "\n".join(
                f"> {line}" if line.strip() else ">" for line in trans.split("\n")
            )
            pairs.append(f"{orig}\n\n{trans_quoted}")
        else:
            pairs.append(orig)
    return "\n\n".join(pairs)


# Pure-image paragraphs (one or more ``![alt](src)`` with only whitespace
# between) carry no translatable text — sending them to LLM wastes tokens and
# the echo gets wrapped in a blockquote, rendering the image twice in the
# reader. Detect and passthrough.
_IMAGE_ONLY_RE = re.compile(r"^\s*(?:!\[[^\]]*\]\([^)\s]+\)\s*)+$")


def _is_image_only_segment(segment: str) -> bool:
    """True if the segment is one or more markdown images and nothing else."""
    return bool(_IMAGE_ONLY_RE.match(segment))


def translate_document(
    text: str,
    *,
    batch_size: int = _BATCH_SIZE,
    model: str | None = None,
) -> str:
    """翻譯整份文件，回傳雙語 Markdown。

    Args:
        text:       原始文字（Markdown 格式）
        batch_size: 每批次翻譯的段落數
        model:      翻譯模型

    Returns:
        雙語 Markdown：每段原文後緊接 blockquote 譯文
    """
    body, ref_section = split_off_reference_section(text)
    segments = split_paragraphs(body)
    if not segments:
        # Pure-reference doc or empty body — return as-is (no LLM call).
        return text

    # Partition: image-only segments pass through verbatim; text segments go
    # to the LLM. ``image_passthrough`` keeps the original index so the final
    # output preserves segment ordering.
    text_segments: list[str] = []
    text_indices: list[int] = []
    image_passthrough: dict[int, str] = {}
    for i, seg in enumerate(segments):
        if _is_image_only_segment(seg):
            image_passthrough[i] = ""  # marker — formatter renders no blockquote
        else:
            text_segments.append(seg)
            text_indices.append(i)

    skip_msg = ""
    if image_passthrough:
        skip_msg += f"，image-only 段 {len(image_passthrough)} 段已跳過"
    if ref_section:
        skip_msg += f"，reference 區塊 {len(ref_section)} 字元已跳過"
    logger.info(f"開始翻譯：{len(text_segments)} 段落，batch_size={batch_size}{skip_msg}")

    glossary = load_glossary()
    text_translations: list[str] = []
    for batch_start in range(0, len(text_segments), batch_size):
        batch = text_segments[batch_start : batch_start + batch_size]
        translations = translate_segments(batch, model=model, glossary=glossary)
        text_translations.extend(translations)
        done = min(batch_start + batch_size, len(text_segments))
        logger.info(f"  翻譯進度：{done}/{len(text_segments)} 段")

    # Reassemble translations in original segment order.
    all_translations: list[str] = [""] * len(segments)
    for slot, idx in enumerate(text_indices):
        all_translations[idx] = text_translations[slot]
    # image_passthrough already maps idx → "" (no-blockquote marker).

    bilingual = format_bilingual_markdown(segments, all_translations)
    if ref_section:
        # Pass through reference list verbatim — no translation, no blockquote.
        bilingual = f"{bilingual}\n\n{ref_section}"
    return bilingual
