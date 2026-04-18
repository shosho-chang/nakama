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

from shared.anthropic_client import ask_claude
from shared.log import get_logger

logger = get_logger("nakama.shared.translator")

_GLOSSARY_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "robin" / "translation_tw_glossary.yaml"
)
_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_BATCH_SIZE = 20


def load_glossary() -> dict[str, str]:
    """讀取台灣術語表，回傳 {英文: 台灣中文} dict。"""
    if not _GLOSSARY_PATH.exists():
        return {}
    with _GLOSSARY_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("terms", {})


def add_glossary_term(english: str, zh_tw: str) -> None:
    """新增或更新一條術語（用於 Robin 術語學習）。

    若術語已存在則覆蓋，不存在則新增。
    """
    data: dict = {}
    if _GLOSSARY_PATH.exists():
        with _GLOSSARY_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    terms: dict = data.get("terms", {})
    terms[english.lower()] = zh_tw
    data["terms"] = dict(sorted(terms.items()))
    with _GLOSSARY_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    logger.info(f"術語表更新：{english} → {zh_tw}")


def split_paragraphs(text: str) -> list[str]:
    """將文字切分成段落陣列（以兩個以上換行為分隔符）。"""
    paragraphs = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in paragraphs if p.strip()]


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
    model: str = _DEFAULT_MODEL,
    glossary: dict[str, str] | None = None,
) -> list[str]:
    """批次翻譯段落陣列，回傳等長譯文陣列。

    Args:
        segments: 原文段落陣列
        model:    翻譯模型
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

    response = ask_claude(prompt, system=system, model=model, max_tokens=8192)

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


def _translate_one_by_one(segments: list[str], *, system: str, model: str) -> list[str]:
    """降級方案：逐段翻譯（批次解析失敗時使用）。"""
    results = []
    for i, seg in enumerate(segments):
        try:
            t = ask_claude(
                f"翻譯成台灣繁體中文（只回傳譯文，不要其他說明）：\n\n{seg}",
                system=system,
                model=model,
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


def translate_document(
    text: str,
    *,
    batch_size: int = _BATCH_SIZE,
    model: str = _DEFAULT_MODEL,
) -> str:
    """翻譯整份文件，回傳雙語 Markdown。

    Args:
        text:       原始文字（Markdown 格式）
        batch_size: 每批次翻譯的段落數
        model:      翻譯模型

    Returns:
        雙語 Markdown：每段原文後緊接 blockquote 譯文
    """
    segments = split_paragraphs(text)
    if not segments:
        return text

    logger.info(f"開始翻譯：{len(segments)} 段落，batch_size={batch_size}")
    glossary = load_glossary()

    all_translations: list[str] = []
    for batch_start in range(0, len(segments), batch_size):
        batch = segments[batch_start : batch_start + batch_size]
        translations = translate_segments(batch, model=model, glossary=glossary)
        all_translations.extend(translations)
        done = min(batch_start + batch_size, len(segments))
        logger.info(f"  翻譯進度：{done}/{len(segments)} 段")

    return format_bilingual_markdown(segments, all_translations)
