"""下載 Markdown 中的外部圖片到 vault/Files/，並將連結改為 Obsidian wikilink。"""

import hashlib
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from shared.log import get_logger
from shared.obsidian_writer import vault_path

logger = get_logger("nakama.robin.images")

# 匹配標準 Markdown 圖片語法：![alt](https://...)
_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")

# 常見圖片副檔名
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".bmp"}


def fetch_images(file_path: Path) -> int:
    """下載 file_path 中所有外部圖片，存到 vault/Files/，更新連結為 ![[filename]]。

    Returns:
        成功下載的圖片數量
    """
    content = file_path.read_text(encoding="utf-8")
    matches = _IMG_PATTERN.findall(content)
    if not matches:
        return 0

    files_dir = vault_path() / "Files"
    files_dir.mkdir(exist_ok=True)

    count = 0

    def _replace(match: re.Match) -> str:
        nonlocal count
        _alt = match.group(1)
        url = match.group(2)

        # 從 URL 推斷副檔名
        parsed = urlparse(url)
        url_path = Path(parsed.path)
        ext = url_path.suffix.lower()
        if ext not in _IMAGE_EXTS:
            ext = ".jpg"

        # 用 URL hash 避免重名
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        stem = url_path.stem[:40].strip(".") or "image"
        filename = f"{stem}-{url_hash}{ext}"
        dest = files_dir / filename

        if not dest.exists():
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; Robin/1.0)"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    dest.write_bytes(resp.read())
                logger.info(f"已下載圖片：{url} → Files/{filename}")
                count += 1
            except Exception as e:
                logger.warning(f"圖片下載失敗（保留原連結）：{url} — {e}")
                return match.group(0)  # 失敗時保留原始連結

        # 改為 Obsidian wikilink 格式（Obsidian 可從 vault 自動解析）
        return f"![[{filename}]]"

    new_content = _IMG_PATTERN.sub(_replace, content)
    if new_content != content:
        file_path.write_text(new_content, encoding="utf-8")
        logger.info(f"已更新圖片連結：{file_path.name}，共 {count} 張")

    return count
