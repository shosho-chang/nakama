"""
Generate Brook style-training inventory: classify all shosho.tw posts into
three buckets (讀書心得 / 人物故事 / 科普文章) and emit a vault-friendly
Markdown checklist for 修修 to hand-pick training samples.

Inputs:  data/brook/shosho-posts.json (dumped by wp-cli on VPS)
Output:  <vault>/Projects/Brook 風格訓練.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
JSON_IN = REPO / "data" / "brook" / "shosho-posts.json"
VAULT_OUT = Path(r"F:\Shosho LifeOS\Projects\Brook 風格訓練.md")

CAT_BOOK = "book-review"
CAT_PEOPLE = {"people", "podcast"}
CAT_SCIENCE = {
    "neuroscience",
    "sport-science",
    "nutrition-science",
    "weight-loss-science",
    "sleep-science",
    "emotion-science",
    "longevity-science",
    "preventive-healthcare",
    "productivity-science",
}
CAT_NAMES_ZH = {
    "book-review": "讀書心得",
    "people": "人物專訪",
    "podcast": "Podcast",
    "neuroscience": "腦神經",
    "sport-science": "運動",
    "nutrition-science": "營養",
    "weight-loss-science": "減重",
    "sleep-science": "睡眠",
    "emotion-science": "情緒",
    "longevity-science": "長壽",
    "preventive-healthcare": "預防",
    "productivity-science": "生產力",
    "blog": "Blog",
}


def classify(cats: list[str]) -> str:
    if CAT_BOOK in cats:
        return "book"
    if any(c in CAT_PEOPLE for c in cats):
        return "people"
    if any(c in CAT_SCIENCE for c in cats):
        return "science"
    return "other"


def tags_line(cats: list[str]) -> str:
    labels = [CAT_NAMES_ZH.get(c, c) for c in cats if c != "blog"]
    return " · ".join(labels) if labels else "—"


def render_bucket(name: str, posts: list[dict]) -> str:
    lines = [f"## {name}（{len(posts)} 篇）", ""]
    for p in posts:
        title = p["title"]
        date = p["date"]
        url = p["url"]
        wc = p["word_count"]
        tags = tags_line(p["categories"])
        excerpt = (p["excerpt"] or "").strip().replace("\n", " ")[:80]
        lines.append(f"- [ ] [{title}]({url}) · `{date}` · {wc} 字 · {tags}")
        if excerpt:
            lines.append(f"  > {excerpt}…")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    posts = json.loads(JSON_IN.read_text(encoding="utf-8"))
    buckets: dict[str, list[dict]] = {"book": [], "people": [], "science": [], "other": []}
    for p in posts:
        buckets[classify(p["categories"])].append(p)

    total = len(posts)
    header = f"""---
type: agent-workspace
agent: brook
purpose: style-training-selection
created: 2026-04-22
source: shosho.tw WordPress (wp-cli dump)
total_posts: {total}
---

# Brook 風格訓練 — 文章挑選清單

**{total} 篇已發布文章，分三類**。在下方勾選要當作訓練材料的文章，
每類建議挑 **5-10 篇最能代表你該類寫作風格**的。勾完後告訴我，
我會抽取全文讓 Brook 做 style extraction。

## 說明

- 三類風格分開訓練：讀書心得 / 人物故事 / 科普文章
- 未分類的「其他」不會用於訓練
- 每篇顯示：標題 · 日期 · 字數 · 額外 tag · 摘錄

---

"""
    body = "\n".join(
        [
            render_bucket("📖 讀書心得", buckets["book"]),
            render_bucket("🎙️ 人物故事（Podcast / 人物專訪）", buckets["people"]),
            render_bucket("🧪 科普文章", buckets["science"]),
            render_bucket("📂 其他（不納入訓練）", buckets["other"]),
        ]
    )

    VAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    VAULT_OUT.write_text(header + body, encoding="utf-8")

    print(f"Wrote {VAULT_OUT}")
    print(f"  讀書心得: {len(buckets['book'])}")
    print(f"  人物故事: {len(buckets['people'])}")
    print(f"  科普文章: {len(buckets['science'])}")
    print(f"  其他:     {len(buckets['other'])}")
    print(f"  合計:     {total}")


if __name__ == "__main__":
    main()
