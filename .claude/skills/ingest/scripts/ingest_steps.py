"""ingest skill — route C 兩步 CLI（plan / execute），給對話式 HITL 用。

這是 `ingest` skill 專屬的薄 orchestration script（住 skill 的 scripts/）。
它**不重寫 pipeline**——只是把 `agents.robin.ingest.IngestPipeline` 的既有步驟
拆成「出計畫 / 執行」兩次呼叫，讓 Claude 能在中間停下來、把 concept/entity 計畫
列給修修做 accept/defer/exclude（pipeline 內建的 interactive 模式用終端 input()，
對話驅動不了，故走這條兩步拆法）。

呼叫的是 IngestPipeline 既有方法，與 Web UI 後端
(`thousand_sunny/routers/robin.py` 的 summarizing→planning→executing 步驟) 同源。
兩處若 pipeline 私有方法簽名變動須一起更新（GOTCHAS 有記）。

用法（從 repo root 跑）：
    python .claude/skills/ingest/scripts/ingest_steps.py plan \
        --raw "<path to article>" --source-type article \
        [--annotation-slug <slug>] [--content-nature popular_science] \
        [--guidance "..."] --out plan.json

    # 修修在對話裡 accept/defer/exclude 後，Claude 把過濾過的 plan 寫回 plan.json，再：
    python .claude/skills/ingest/scripts/ingest_steps.py execute --plan-file plan.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# repo root 上 sys.path。檔在 <root>/.claude/skills/ingest/scripts/ingest_steps.py：
# parents[0]=scripts, [1]=ingest, [2]=skills, [3]=.claude, [4]=repo root。
# 從 repo root 跑（python .claude/skills/ingest/scripts/ingest_steps.py ...）時正確。
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.robin.ingest import IngestPipeline  # noqa: E402
from shared.config import get_vault_path  # noqa: E402
from shared.log import kb_log  # noqa: E402
from shared.obsidian_writer import write_page  # noqa: E402
from shared.utils import extract_frontmatter, read_text, slugify  # noqa: E402


def _phase_plan(args: argparse.Namespace) -> None:
    """Phase 1（Literature，若有 annotation）+ 摘要 + Source 頁 + concept/entity 計畫。

    寫入 vault 的只有 Source Summary 頁（draft/candidate，author=agent_robin）——
    這與 ingest()/Web UI 一致（Source 摘要在 plan 審查前就寫，審查只針對 concept/entity）。
    **不寫任何 Concept/Entity**（那要 execute 階段、修修點頭後才寫）。
    """
    raw_path = Path(args.raw).expanduser()
    if not raw_path.exists():
        sys.exit(f"找不到檔案：{raw_path}")

    pipeline = IngestPipeline()

    content = read_text(raw_path)
    title = raw_path.stem
    author = ""
    if raw_path.suffix.lower() == ".md":
        fm, body = extract_frontmatter(content)
        title = fm.get("title", title)
        author = fm.get("author", "")
        content = body if body else content

    # Phase 1（route C）：render 人讀 Literature Note（idempotent；找不到 annotation
    # set 不中斷，pipeline 內部 log）。劃線是「強調訊號」，全文照樣 ingest（紅線 D-A）。
    if args.annotation_slug:
        pipeline._render_literature(args.annotation_slug, args.source_type)

    # Step 1：產 Source Summary（吃整份全文；>30000 字 pipeline 自動 map-reduce）
    summary_body = pipeline._generate_summary(
        content=content,
        title=title,
        author=author,
        source_type=args.source_type,
        content_nature=args.content_nature,
    )

    # Step 2：寫 Source Summary 頁（draft）。frontmatter 比照 ingest()／Web UI。
    slug = slugify(title)
    summary_path = f"KB/Wiki/Sources/{slug}.md"
    try:
        raw_relative = str(raw_path.relative_to(get_vault_path()))
    except ValueError:
        raw_relative = str(raw_path)
    write_page(
        summary_path,
        frontmatter={
            "title": title,
            "type": "source",
            "status": "draft",
            "created": str(date.today()),
            "updated": str(date.today()),
            "source_refs": [raw_relative],
            "source_type": args.source_type,
            "content_nature": args.content_nature or "popular_science",
            "author": "agent_robin",  # AI 綜整摘要（紅線 3：provenance 分離）
            "original_author": author,
            "confidence": "medium",
            "tags": [],
            "related_pages": [],
        },
        body=summary_body,
    )
    kb_log("robin", "ingest", f"建立 Source Summary: {slug}")

    # Step 3：concept/entity 計畫（吃 summary_body，非全文——pipeline 的 D-A 事實）
    plan = pipeline._get_concept_plan(
        summary_body, summary_path, args.guidance, content_nature=args.content_nature
    ) or {"concepts": [], "entities": []}

    out = {
        "title": title,
        "slug": slug,
        "source_type": args.source_type,
        "content_nature": args.content_nature or "popular_science",
        "summary_path": summary_path,
        "summary_excerpt": summary_body[:1200],
        "plan": plan,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    n_c = len(plan.get("concepts", []))
    n_e = len(plan.get("entities", []))
    print(f"PLAN_READY out={args.out} concepts={n_c} entities={n_e} summary={summary_path}")


def _phase_execute(args: argparse.Namespace) -> None:
    """執行修修過濾後的 plan：寫 Concept/Entity + 更新 index。

    plan-file 是 plan 階段輸出、經修修 accept/defer/exclude 過濾後的 JSON
    （只留要執行的 concepts/entities）。defer/exclude 的項目 Claude 在寫回前已移除。
    """
    data = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    plan = data.get("plan", {"concepts": [], "entities": []})
    summary_path = data["summary_path"]

    pipeline = IngestPipeline()
    pipeline._execute_plan(plan, summary_path)
    pipeline._update_index(data["title"], data["slug"], data["source_type"])

    concepts = plan.get("concepts", [])
    entities = plan.get("entities", [])
    writes = sum(
        1 for c in concepts if c.get("action") in ("create", "update_merge", "update_conflict")
    ) + len(entities)
    noop = sum(1 for c in concepts if c.get("action") == "noop")
    print(f"EXECUTED writes={writes} noop={noop} index={summary_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ingest skill route C 兩步 CLI")
    sub = parser.add_subparsers(dest="phase", required=True)

    p = sub.add_parser("plan", help="出 Source 摘要 + concept/entity 計畫（不寫 Concept/Entity）")
    p.add_argument("--raw", required=True, help="原始文章檔路徑")
    p.add_argument("--source-type", default="article", choices=["article", "paper"])
    p.add_argument("--annotation-slug", default=None, help="annotation set slug（Phase 1 Literature）")
    p.add_argument("--content-nature", default="popular_science")
    p.add_argument("--guidance", default="", help="修修對抽取方向的引導（可空）")
    p.add_argument("--out", required=True, help="計畫 JSON 輸出路徑")
    p.set_defaults(func=_phase_plan)

    e = sub.add_parser("execute", help="執行過濾後的 plan（寫 Concept/Entity + index）")
    e.add_argument("--plan-file", required=True, help="過濾後的計畫 JSON")
    e.set_defaults(func=_phase_execute)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
