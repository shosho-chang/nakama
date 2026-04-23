"""掃 Markdown 裡的圖片連結並下載到本地，回傳 rewrite 後的 Markdown。

使用方式：
    from pathlib import Path
    from shared.image_fetcher import download_markdown_images

    rewritten_md, saved = download_markdown_images(
        md_text,
        dest_dir=Path("/vault/KB/Attachments/pubmed/42020128"),
        vault_relative_prefix="KB/Attachments/pubmed/42020128",
        base_url="https://bmjopen.bmj.com/content/16/4/e116911",
    )

失敗的圖片保留 remote URL（不 rewrite）；單張下載失敗不中止整體流程。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from shared.log import get_logger

logger = get_logger("nakama.shared.image_fetcher")

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB 硬上限
_DEFAULT_TIMEOUT = 15.0

_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((\S+?)\)")

_CT_TO_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    "image/avif": "avif",
}


def download_markdown_images(
    md_text: str,
    *,
    dest_dir: Path,
    vault_relative_prefix: str,
    base_url: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT,
    user_agent: str = "nakama-robin/1.0 (+https://github.com/shosho-chang/nakama)",
) -> tuple[str, list[str]]:
    """掃 md 裡 ``![alt](url)`` 圖片、下載到 ``dest_dir``、rewrite URL 為 vault-relative。

    Args:
        md_text: 原始 Markdown 文字
        dest_dir: 圖片實際寫入的絕對路徑（會自動 mkdir）
        vault_relative_prefix: rewrite 後 md 裡使用的前綴，例如
            ``"KB/Attachments/pubmed/42020128"``
        base_url: 若 md 裡出現相對 URL，用此 base 做 urljoin
        timeout: 單張圖下載 timeout（秒）
        user_agent: HTTP User-Agent

    Returns:
        ``(rewritten_md, saved_relative_paths)``：rewrite 後的 markdown、
        以及實際下載成功並寫入 vault 的相對路徑列表（相對於 vault 根）。
    """
    matches = list(_MD_IMAGE_RE.finditer(md_text))
    if not matches:
        return md_text, []

    dest_dir.mkdir(parents=True, exist_ok=True)
    url_to_relpath: dict[str, str] = {}
    saved: list[str] = []

    headers = {"User-Agent": user_agent}

    # 先蒐集所有絕對 URL，按出現順序配 index
    unique_urls: list[str] = []
    for m in matches:
        raw_url = m.group(2).strip()
        abs_url = _resolve_url(raw_url, base_url)
        if abs_url and abs_url not in url_to_relpath and abs_url not in unique_urls:
            unique_urls.append(abs_url)

    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for idx, abs_url in enumerate(unique_urls, start=1):
            result = _download_one(client, abs_url, dest_dir, idx)
            if result is None:
                continue
            ext, filename = result
            rel_in_vault = f"{vault_relative_prefix.rstrip('/')}/{filename}"
            url_to_relpath[abs_url] = rel_in_vault
            saved.append(rel_in_vault)

    # 再 rewrite md：對每個 match，看它的 absolute URL 在 dict 裡有沒有對應
    def _sub(match: re.Match[str]) -> str:
        alt = match.group(1)
        raw_url = match.group(2).strip()
        abs_url = _resolve_url(raw_url, base_url)
        if abs_url and abs_url in url_to_relpath:
            return f"![{alt}]({url_to_relpath[abs_url]})"
        return match.group(0)

    rewritten = _MD_IMAGE_RE.sub(_sub, md_text)
    return rewritten, saved


def _resolve_url(raw_url: str, base_url: Optional[str]) -> Optional[str]:
    """把 raw_url 轉成 absolute http(s) URL；無法轉則回 None。"""
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    if parsed.scheme in ("http", "https"):
        return raw_url
    if base_url and (parsed.scheme == "" or raw_url.startswith("/")):
        joined = urljoin(base_url, raw_url)
        if urlparse(joined).scheme in ("http", "https"):
            return joined
    return None


def _download_one(
    client: httpx.Client,
    url: str,
    dest_dir: Path,
    idx: int,
) -> Optional[tuple[str, str]]:
    """下載單張圖；成功回 (ext, filename)，失敗回 None（log warning）。"""
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                logger.warning(f"[image_fetcher] {url} → HTTP {r.status_code}")
                return None
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            ext = _extension_for(ctype, url)
            if ext is None:
                logger.warning(f"[image_fetcher] {url} 回傳 content-type={ctype}，非 image，跳過")
                return None
            filename = f"img-{idx}.{ext}"
            dest_path = dest_dir / filename
            total = 0
            with open(dest_path, "wb") as f:
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        logger.warning(
                            f"[image_fetcher] {url} 超過 {_MAX_IMAGE_BYTES} bytes 上限，中止"
                        )
                        f.close()
                        dest_path.unlink(missing_ok=True)
                        return None
                    f.write(chunk)
            return ext, filename
    except httpx.HTTPError as e:
        logger.warning(f"[image_fetcher] {url} 下載失敗：{e}")
        return None


def _extension_for(content_type: str, url: str) -> Optional[str]:
    """由 content-type 決定副檔名，失敗時 fallback URL path basename。

    回 None 表示判定不是 image。
    """
    if content_type in _CT_TO_EXT:
        return _CT_TO_EXT[content_type]
    if content_type.startswith("image/"):
        # 未知 image/* 子型別
        return content_type.split("/", 1)[1].split("+", 1)[0]
    # content-type 不是 image/* 時，最後試 URL path 副檔名（某些 CDN 回 octet-stream）
    path_ext = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if path_ext in {"jpg", "jpeg", "png", "webp", "gif", "svg", "tiff", "bmp", "avif"}:
        return "jpg" if path_ext == "jpeg" else path_ext
    return None
