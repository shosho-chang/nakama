# ruff: noqa: E501
"""Multi-model blocker verification — 驗證修訂後的 ADR 是否解掉上一輪 review 的 blocker。

比 adr_multi_model_review.py 更聚焦：餵新 ADR + 上一輪 blocker list，問「解了嗎 + 新問題？」

輸出：docs/decisions/multi-model-review/VERIFICATION--{adr}--{model}.md
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

# (adr_file, consolidated_file) — 005a/005b 共用原 ADR-005 的 CONSOLIDATED
ADRS: list[tuple[str, str]] = [
    ("ADR-005a-brook-gutenberg-pipeline", "ADR-005--CONSOLIDATED"),
    ("ADR-005b-usopp-wp-publishing", "ADR-005--CONSOLIDATED"),
    ("ADR-006-hitl-approval-queue", "ADR-006--CONSOLIDATED"),
    ("ADR-007-franky-scope-expansion", "ADR-007--CONSOLIDATED"),
]

MODELS: list[tuple[str, str]] = [
    ("claude-sonnet-4-6", "claude-sonnet"),
    ("grok-4", "grok"),
    ("gemini-2.5-pro", "gemini"),
]

VERIFICATION_PROMPT = """你是一位資深軟體架構師，正在驗證一份 ADR 的**修訂版**是否解決了上一輪 multi-model review 指出的 blocker。

## 上下文

這是 Nakama AI Agent 團隊的 ADR，部署於 Vultr VPS（2vCPU / 4GB），服務台灣健康內容創作者。Nakama 有多個 agent：Brook 寫作、Usopp 發佈、Franky 監控。所有對外發佈前必須 HITL 審核。

Nakama 專案有三份通用原則（新 ADR 可援引）：
- `docs/principles/schemas.md` — Pydantic schema、版本欄位、嚴格 extra=forbid
- `docs/principles/reliability.md` — idempotency、atomic claim、SPOF、retry
- `docs/principles/observability.md` — structured log、operation_id、外部 probe、SLO

## 你的任務

讀以下兩份內容，產出**聚焦的驗證報告**（繁體中文）：

### 輸入 A — 上一輪的 Consolidated Review（blocker list 在裡面）

{consolidated_content}

---

### 輸入 B — 修訂後的 ADR

{adr_content}

---

## 輸出結構（嚴格按此）

### 1. Blocker 逐項檢核

針對 Consolidated Review §5「最 blocking 的問題」每一項，給：
- **原 blocker 一句話描述**
- **修訂版回應**：✅ 完整解 / ⚠️ 部分解（說明還差什麼）/ ❌ 未解 / 🔄 拆到別處（指明去向）
- **證據**：修訂版 ADR 哪個章節或段落處理了這點

### 2. 新發現的問題

修訂版引入或暴露的**新問題**（原 review 沒抓到的）：
- 按嚴重度排序
- 每項：問題描述 / 嚴重度（Critical / High / Medium / Low）/ 建議修法

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | X | ... |
| Reliability 機制（idempotency / atomic / SPOF）| X | ... |
| Observability（log / metric / SLO / probe）| X | ... |
| 可實作性（工程師照寫能不能動） | X | ... |
| 範圍聚焦度（沒再 scope creep） | X | ... |

### 4. 最終判定

- **go / no-go**：工程師可以開始寫 code 了嗎？
- **如果 no-go**：必須先修的 1-2 個 blocking 項目
- **如果 go**：Phase 1 實作過程中要特別盯的 1-2 個風險

---

嚴格、具體、不客套。修訂版真的改好就說改好，沒改就直接指出。
"""


def verify_one(
    adr_name: str, consolidated_name: str, model_id: str, label: str
) -> tuple[str, str, int | str]:
    adr_content = (ADR_DIR / f"{adr_name}.md").read_text(encoding="utf-8")
    consolidated_content = (OUT_DIR / f"{consolidated_name}.md").read_text(encoding="utf-8")
    prompt = VERIFICATION_PROMPT.format(
        adr_content=adr_content,
        consolidated_content=consolidated_content,
    )

    start = time.time()
    try:
        review = ask(prompt, model=model_id, max_tokens=6000)
    except Exception as e:
        return adr_name, label, f"FAILED: {type(e).__name__}: {e}"

    elapsed = int(time.time() - start)
    out_file = OUT_DIR / f"VERIFICATION--{adr_name}--{label}.md"
    header = (
        f"---\n"
        f"source_adr: {adr_name}\n"
        f"verification_round: 2\n"
        f"reviewer_model: {model_id}\n"
        f"elapsed_seconds: {elapsed}\n"
        f"review_date: 2026-04-22\n"
        f"---\n\n"
        f"# {adr_name} — 修訂驗證（{label}）\n\n"
    )
    out_file.write_text(header + review, encoding="utf-8")
    return adr_name, label, elapsed


def main() -> None:
    jobs = [(adr, cons, model_id, label) for (adr, cons) in ADRS for (model_id, label) in MODELS]

    print(f"Dispatching {len(jobs)} verification jobs in parallel...")
    results: list[tuple[str, str, int | str]] = []

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(verify_one, *job): job for job in jobs}
        for fut in as_completed(futures):
            adr, label, status = fut.result()
            results.append((adr, label, status))
            print(f"  [{adr} × {label}] → {status}")

    print("\n--- Summary ---")
    for adr, label, status in sorted(results):
        marker = "✓" if isinstance(status, int) else "✗"
        print(f"  {marker} {adr} × {label}: {status}")

    print(f"\nVerifications saved in: {OUT_DIR}")


if __name__ == "__main__":
    main()
