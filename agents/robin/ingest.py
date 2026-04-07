"""Robin 的 Ingest Pipeline：來源 → Source Summary → Concept/Entity 更新。"""

import json
import re
from datetime import date
from pathlib import Path

from shared.anthropic_client import ask_claude
from shared.log import get_logger, kb_log
from shared.obsidian_writer import (
    list_files,
    read_page,
    vault_path,
    write_page,
)
from shared.utils import extract_frontmatter, read_text, slugify

logger = get_logger("nakama.robin.ingest")

_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


class IngestPipeline:
    """處理單一來源的完整 ingest 流程。"""

    def ingest(
        self,
        raw_path: Path,
        source_type: str,
        user_guidance: str = "",
        interactive: bool = False,
    ) -> None:
        """執行完整 ingest pipeline。"""
        # 讀取來源內容
        content = read_text(raw_path)
        title = raw_path.stem
        author = ""

        # 嘗試從 frontmatter 提取 metadata
        if raw_path.suffix == ".md":
            fm, body = extract_frontmatter(content)
            title = fm.get("title", title)
            author = fm.get("author", "")
            content = body if body else content

        logger.info(f"Ingest: {title} (type={source_type})")

        # Step 1: 產出 Source Summary
        summary_body = self._generate_summary(
            content=content,
            title=title,
            author=author,
            source_type=source_type,
        )

        # Step 2: 互動式模式 — 印出 Summary，等待使用者引導
        if interactive:
            user_guidance = self._prompt_user_guidance(title, summary_body)

        # Step 3: 寫入 Source Summary 頁面
        slug = slugify(title)
        summary_path = f"KB/Wiki/Sources/{slug}.md"
        raw_relative = str(raw_path.relative_to(vault_path().parent.parent))

        write_page(
            summary_path,
            frontmatter={
                "title": title,
                "type": "source",
                "status": "draft",
                "created": str(date.today()),
                "updated": str(date.today()),
                "source_refs": [raw_relative],
                "source_type": source_type,
                "author": author,
                "confidence": "medium",
                "tags": [],
                "related_pages": [],
            },
            body=summary_body,
        )
        logger.info(f"已建立 Source Summary：{summary_path}")
        kb_log("robin", "ingest", f"建立 Source Summary: {slug}")

        # Step 4: 識別需要建立/更新的 Concept & Entity pages
        self._process_concepts_and_entities(summary_body, summary_path, user_guidance)

        # Step 5: 更新 index.md
        self._update_index(title, slug, source_type)

    def _prompt_user_guidance(self, title: str, summary_body: str) -> str:
        """互動式模式：印出 Summary 並等待使用者輸入引導方向。"""
        print(f"\n{'='*60}")
        print(f"📝 Source Summary：{title}")
        print(f"{'='*60}")
        print(summary_body)
        print(f"\n{'='*60}")
        print("Robin 即將根據以上 Summary 建立 Concept 和 Entity 頁面。")
        print()
        print("你有想要特別強調的方向嗎？例如：")
        print('  "重點放在 CBT-I 療法的部分"')
        print('  "作者 Colleen Carney 的研究背景很重要"')
        print('  "失眠和焦慮的關係要獨立成一頁"')
        print()
        guidance = input("引導方向（直接按 Enter 讓 Robin 自行判斷）：").strip()
        print()
        if guidance:
            print(f"✓ 已收到引導：{guidance}")
        else:
            print("✓ Robin 將自行判斷重點")
        print()
        return guidance

    def _generate_summary(
        self,
        content: str,
        title: str,
        author: str,
        source_type: str,
    ) -> str:
        """呼叫 Claude 產出 Source Summary。"""
        prompt_template = _load_prompt("summarize.md")
        prompt = prompt_template.format(
            title=title,
            author=author or "未知",
            source_type=source_type,
            date=str(date.today()),
            content=content[:30000],  # 限制輸入長度
        )

        return ask_claude(prompt, system="你是 Robin，Nakama 團隊的考古學家，負責知識庫管理。")

    def _process_concepts_and_entities(
        self, summary_body: str, source_path: str, user_guidance: str = ""
    ) -> None:
        """根據 summary 識別並建立/更新 concept & entity pages。"""
        # 收集既有頁面清單
        existing_concepts = [
            f.stem for f in list_files("KB/Wiki/Concepts")
        ]
        existing_entities = [
            f.stem for f in list_files("KB/Wiki/Entities")
        ]

        existing_pages = (
            "概念頁：" + ", ".join(existing_concepts) if existing_concepts else "概念頁：（無）"
        ) + "\n" + (
            "實體頁：" + ", ".join(existing_entities) if existing_entities else "實體頁：（無）"
        )

        prompt_template = _load_prompt("extract_concepts.md")
        prompt = prompt_template.format(
            existing_pages=existing_pages,
            summary=summary_body,
            user_guidance=user_guidance or "（無特別引導，請自行判斷重點）",
        )

        response = ask_claude(
            prompt,
            system="你是 Robin，Nakama 團隊的考古學家。回傳純 JSON，不要包含其他文字。",
            temperature=0.2,
        )

        # 解析 JSON 回應
        try:
            # 提取 JSON block（可能被 ```json ... ``` 包裹）
            json_match = re.search(r"\{[\s\S]*\}", response)
            if not json_match:
                logger.warning("未能從 Claude 回應中提取 JSON")
                return
            plan = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失敗：{e}")
            return

        # 建立新頁面
        for item in plan.get("create", []):
            self._create_wiki_page(item, source_path)

        # 更新既有頁面
        for item in plan.get("update", []):
            self._update_wiki_page(item, source_path)

    def _create_wiki_page(self, item: dict, source_path: str) -> None:
        """建立一個新的 concept 或 entity 頁面。"""
        title = item["title"]
        page_type = item.get("type", "concept")
        content_notes = item.get("content_notes", "")
        slug = slugify(title)

        if page_type == "concept":
            prompt_template = _load_prompt("write_concept.md")
            prompt = prompt_template.format(
                title=title,
                content_notes=content_notes,
                source_refs=source_path,
            )
            wiki_dir = "KB/Wiki/Concepts"
        else:
            prompt_template = _load_prompt("write_entity.md")
            prompt = prompt_template.format(
                title=title,
                entity_type=item.get("entity_type", "other"),
                content_notes=content_notes,
                source_refs=source_path,
            )
            wiki_dir = "KB/Wiki/Entities"

        body = ask_claude(prompt, system="你是 Robin，Nakama 團隊的考古學家。")

        write_page(
            f"{wiki_dir}/{slug}.md",
            frontmatter={
                "title": title,
                "type": page_type,
                "status": "draft",
                "created": str(date.today()),
                "updated": str(date.today()),
                "source_refs": [source_path],
                "confidence": "medium",
                "tags": [],
                "related_pages": [],
            },
            body=body,
        )
        logger.info(f"已建立 {page_type} page：{slug}")
        kb_log("robin", f"create-{page_type}", f"建立 [[{title}]]")

    def _update_wiki_page(self, item: dict, source_path: str) -> None:
        """更新一個既有的 wiki 頁面，加入新來源的資訊。"""
        file_path = item.get("file", "")
        additions = item.get("additions", "")
        title = item.get("title", "")

        if not file_path:
            # 嘗試根據 title 找到檔案
            slug = slugify(title)
            for wiki_dir in ("KB/Wiki/Concepts", "KB/Wiki/Entities"):
                candidate = f"{wiki_dir}/{slug}.md"
                if read_page(candidate) is not None:
                    file_path = candidate
                    break

        if not file_path:
            logger.warning(f"找不到要更新的頁面：{title}")
            return

        existing_content = read_page(file_path)
        if existing_content is None:
            logger.warning(f"頁面不存在：{file_path}")
            return

        fm, body = extract_frontmatter(existing_content)

        # 將新來源加入 source_refs
        refs = fm.get("source_refs", [])
        if source_path not in refs:
            refs.append(source_path)
        fm["source_refs"] = refs

        # 在頁面末尾加入新資訊
        update_section = f"\n\n---\n\n## 更新（{date.today()}）\n\n{additions}\n\n來源：[[{Path(source_path).stem}]]\n"
        body += update_section

        write_page(file_path, frontmatter=fm, body=body)
        logger.info(f"已更新頁面：{file_path}")
        kb_log("robin", "update", f"更新 [[{title}]]，新增來源資訊")

    def _update_index(self, title: str, slug: str, source_type: str) -> None:
        """在 KB/index.md 中新增此來源的條目。"""
        index_content = read_page("KB/index.md") or ""

        # 檢查是否已存在
        if slug in index_content:
            return

        entry = f"- [[{slug}]] — {source_type}：{title}\n"

        # 找到合適的位置插入（在 Sources 區塊下）
        if "## Sources" in index_content:
            index_content = index_content.replace(
                "## Sources\n",
                f"## Sources\n{entry}",
            )
        else:
            index_content += f"\n## Sources\n{entry}"

        # 直接寫入（index.md 不需要 frontmatter 格式）
        target = vault_path("KB", "index.md")
        target.write_text(index_content, encoding="utf-8")
        logger.info(f"已更新 KB/index.md：加入 {slug}")
