"""Robin 的 Ingest Pipeline：來源 → Source Summary → Concept/Entity 更新。

ADR-011 textbook ingest v2：concept page 走 `shared.kb_writer.upsert_concept_page`
4-action dispatcher (create / update_merge / update_conflict / noop)；entity page
仍走 v1 schema（ADR-011 暫不 cover entity）。
"""

import json
import re
from datetime import date
from pathlib import Path

from shared import kb_writer
from shared.anthropic_client import set_current_agent
from shared.config import get_vault_path
from shared.llm import ask
from shared.log import get_logger, kb_log
from shared.memory import get_context, remember
from shared.obsidian_writer import (
    list_files,
    read_page,
    vault_path,
    write_page,
)
from shared.prompt_loader import load_prompt
from shared.schemas.kb import ConflictBlock
from shared.utils import extract_frontmatter, read_text, slugify

logger = get_logger("nakama.robin.ingest")


def _truncate_at_boundary(text: str, max_chars: int) -> str:
    """Truncate text at the last paragraph break before max_chars."""
    if len(text) <= max_chars:
        return text
    # Try to cut at a paragraph boundary (double newline)
    cut = text[:max_chars].rfind("\n\n")
    if cut > max_chars * 0.5:
        return text[:cut] + "\n\n[…內容過長，已截斷]"
    # Fallback: cut at last sentence-ending punctuation
    for sep in ("。", ".\n", ". ", "\n"):
        cut = text[:max_chars].rfind(sep)
        if cut > max_chars * 0.5:
            return text[: cut + len(sep)] + "\n\n[…內容過長，已截斷]"
    return text[:max_chars] + "\n\n[…內容過長，已截斷]"


def _build_robin_system_prompt() -> str:
    """組合 Robin 的 system prompt，注入跨 session 記憶。"""
    base = "你是 Robin，Nakama 團隊的考古學家，負責知識庫管理。"
    memory = get_context("robin", task="ingest")
    return f"{base}\n\n{memory}" if memory else base


def _concept_label(item: dict) -> str:
    """Display label for a concept action item (title fallback to slug)."""
    return item.get("title") or item.get("slug") or "?"


def _build_existing_concepts_blob(existing: dict[str, dict]) -> str:
    """Render existing concept pages into a prompt-friendly aggregator blob.

    Each entry: slug + domain + aliases + body excerpt (≤800 chars per page) so
    the LLM can detect dedup matches and content conflicts without needing the
    full vault dump in prompt.
    """
    if not existing:
        return "（無既有 concept）"
    lines: list[str] = []
    for slug, page in sorted(existing.items()):
        fm = page["frontmatter"]
        body = page["body"]
        aliases = fm.get("aliases") or []
        domain = fm.get("domain", "general")
        body_excerpt = body[:800] + ("...(truncated)" if len(body) > 800 else "")
        lines.append(
            f"### [[{slug}]]\n"
            f"- domain: {domain}\n"
            f"- aliases: {aliases}\n"
            f"- body excerpt:\n```\n{body_excerpt}\n```"
        )
    return "\n\n".join(lines)


class IngestPipeline:
    """處理單一來源的完整 ingest 流程。"""

    def ingest(
        self,
        raw_path: Path,
        source_type: str,
        user_guidance: str = "",
        interactive: bool = False,
        content_nature: str = "",
    ) -> None:
        """執行完整 ingest pipeline。"""
        # 讀取來源內容
        if raw_path.suffix.lower() == ".pdf":
            from shared.pdf_parser import parse_pdf

            # 研究型文件（論文/教科書/臨床指引）含大量表格，啟用 pdfplumber 精確表格提取
            _TABLE_NATURES = {"research", "textbook", "clinical_protocol"}
            with_tables = content_nature in _TABLE_NATURES
            content = parse_pdf(raw_path, with_tables=with_tables)
        else:
            content = read_text(raw_path)
        title = raw_path.stem
        author = ""

        # 嘗試從 frontmatter 提取 metadata
        if raw_path.suffix.lower() == ".md":
            fm, body = extract_frontmatter(content)
            title = fm.get("title", title)
            author = fm.get("author", "")
            content = body if body else content

        logger.info(f"Ingest: {title} (type={source_type}, nature={content_nature or 'default'})")

        # Step 1: 產出 Source Summary
        summary_body = self._generate_summary(
            content=content,
            title=title,
            author=author,
            source_type=source_type,
            content_nature=content_nature,
        )

        # Step 2: 互動式模式 — 印出 Summary，等待使用者引導
        if interactive:
            user_guidance = self._prompt_user_guidance(title, summary_body)

        # Step 3: 寫入 Source Summary 頁面
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
                "source_type": source_type,
                "content_nature": content_nature or "popular_science",
                "author": author,
                "confidence": "medium",
                "tags": [],
                "related_pages": [],
            },
            body=summary_body,
        )
        logger.info(f"已建立 Source Summary：{summary_path}")
        kb_log("robin", "ingest", f"建立 Source Summary: {slug}")

        # Step 4: 取得 Concept & Entity 候選清單
        plan = self._get_concept_plan(
            summary_body, summary_path, user_guidance, content_nature=content_nature
        )
        if not plan:
            return

        # Step 5: 互動式模式 — 讓使用者審核候選清單後再建頁
        if interactive:
            plan = self._review_plan_interactive(plan)

        # Step 6: 執行計畫（建立/更新頁面）
        self._execute_plan(plan, summary_path)

        # Step 7: 更新 index.md
        self._update_index(title, slug, source_type)

        # Step 8: 記錄事件到 Tier 3 記憶
        concepts = plan.get("concepts", [])
        entities = plan.get("entities", [])
        concept_create = [_concept_label(c) for c in concepts if c.get("action") == "create"]
        concept_merge = [_concept_label(c) for c in concepts if c.get("action") == "update_merge"]
        concept_conflict = [
            _concept_label(c) for c in concepts if c.get("action") == "update_conflict"
        ]
        entity_create = [e.get("title", "") for e in entities]
        remember(
            agent="robin",
            type="episodic",
            title=f"Ingest: {title}",
            content=(
                f"來源：{title}（{source_type}）\n"
                f"Summary：{summary_path}\n"
                f"新建 concept：{', '.join(concept_create) if concept_create else '無'}\n"
                f"merge 更新 concept：{', '.join(concept_merge) if concept_merge else '無'}\n"
                f"conflict 記錄 concept："
                f"{', '.join(concept_conflict) if concept_conflict else '無'}\n"
                f"新建 entity：{', '.join(entity_create) if entity_create else '無'}\n"
                f"引導方向：{user_guidance or '無'}"
            ),
            tags=["ingest", source_type, content_nature or "popular_science", slug],
            confidence="high",
            source=str(raw_path),
        )

    def _prompt_user_guidance(self, title: str, summary_body: str) -> str:
        """互動式模式：印出 Summary 並等待使用者輸入引導方向。"""
        print(f"\n{'=' * 60}")
        print(f"📝 Source Summary：{title}")
        print(f"{'=' * 60}")
        print(summary_body)
        print(f"\n{'=' * 60}")
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

    # 大文件閾值（超過此字元數啟用 Map-Reduce）
    LARGE_DOC_THRESHOLD = 30000

    def _generate_summary(
        self,
        content: str,
        title: str,
        author: str,
        source_type: str,
        content_nature: str = "",
    ) -> str:
        """產出 Source Summary。小文件直接用 facade，大文件走 Map-Reduce。

        facade 依 `MODEL_ROBIN` env 選 provider（預設 Gemini 2.5 Pro，見步驟 4）。
        """
        set_current_agent("robin")  # Web UI 也會呼叫此 method，重設 thread-local
        if len(content) <= self.LARGE_DOC_THRESHOLD:
            # 小文件：單次 facade 呼叫（provider 由 MODEL_ROBIN 決定）。
            # ADR-011 P2「不省 token、deep extract」— 這個分支 content 已經
            # 在 LARGE_DOC_THRESHOLD 之內，pass-through 不截斷；先前的
            # `_truncate_at_boundary(content, 30000)` 呼叫在此 branch 永遠是
            # no-op（max_chars == LARGE_DOC_THRESHOLD），但留著會誤導後人
            # 以為 ingest 會主動摺扣內容（A-10）。函式本身保留作為 future
            # opt-in utility（例如 retrieval-time pre-trim）。
            prompt = load_prompt(
                "robin",
                "summarize",
                content_nature=content_nature,
                title=title,
                author=author or "未知",
                source_type=source_type,
                date=str(date.today()),
                content=content,
            )
            return ask(prompt=prompt, system=_build_robin_system_prompt())

        # 大文件：Map-Reduce
        return self._map_reduce_summary(
            content=content,
            title=title,
            author=author or "未知",
            source_type=source_type,
            content_nature=content_nature,
        )

    def _map_reduce_summary(
        self,
        content: str,
        title: str,
        author: str,
        source_type: str,
        content_nature: str = "",
    ) -> str:
        """Map-Reduce 摘要：分段用本地模型，合併走 facade（MODEL_ROBIN）。"""
        set_current_agent("robin")
        from agents.robin.chunker import chunk_document

        chunks = chunk_document(content)
        logger.info(f"大文件 Map-Reduce：{len(chunks)} chunks，{len(content):,} 字元")

        # 決定 Map 階段使用的推理函式
        ask_fn = self._get_map_ask_fn()

        # Map：每個 chunk 獨立摘要（單一 chunk 失敗不中斷整個流程）
        system = _build_robin_system_prompt()
        chunk_summaries = []
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
            try:
                summary = ask_fn(prompt, system=system)
            except Exception as e:
                logger.error(f"  chunk {chunk['index']}/{len(chunks)} 失敗：{e}")
                summary = f"（此段落摘要失敗：{chunk['heading']}）"
            chunk_summaries.append(summary)
            logger.info(f"  chunk {chunk['index']}/{len(chunks)} 完成（{len(summary)} 字元）")

        # Reduce：合併所有 chunk 摘要（走 facade，provider 由 MODEL_ROBIN 決定）
        combined = "\n\n---\n\n".join(
            f"### 段落 {i + 1}：{chunks[i]['heading']}\n{s}" for i, s in enumerate(chunk_summaries)
        )

        reduce_prompt = load_prompt(
            "robin",
            "reduce_summary",
            content_nature=content_nature,
            title=title,
            author=author,
            source_type=source_type,
            total_chunks=str(len(chunks)),
            chunk_summaries=combined,
        )

        return ask(prompt=reduce_prompt, system=system)

    @staticmethod
    def _get_map_ask_fn():
        """取得 Map 階段的推理函式：優先本地模型，fallback 到 facade。

        facade（`shared.llm.ask`）依 `MODEL_ROBIN` env 自動選 provider — Robin
        預設走 Gemini（步驟 4）。沒設就回退到 DEFAULT_MODELS 的 Claude Sonnet。
        """
        try:
            from shared.local_llm import ask_local, is_server_available

            if is_server_available():
                logger.info("Map 階段使用本地 LLM")
                return ask_local
        except ImportError:
            pass

        logger.warning("本地 LLM 不可用，Map 階段改走雲端 facade（費用較高）")
        return ask

    def _get_concept_plan(
        self,
        summary_body: str,
        source_path: str,
        user_guidance: str = "",
        content_nature: str = "",
    ) -> dict | None:
        """呼叫 facade（依 MODEL_ROBIN）取得 v2 plan：{concepts, entities}。

        ADR-011 §3.3 Step 4：注入既有 concept page aliases + body 給 LLM 做 dedup
        + conflict detection；LLM 對每候選 concept 直接吐 4 種 action 之一。
        """
        set_current_agent("robin")
        existing_concepts = kb_writer.list_existing_concepts()
        existing_concepts_blob = _build_existing_concepts_blob(existing_concepts)
        existing_entity_stems = [f.stem for f in list_files("KB/Wiki/Entities")]
        existing_entities = ", ".join(existing_entity_stems) if existing_entity_stems else "（無）"

        prompt = load_prompt(
            "robin",
            "extract_concepts",
            content_nature=content_nature,
            existing_concepts_blob=existing_concepts_blob,
            existing_entities=existing_entities,
            summary=summary_body,
            user_guidance=user_guidance or "（無特別引導，請自行判斷重點）",
        )

        response = ask(
            prompt=prompt,
            system=_build_robin_system_prompt() + "\n\n回傳純 JSON，不要包含其他文字。",
            temperature=0.2,
        )

        try:
            json_match = re.search(r"\{[\s\S]*\}", response)
            if not json_match:
                logger.warning("未能從 Claude 回應中提取 JSON")
                return None
            return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失敗：{e}")
            return None

    def _review_plan_interactive(self, plan: dict) -> dict:
        """互動式模式：印出 v2 plan 候選清單，讓使用者逐一確認後回傳過濾後的計畫。

        Plan schema (ADR-011 §3.3 Step 4)：
            {
                "concepts": [{slug, action, title, ...}],
                "entities": [{title, entity_type, reason, content_notes}],
            }
        """
        concepts = plan.get("concepts", [])
        entities = plan.get("entities", [])

        if not concepts and not entities:
            print("Robin 判斷這份來源不需要新增或更新任何頁面。")
            return plan

        approved_concepts: list[dict] = []
        approved_entities: list[dict] = []

        if concepts:
            print(f"\n{'=' * 60}")
            print(f"💡 Robin 建議對 {len(concepts)} 個 Concept 動作：")
            print(f"{'=' * 60}")
            for i, item in enumerate(concepts, 1):
                action = item.get("action", "?")
                icon = {
                    "create": "🆕",
                    "update_merge": "🔀",
                    "update_conflict": "⚠️",
                    "noop": "🟢",
                }.get(action, "?")
                print(f"\n{i}. {icon} [{action.upper()}] {_concept_label(item)}")
                if item.get("reason"):
                    print(f"   理由：{item['reason']}")
                if item.get("conflict"):
                    c = item["conflict"]
                    print(
                        f"   衝突：{c.get('topic', '?')} — "
                        f"既有「{c.get('existing_claim', '')}」"
                        f" vs 新「{c.get('new_claim', '')}」"
                    )

            print(f"\n{'=' * 60}")
            print("輸入要執行的編號（逗號分隔），例如：1,3")
            print("輸入 all 全部執行，輸入 none 或直接 Enter 全部跳過")
            choice = input("你的選擇：").strip().lower()

            if choice == "all":
                approved_concepts = concepts
                print(f"✓ 全部 {len(concepts)} 個 concept action 將執行")
            elif choice and choice != "none":
                selected = {int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()}
                approved_concepts = [concepts[i] for i in sorted(selected) if i < len(concepts)]
                print(f"✓ 已選擇 {len(approved_concepts)} 個 concept action")
            else:
                print("✓ 跳過所有 concept action")

        if entities:
            print(f"\n{'=' * 60}")
            print(f"👤 Robin 建議新建以下 {len(entities)} 個 Entity：")
            print(f"{'=' * 60}")
            for i, item in enumerate(entities, 1):
                etype = item.get("entity_type", "other")
                print(f"\n{i}. [{etype.upper()}] {item['title']}")
                if item.get("reason"):
                    print(f"   理由：{item['reason']}")
                if item.get("content_notes"):
                    print(f"   內容重點：{item['content_notes'][:100]}...")

            print(f"\n{'=' * 60}")
            print("輸入要建立的編號（逗號分隔），all 全部，none/Enter 跳過")
            choice = input("你的選擇：").strip().lower()

            if choice == "all":
                approved_entities = entities
            elif choice and choice != "none":
                selected = {int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()}
                approved_entities = [entities[i] for i in sorted(selected) if i < len(entities)]

        print()
        return {"concepts": approved_concepts, "entities": approved_entities}

    def _execute_plan(self, plan: dict, source_path: str) -> None:
        """執行 v2 plan：concepts 走 kb_writer 4-action dispatcher；entities 沿用 v1。"""
        source_link = f"[[{Path(source_path).stem}]]"
        for concept in plan.get("concepts", []):
            self._execute_concept_action(concept, source_link)
        for entity in plan.get("entities", []):
            self._create_entity_page(entity, source_path)

    def _execute_concept_action(self, item: dict, source_link: str) -> None:
        """Dispatch 一個 concept action 到 kb_writer.upsert_concept_page。

        Plan item schema：{slug, action, title?, domain?, candidate_aliases?,
        extracted_body?, conflict?, reason?}
        """
        set_current_agent("robin")
        slug = item.get("slug") or slugify(item.get("title", ""))
        action = item.get("action", "create")
        if not slug:
            logger.warning(f"concept action missing slug/title: {item}")
            return
        if action not in ("create", "update_merge", "update_conflict", "noop"):
            logger.warning(f"unknown concept action {action!r} for slug {slug}")
            return

        # Only update_conflict consumes `conflict`; gate validation so a
        # defensively-populated partial conflict dict on a non-conflict action
        # does not silently drop the entire concept action (bug_020).
        conflict: ConflictBlock | None = None
        if action == "update_conflict":
            conflict_data = item.get("conflict")
            if conflict_data:
                try:
                    conflict = ConflictBlock(**conflict_data)
                except Exception as e:
                    logger.warning(f"invalid conflict block for {slug}: {e}")
                    return

        try:
            kb_writer.upsert_concept_page(
                slug=slug,
                action=action,
                source_link=source_link,
                title=item.get("title"),
                domain=item.get("domain"),
                aliases=item.get("candidate_aliases") or [],
                extracted_body=item.get("extracted_body"),
                conflict=conflict,
            )
            kb_log("robin", f"concept-{action}", f"[[{slug}]]")
        except Exception as e:
            logger.error(f"upsert_concept_page failed for {slug}: {e}")

    def _create_entity_page(self, item: dict, source_path: str) -> None:
        """Entity page 沿用 v1 schema (ADR-011 暫不 cover entity)."""
        set_current_agent("robin")
        title = item["title"]
        content_notes = item.get("content_notes", "")
        slug = slugify(title)

        prompt = load_prompt(
            "robin",
            "write_entity",
            title=title,
            entity_type=item.get("entity_type", "other"),
            content_notes=content_notes,
            source_refs=source_path,
        )
        body = ask(prompt=prompt, system=_build_robin_system_prompt())

        write_page(
            f"KB/Wiki/Entities/{slug}.md",
            frontmatter={
                "title": title,
                "type": "entity",
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
        logger.info(f"已建立 entity page：{slug}")
        kb_log("robin", "create-entity", f"建立 [[{title}]]")

    def _update_index(self, title: str, slug: str, source_type: str) -> None:
        """在 KB/index.md 中新增此來源的條目。"""
        index_content = read_page("KB/index.md") or ""

        entry = f"- [[{slug}]] — {source_type}：{title}\n"

        # 已有正確的 wikilink 格式，跳過
        if f"[[{slug}]]" in index_content:
            return

        # 有舊格式（plain path），自動修正為 wikilink
        plain_path = f"KB/Wiki/Sources/{slug}.md"
        if plain_path in index_content:
            # 找到整行並替換
            index_content = re.sub(
                rf"- {re.escape(plain_path)}[^\n]*\n?",
                entry,
                index_content,
            )
            target = vault_path("KB", "index.md")
            target.write_text(index_content, encoding="utf-8")
            logger.info(f"已修正 KB/index.md：{slug} 的連結格式")
            return

        # 全新條目：插入 Sources 區塊（相容中英文 heading）
        if "## Sources" in index_content:
            # 同時處理 "## Sources" 和 "## 來源（Sources）"
            index_content = re.sub(
                r"(## (?:來源（)?Sources(?:）)?)\n",
                rf"\1\n{entry}",
                index_content,
                count=1,
            )
        else:
            index_content += f"\n## Sources\n{entry}"

        target = vault_path("KB", "index.md")
        target.write_text(index_content, encoding="utf-8")
        logger.info(f"已更新 KB/index.md：加入 {slug}")
