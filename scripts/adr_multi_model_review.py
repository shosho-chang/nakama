# ruff: noqa: E501
"""多模型 ADR review — 把 ADR-005/006/007 送給 Claude Sonnet / Grok 4 / Gemini 2.5 Pro 各自獨立審查。

輸出：docs/decisions/multi-model-review/ADR-xxx--{model}.md

用法：
    python -m scripts.adr_multi_model_review
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.llm import ask  # noqa: E402

ROOT = Path(__file__).parent.parent
ADR_DIR = ROOT / "docs" / "decisions"
OUT_DIR = ADR_DIR / "multi-model-review"

ADRS = [
    "ADR-005-publishing-infrastructure",
    "ADR-006-hitl-approval-queue",
    "ADR-007-franky-scope-expansion",
]

MODELS: list[tuple[str, str]] = [
    ("claude-sonnet-4-6", "claude-sonnet"),
    ("grok-4", "grok"),
    ("gemini-2.5-pro", "gemini"),
]

REVIEW_PROMPT = """你是一位資深軟體架構師，正在為一個 AI Agent 團隊（代號 Nakama）審查一份架構決策文件（ADR）。

這個系統部署在 Vultr VPS（2vCPU / 4GB RAM / 128GB NVMe），服務對象是一位台灣健康與長壽領域的內容創作者（修修）。Nakama 包含多個 agent（Brook 寫作、Usopp 發佈、Franky 監控、Chopper 社群、Robin PubMed digest 等），目標是把部落格寫作、社群互動、SEO 監控自動化。所有對外發佈前都必須經 HITL（Human-in-the-loop）審核。

你對作者沒有義務，請挑剔、嚴格、具體。避免客套話。繁體中文輸出。

## 審查結構（請嚴格按此輸出）

### 1. 核心假設檢驗
這份 ADR 基於哪些未明說的假設？哪些假設容易出錯？

### 2. 風險分析
- (a) 未被提及但會造成生產問題的風險
- (b) 已提及但嚴重度被低估的風險
- (c) 已提及但嚴重度被高估的風險

### 3. 替代方案
有沒有更簡單或更 proven 的替代路徑？為什麼作者沒選？有沒有更穩的第三方工具 / library 能取代自建？

### 4. 實作 pitfalls
如果工程師照這個 ADR 寫，最容易踩到哪些坑？具體指出檔名 / schema / API 契約會出問題的點。

### 5. 缺失的視角
資安 / 效能 / 運維 / 法規 / 可觀測性 / 成本 / 可測試性 / 可維護性 — 哪一塊沒講到或講得太輕？

### 6. Phase 拆分建議
哪些內容應該再拆成獨立 ADR？哪些可以延後到 Phase 2+？哪些是必須 Phase 1 完成？

### 7. 結論
- (a) 整體可行性評分 1-10（含簡短理由）
- (b) 建議：通過 / 修改後通過 / 退回重寫
- (c) 最 blocking 的 1-2 個問題（必須先解決才能開工）

---

# 以下是 ADR 全文：

{adr_content}
"""


def review_one(adr_name: str, model_id: str, label: str) -> tuple[str, str, int | str]:
    adr_file = ADR_DIR / f"{adr_name}.md"
    adr_content = adr_file.read_text(encoding="utf-8")
    prompt = REVIEW_PROMPT.format(adr_content=adr_content)

    start = time.time()
    try:
        review = ask(prompt, model=model_id, max_tokens=8192)
    except Exception as e:
        return adr_name, label, f"FAILED: {type(e).__name__}: {e}"

    elapsed = int(time.time() - start)
    out_file = OUT_DIR / f"{adr_name}--{label}.md"
    header = (
        f"---\n"
        f"source_adr: {adr_name}\n"
        f"reviewer_model: {model_id}\n"
        f"elapsed_seconds: {elapsed}\n"
        f"review_date: 2026-04-22\n"
        f"---\n\n"
        f"# {adr_name} — {label} 審查\n\n"
    )
    out_file.write_text(header + review, encoding="utf-8")
    return adr_name, label, elapsed


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[str, str, str]] = [
        (adr, model_id, label) for adr in ADRS for (model_id, label) in MODELS
    ]

    print(f"Dispatching {len(jobs)} review jobs in parallel...")
    results: list[tuple[str, str, int | str]] = []

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(review_one, *job): job for job in jobs}
        for fut in as_completed(futures):
            adr, label, status = fut.result()
            results.append((adr, label, status))
            print(f"  [{adr} × {label}] → {status}")

    print("\n--- Summary ---")
    for adr, label, status in sorted(results):
        marker = "✓" if isinstance(status, int) else "✗"
        print(f"  {marker} {adr} × {label}: {status}")

    print(f"\nReviews saved in: {OUT_DIR}")


if __name__ == "__main__":
    main()
