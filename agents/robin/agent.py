"""Robin — Knowledge Base Agent（考古學家）。

掃描 Inbox/kb/ 中的新檔案，執行 Ingest pipeline：
1. 分類來源類型
2. 搬移到 KB/Raw/ 對應子資料夾
3. 產出 Source Summary → KB/Wiki/Sources/
4. 識別並更新/建立 Concept & Entity pages
5. 更新 KB/index.md + KB/log.md
6. 在 SQLite 標記已處理
"""

import shutil
from pathlib import Path

from agents.base import BaseAgent
from agents.robin.categories import CONTENT_NATURES, DEFAULT_CONTENT_NATURE
from agents.robin.ingest import IngestPipeline
from shared.config import get_agent_config, get_vault_path
from shared.log import kb_log
from shared.state import is_file_processed, mark_file_processed

# 副檔名 → Raw 子資料夾 對應（僅作預設推測，Web UI 可覆寫）
EXTENSION_TO_RAW_DIR: dict[str, str] = {
    ".pdf": "Papers",
    ".md": "Articles",
    ".txt": "Articles",
    ".html": "Articles",
    ".epub": "Books",
}

# 副檔名 → source type 對應（僅作預設推測，Web UI 可覆寫）
EXTENSION_TO_SOURCE_TYPE: dict[str, str] = {
    ".pdf": "paper",
    ".md": "article",
    ".txt": "article",
    ".html": "article",
    ".epub": "book",
}

# source type → Raw 子資料夾 對應（Web UI 手動選擇後使用）
SOURCE_TYPE_TO_RAW_DIR: dict[str, str] = {
    "article": "Articles",
    "paper": "Papers",
    "book": "Books",
    "video": "Videos",
    "podcast": "Podcasts",
}


class RobinAgent(BaseAgent):
    name = "robin"

    def __init__(self, interactive: bool = False) -> None:
        super().__init__()
        self.config = get_agent_config("robin")
        self.vault = get_vault_path()
        self.inbox = self.vault / self.config.get("inbox_path", "Inbox/kb")
        self.pipeline = IngestPipeline()
        self.interactive = interactive

    def run(self) -> str:
        """掃描 inbox，處理所有新檔案。"""
        if not self.inbox.exists():
            self.logger.info("Inbox 資料夾不存在，跳過")
            return "Inbox 不存在，無檔案處理"

        files = self._scan_inbox()
        if not files:
            self.logger.info("Inbox 無新檔案")
            return "無新檔案"

        processed = 0
        for file_path in files:
            try:
                self._process_file(file_path)
                processed += 1
            except Exception as e:
                self.logger.error(f"處理 {file_path.name} 失敗：{e}", exc_info=True)
                kb_log(self.name, "error", f"處理 {file_path.name} 失敗：{e}")

        summary = f"處理了 {processed}/{len(files)} 個檔案"
        return summary

    def _scan_inbox(self) -> list[Path]:
        """掃描 inbox 中未處理的檔案。"""
        supported = set(EXTENSION_TO_RAW_DIR.keys())
        all_files = [
            f for f in self.inbox.iterdir() if f.is_file() and f.suffix.lower() in supported
        ]

        new_files = [f for f in all_files if not is_file_processed(f, self.name)]

        self.logger.info(f"Inbox 共 {len(all_files)} 個檔案，{len(new_files)} 個待處理")
        return new_files

    def _process_file(self, file_path: Path) -> None:
        """處理單一檔案的完整 ingest 流程。"""
        self.logger.info(f"開始處理：{file_path.name}")

        # 1. 分類
        suffix = file_path.suffix.lower()
        raw_dir = EXTENSION_TO_RAW_DIR.get(suffix, "Articles")
        source_type = EXTENSION_TO_SOURCE_TYPE.get(suffix, "article")

        # 2. 搬移到 KB/Raw/
        raw_dest = self.vault / "KB" / "Raw" / raw_dir / file_path.name
        raw_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, raw_dest)
        self.logger.info(f"已複製到 KB/Raw/{raw_dir}/{file_path.name}")

        # 3. 取得使用者引導和內容性質（互動式模式）
        user_guidance = ""
        content_nature = DEFAULT_CONTENT_NATURE
        if self.interactive:
            user_guidance = self._get_user_guidance(file_path.name, source_type)
            content_nature = self._get_content_nature(file_path.name)

        # 4. 執行 ingest pipeline（摘要 → 概念/實體 → index/log）
        self.pipeline.ingest(
            raw_path=raw_dest,
            source_type=source_type,
            user_guidance=user_guidance,
            interactive=self.interactive,
            content_nature=content_nature,
        )

        # 5. 標記已處理，移除 inbox 中的原檔
        mark_file_processed(file_path, self.name)
        file_path.unlink()
        self.logger.info(f"已完成：{file_path.name}")

    def _get_user_guidance(self, filename: str, source_type: str) -> str:
        """互動式模式：等待使用者輸入引導方向。"""
        print(f"\n{'=' * 60}")
        print(f"📄 檔案：{filename}（{source_type}）")
        print(f"{'=' * 60}")
        print("Robin 即將產出 Source Summary，完成後會暫停等你閱讀。")
        print("你可以在閱讀後輸入引導方向，例如：")
        print('  "重點放在睡眠品質那部分"')
        print('  "作者的研究背景很重要"')
        print("  （直接按 Enter 讓 Robin 自行判斷）")
        print()
        return ""  # 引導在 summary 產出後才收集，此處回傳空字串

    @staticmethod
    def _get_content_nature(filename: str) -> str:
        """互動式模式：讓使用者選擇內容性質。"""
        print(f"\n{'=' * 60}")
        print(f"📋 請選擇 {filename} 的內容性質：")
        print(f"{'=' * 60}")
        options = list(CONTENT_NATURES.items())
        for i, (key, info) in enumerate(options, 1):
            print(f"  {i}. {info['label']}（{info['description']}）")
        print()
        choice = input("輸入編號（預設 1 = 科普讀物）：").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            selected = options[int(choice) - 1]
            print(f"✓ 已選擇：{selected[1]['label']}")
            return selected[0]
        print("✓ 使用預設：科普讀物")
        return DEFAULT_CONTENT_NATURE
