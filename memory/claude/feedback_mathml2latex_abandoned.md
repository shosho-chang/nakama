---
name: mathml2latex PyPI 0.1.0 是 abandoned，MathML → LaTeX 走 alttext-first
description: PyPI mathml2latex package 公開 API 空白，不要當 dep；MathML 轉 LaTeX 用 `<math alttext="...">` 屬性 + 文字 fallback
type: feedback
originSessionId: 211fa78f-698e-45a6-9e46-142599efead2
---
動 EPUB / MathML 轉 LaTeX 不要加 `mathml2latex` PyPI dep；用 alttext-first path。

**Why:** ADR-011 §3.4.1 列 `mathml2latex>=0.0.5` 當 dep，實測 PyPI v0.1.0 ships `__init__.py` 只有 `__version__ = '0.1.0'`，無公開 callable。內部 modules（`mathml.py` `parts_pickup.py`）有 `process_math` / `process_mathml` 但 signature 是 `(descendant, insertion_list)` 內部 mutation pattern，不是用戶可呼叫的 high-level API。Package 看起來是 abandoned。

**How to apply:**
- 動 EPUB / textbook ingest 遇到 `<math>` tag 時，**第一順位**用 `tag.get('alttext')` — 現代教科書 EPUB 都有 accessibility `alttext` 屬性帶 LaTeX 或可讀文字
- alttext 缺時 fallback `tag.get_text(strip=True)` 把 MathML 的 mi/mn/mo 字面內容拉出來（會失去 frac/sup 結構，但保留 identifier + 數字給下游 LLM 看）
- 兩者都空就回空字串（caller 決定如何處理空 placeholder）
- **不要**加 `mathml2latex` 到 pyproject.toml / requirements.txt
- 範例：`.claude/skills/textbook-ingest/scripts/parse_book.py` `_html_math_to_latex`

**未來路徑**（如果 alttext-first 不夠用）：
- 自己寫 walker 處理 mfrac / msup / msub / msqrt（MathML 常見子集）
- 找維護中的替代 lib（如 `pylatexenc` 或 `mathjax-node` Python wrapper）
- 不要再回 mathml2latex
