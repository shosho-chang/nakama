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
from agents.robin.ingest import IngestPipeline
from shared.config import get_agent_config, get_vault_path
from shared.log import kb_log
from shared.state import is_file_processed, mark_file_processed


# 副檔名 → Raw 子資料夾 對應
EXTENSION_TO_RAW_DIR: dict[str, str] = {
    ".pdf": "Papers",
    ".md": "Articles",
    ".txt": "Articles",
    ".html": "Articles",
    ".epub": "Books",
}

# 副檔名 → source type 對應
EXTENSION_TO_SOURCE_TYPE: dict[str, str] = {
    ".pdf": "paper",
    ".md": "article",
    ".txt": "article",
    ".html": "article",
    ".epub": "book",
}


class RobinAgent(BaseAgent):
    name = "robin"

    def __init__(self) -> None:
        super().__init__()
        self.config = get_agent_config("robin")
        self.vault = get_vault_path()
        self.inbox = self.vault / self.config.get("inbox_path", "Inbox/kb")
        self.pipeline = IngestPipeline()

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
            f for f in self.inbox.iterdir()
            if f.is_file() and f.suffix.lower() in supported
        ]

        new_files = [
            f for f in all_files
            if not is_file_processed(f, self.name)
        ]

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

        # 3-5. 執行 ingest pipeline（摘要 → 概念/實體 → index/log）
        self.pipeline.ingest(
            raw_path=raw_dest,
            source_type=source_type,
        )

        # 6. 標記已處理，移除 inbox 中的原檔
        mark_file_processed(file_path, self.name)
        file_path.unlink()
        self.logger.info(f"已完成：{file_path.name}")
