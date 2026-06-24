"""Tolerant JSON extraction from LLM output.

LLM 常把 JSON 包在 ` ```json ` code fence 裡，或在前面加一段 reasoning preamble（即使
prompt 明令禁止）。本模組用「去 fence + 從第一個括號做深度配對」把 array/object 撈出來
再餵 ``json.loads``，避免「LLM 其實有給、卻被天真的 regex 丟掉」。

緣由：``agents/robin/daily_review.py`` P-1/P-2 原本用貪婪 ``\\[[\\s\\S]*\\]`` 抓 JSON——
遇到 ``max_tokens`` 截斷（無收尾 ``]``）或 fence/preamble 就回空 → 每日回顧吐 0 候選
（2026-06-24 實機抓到）。集中 ``shared/memory_reflection`` 既有的 fence/bracket-depth
做法成可重用工具；額外加上「字串內括號不計」讓 content 裡的 ``[]`` / ``{}`` 不干擾。
"""

from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """剝掉 ` ```json … ``` ` 外殼；沒有 fence 就原樣回（已 strip）。"""
    t = (text or "").strip()
    m = _FENCE.search(t)
    return m.group(1).strip() if m else t


def _extract_balanced(s: str, open_ch: str, close_ch: str) -> str | None:
    """從第一個 ``open_ch`` 起做括號深度配對，回傳第一個 balanced span。

    字串字面值內的括號不計（避免 ``"quote": "see [1]"`` 這種干擾）。找不到起點或
    被截斷（深度永不歸零）→ ``None``。
    """
    start = s.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def extract_json_array(text: str) -> list:
    """LLM 輸出 → ``list``（容忍 fence / preamble / 截斷）。失敗回 ``[]``。"""
    span = _extract_balanced(strip_code_fence(text), "[", "]")
    if span is None:
        return []
    try:
        data = json.loads(span)
    except (json.JSONDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def extract_json_object(text: str) -> dict:
    """LLM 輸出 → ``dict``（容忍 fence / preamble / 截斷）。失敗回 ``{}``。"""
    span = _extract_balanced(strip_code_fence(text), "{", "}")
    if span is None:
        return {}
    try:
        data = json.loads(span)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
