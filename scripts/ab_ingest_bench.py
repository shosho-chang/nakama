"""A/B benchmark for local LLM used in Robin's ingest pipeline.

測試對象：Robin 的 Map 步驟（`summarize_chunk`）— 大於 30K 字元的文件會觸發，
目前由本地 llama.cpp server 服務（Gemma 4 26B-A4B Q4_K_M）。

本工具讓你用同一份文件，串到兩個不同的本地模型（例如 Gemma 4 vs Qwen 3.6），
比對輸出品質、速度、字數，產出可並列審視的 markdown 報告。

## 使用流程

因為 16GB VRAM 無法同時載入兩個大模型，需要輪流啟動 server：

1. 啟動 Gemma server（`scripts/start_llm_server.bat`）
2. `python scripts/ab_ingest_bench.py run <doc> --label gemma`
3. 關閉 Gemma，啟動 Qwen server（`scripts/start_qwen_server.bat`）
4. `python scripts/ab_ingest_bench.py run <doc> --label qwen`
5. `python scripts/ab_ingest_bench.py report <doc>` — 產出並列比對報告

## 輸出位置

`data/ab_bench/<doc-slug>/`
  ├── gemma.json          # run --label gemma 的結果
  ├── qwen.json           # run --label qwen 的結果
  └── REPORT.md           # report subcommand 產出的比對報告
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows 預設 cp1252 stdout 無法印中文，強制切 UTF-8
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# 讓 script 可以從 repo 根目錄匯入 nakama modules
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.robin.chunker import chunk_document  # noqa: E402
from shared.local_llm import ask_local, is_server_available  # noqa: E402
from shared.prompt_loader import load_prompt  # noqa: E402
from shared.utils import read_text, slugify  # noqa: E402

BENCH_DIR = REPO_ROOT / "data" / "ab_bench"


def _doc_slug(doc_path: Path) -> str:
    return slugify(doc_path.stem)


def _run_dir(doc_path: Path) -> Path:
    d = BENCH_DIR / _doc_slug(doc_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_doc(doc_path: Path) -> tuple[str, str]:
    """讀文件，回傳 (title, content)。支援 .md / .txt / .pdf。"""
    if doc_path.suffix.lower() == ".pdf":
        from shared.pdf_parser import parse_pdf

        content = parse_pdf(doc_path, with_tables=False)
    else:
        content = read_text(doc_path)
    return doc_path.stem, content


def cmd_run(args: argparse.Namespace) -> int:
    doc_path = Path(args.doc).resolve()
    if not doc_path.exists():
        print(f"ERROR: 文件不存在：{doc_path}", file=sys.stderr)
        return 1

    base_url = args.base_url
    if not is_server_available(base_url):
        print(f"ERROR: LLM server 不可用（{base_url}）。請先啟動 llama.cpp。", file=sys.stderr)
        return 1

    title, content = _load_doc(doc_path)
    print(f"[{args.label}] 文件：{title}（{len(content):,} 字元）")

    chunks = chunk_document(content)
    print(f"[{args.label}] 分成 {len(chunks)} 個 chunk")

    results = []
    total_start = time.perf_counter()

    for chunk in chunks:
        prompt = load_prompt(
            "robin",
            "summarize_chunk",
            chunk_index=str(chunk["index"]),
            total_chunks=str(len(chunks)),
            title=title,
            heading=chunk["heading"],
            content=chunk["text"],
        )

        prefix = f"[{args.label}] chunk {chunk['index']}/{len(chunks)}（{chunk['heading']}）..."
        print(prefix, end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            output = ask_local(
                prompt,
                system="你是 Robin，Nakama 團隊的考古學家，負責知識庫管理。",
                base_url=base_url,
                model=args.model_name,
                max_tokens=2048,
                temperature=0.3,
            )
            elapsed = time.perf_counter() - t0
            print(f"{elapsed:.1f}s，{len(output)} 字元")
            results.append(
                {
                    "index": chunk["index"],
                    "heading": chunk["heading"],
                    "input_chars": len(chunk["text"]),
                    "output": output,
                    "output_chars": len(output),
                    "duration_sec": round(elapsed, 2),
                    "error": None,
                }
            )
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"FAILED（{elapsed:.1f}s）：{e}")
            results.append(
                {
                    "index": chunk["index"],
                    "heading": chunk["heading"],
                    "input_chars": len(chunk["text"]),
                    "output": None,
                    "output_chars": 0,
                    "duration_sec": round(elapsed, 2),
                    "error": str(e),
                }
            )

    total_elapsed = time.perf_counter() - total_start

    out_file = _run_dir(doc_path) / f"{args.label}.json"
    out_file.write_text(
        json.dumps(
            {
                "label": args.label,
                "doc_path": str(doc_path),
                "doc_title": title,
                "doc_chars": len(content),
                "chunk_count": len(chunks),
                "base_url": base_url,
                "model_name": args.model_name,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "total_duration_sec": round(total_elapsed, 2),
                "chunks": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    ok = sum(1 for r in results if r["error"] is None)
    print(f"\n[{args.label}] 完成：{ok}/{len(results)} 成功，總時 {total_elapsed:.1f}s")
    print(f"[{args.label}] 已寫入：{out_file.relative_to(REPO_ROOT)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    doc_path = Path(args.doc).resolve()
    run_dir = _run_dir(doc_path)

    runs = sorted(run_dir.glob("*.json"))
    if len(runs) < 2:
        names = [r.name for r in runs]
        print(f"ERROR: 需要至少兩份 run JSON 才能比對，目前：{names}", file=sys.stderr)
        return 1

    datasets = [json.loads(r.read_text(encoding="utf-8")) for r in runs]

    # 確認 doc 一致
    doc_chars = {d["doc_chars"] for d in datasets}
    if len(doc_chars) > 1:
        print(f"WARN: run 之間 doc_chars 不一致 {doc_chars}，可能是不同文件")

    chunk_count = datasets[0]["chunk_count"]

    lines = []
    lines.append(f"# A/B Bench Report — {datasets[0]['doc_title']}\n")
    lines.append(f"- 文件：`{datasets[0]['doc_path']}`")
    lines.append(f"- 總字元：{datasets[0]['doc_chars']:,}")
    lines.append(f"- Chunk 數：{chunk_count}")
    lines.append(f"- 產出時間：{datetime.now().isoformat(timespec='seconds')}")
    lines.append("")

    # Summary stats table
    lines.append("## 速度 / 輸出量總表\n")
    lines.append("| Label | Model | 總時 (s) | 平均每 chunk (s) | 成功 | 失敗 | 輸出總字元 |")
    lines.append("|-------|-------|---------|-----------------|------|------|------------|")
    for d in datasets:
        ok = sum(1 for c in d["chunks"] if c["error"] is None)
        fail = len(d["chunks"]) - ok
        avg = d["total_duration_sec"] / max(len(d["chunks"]), 1)
        out_chars = sum(c["output_chars"] for c in d["chunks"])
        lines.append(
            f"| `{d['label']}` | `{d.get('model_name', 'n/a')}` | "
            f"{d['total_duration_sec']:.1f} | {avg:.1f} | {ok} | {fail} | {out_chars:,} |"
        )
    lines.append("")

    # Per-chunk timing
    lines.append("## 每 chunk 速度\n")
    header = "| # | 標題 |" + "".join(f" {d['label']} (s) |" for d in datasets)
    sep = "|---|------|" + "".join("-------|" for _ in datasets)
    lines.append(header)
    lines.append(sep)
    for i in range(chunk_count):
        row = [f"| {i + 1}", datasets[0]["chunks"][i]["heading"]]
        for d in datasets:
            if i < len(d["chunks"]):
                c = d["chunks"][i]
                v = f"{c['duration_sec']:.1f}" if c["error"] is None else "FAIL"
            else:
                v = "—"
            row.append(v)
        lines.append(" | ".join(row) + " |")
    lines.append("")

    # Side-by-side outputs
    lines.append("## 每 chunk 輸出並列\n")
    for i in range(chunk_count):
        heading = datasets[0]["chunks"][i]["heading"]
        lines.append(f"### Chunk {i + 1}：{heading}\n")
        for d in datasets:
            if i >= len(d["chunks"]):
                continue
            c = d["chunks"][i]
            lines.append(f"**{d['label']}** — {c['duration_sec']:.1f}s，{c['output_chars']} 字元\n")
            if c["error"]:
                lines.append(f"_ERROR: {c['error']}_\n")
            else:
                lines.append("```markdown")
                lines.append(c["output"] or "")
                lines.append("```\n")
        lines.append("")

    report_path = run_dir / "REPORT.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已寫入報告：{report_path.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B bench for Robin's local LLM Map step")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="對一份文件跑 summarize_chunk map step 並存 JSON")
    p_run.add_argument("doc", help="文件路徑（.md / .txt / .pdf）")
    p_run.add_argument("--label", required=True, help="輸出檔案標籤（如 gemma / qwen）")
    p_run.add_argument("--base-url", default="http://localhost:8080/v1", help="LLM server URL")
    p_run.add_argument("--model-name", default=None, help="API model 欄位（預設從 config）")
    p_run.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report", help="讀取已有的 run JSON，產出並列比對 markdown")
    p_rep.add_argument("doc", help="文件路徑（用來定位 data/ab_bench/<slug>/）")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
