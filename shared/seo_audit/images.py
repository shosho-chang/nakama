"""Images deterministic checks（I1-I5）。

Slice D.1 §附錄 A 5 條 rule：
    I1 所有 img 有非空 alt
    I2 alt 長度 < 125 字符
    I3 featured image / og:image accessible（HEAD 200 + image content-type）
    I4 圖片 lazy loading 覆蓋率（首屏外 ≥ 80%）
    I5 WebP/AVIF modern format 比例 ≥ 50%

`base_url` 用來解析相對 URL；I3/I5 會 issue HEAD request 驗 content-type，因此
本 module 帶 network call（與 metadata.py 不同）。

I3/I5 的 HEAD network call 失敗或 timeout 不 raise，僅記錄並 degrade 為
status=skip（防 audit pipeline 因外部 image CDN 失敗而整個爛）。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from shared.log import get_logger
from shared.seo_audit.types import AuditCheck

logger = get_logger("nakama.seo_audit.images")

_ALT_MAX = 125
_HEAD_TIMEOUT = 8.0
_FIRST_VIEWPORT_IMAGES = 3  # 視為「首屏」的前 N 張 img；可不嚴謹（lazy 政策有彈性）
_LAZY_PASS_RATIO = 0.80  # 首屏外 lazy 覆蓋率
_WEBP_AVIF_PASS_RATIO = 0.50


def check_images(soup: BeautifulSoup, base_url: str) -> list[AuditCheck]:
    """跑 I1-I5 共 5 條 image check。"""
    return [
        _check_alt_present(soup),
        _check_alt_length(soup),
        _check_og_image_accessible(soup, base_url),
        _check_lazy_loading(soup),
        _check_modern_format(soup, base_url),
    ]


def _all_imgs(soup: BeautifulSoup):
    """body 內 <img>（排除 noscript wrapper）。"""
    return soup.find_all("img")


# ── I1: alt 非空 ──


def _check_alt_present(soup: BeautifulSoup) -> AuditCheck:
    imgs = _all_imgs(soup)
    if not imgs:
        return AuditCheck(
            rule_id="I1",
            name="所有 img 有非空 alt",
            category="images",
            severity="warning",
            status="skip",
            actual="頁面無 <img>",
            expected="N/A",
            fix_suggestion="",
        )
    missing = [i for i in imgs if not (i.get("alt") or "").strip()]
    if not missing:
        return AuditCheck(
            rule_id="I1",
            name="所有 img 有非空 alt",
            category="images",
            severity="warning",
            status="pass",
            actual=f"全 {len(imgs)} 張 img 有 alt",
            expected="0 張缺 alt",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="I1",
        name="所有 img 有非空 alt",
        category="images",
        severity="warning",
        status="warn",
        actual=f"{len(missing)}/{len(imgs)} 張缺 alt",
        expected="0 張缺 alt",
        fix_suggestion="補 alt 描述（含 focus keyword 自然出現）",
        details={"missing_srcs": [i.get("src", "") for i in missing[:5]]},
    )


# ── I2: alt 長度 < 125 ──


def _check_alt_length(soup: BeautifulSoup) -> AuditCheck:
    imgs = _all_imgs(soup)
    long_alts = [(i.get("alt") or "") for i in imgs if len(i.get("alt") or "") >= _ALT_MAX]
    if not imgs:
        return AuditCheck(
            rule_id="I2",
            name=f"alt 長度 < {_ALT_MAX} 字符",
            category="images",
            severity="info",
            status="skip",
            actual="頁面無 <img>",
            expected="N/A",
            fix_suggestion="",
        )
    if not long_alts:
        return AuditCheck(
            rule_id="I2",
            name=f"alt 長度 < {_ALT_MAX} 字符",
            category="images",
            severity="info",
            status="pass",
            actual=f"全 {len(imgs)} 張 alt 在 {_ALT_MAX} 字內",
            expected=f"全部 < {_ALT_MAX}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="I2",
        name=f"alt 長度 < {_ALT_MAX} 字符",
        category="images",
        severity="info",
        status="warn",
        actual=f"{len(long_alts)} 張 alt ≥ {_ALT_MAX} 字",
        expected=f"全部 < {_ALT_MAX}",
        fix_suggestion="截短 alt，重點前置（不要塞滿關鍵字）",
        details={"long_alt_lengths": [len(a) for a in long_alts]},
    )


# ── I3: og:image accessible ──


def _head_image(url: str) -> tuple[bool, int, str]:
    """HEAD request 驗 image URL；網路錯誤回 (False, 0, "<error>")。"""
    try:
        r = httpx.head(url, timeout=_HEAD_TIMEOUT, follow_redirects=True)
        return (
            200 <= r.status_code < 300,
            r.status_code,
            r.headers.get("content-type", ""),
        )
    except httpx.RequestError as e:
        logger.warning("image_head_neterr url=%s err=%s", url, e)
        return (False, 0, f"err: {type(e).__name__}")


def _check_og_image_accessible(soup: BeautifulSoup, base_url: str) -> AuditCheck:
    og_image_tag = soup.find("meta", property=re.compile(r"^og:image$", re.I))
    og_image = (og_image_tag.get("content") if og_image_tag else "") or ""
    if not og_image:
        return AuditCheck(
            rule_id="I3",
            name="og:image accessible（HEAD 200 + image type）",
            category="images",
            severity="warning",
            status="skip",
            actual="缺 og:image（先補 O2）",
            expected="N/A 直到 og:image 存在",
            fix_suggestion="先補 og:image meta（O2）",
        )
    full = urljoin(base_url, og_image)
    ok, status, ctype = _head_image(full)
    if ok and ctype.startswith("image/"):
        return AuditCheck(
            rule_id="I3",
            name="og:image accessible（HEAD 200 + image type）",
            category="images",
            severity="warning",
            status="pass",
            actual=f"HEAD {status} {ctype}",
            expected="2xx + content-type image/*",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="I3",
        name="og:image accessible（HEAD 200 + image type）",
        category="images",
        severity="warning",
        status="fail",
        actual=f"HEAD status={status} content-type={ctype!r}",
        expected="2xx + content-type image/*",
        fix_suggestion="修正 og:image URL 或上傳新圖（避免 social share 預覽炸圖）",
    )


# ── I4: lazy loading 覆蓋率（首屏外） ──


def _check_lazy_loading(soup: BeautifulSoup) -> AuditCheck:
    imgs = _all_imgs(soup)
    if len(imgs) <= _FIRST_VIEWPORT_IMAGES:
        return AuditCheck(
            rule_id="I4",
            name="lazy loading 覆蓋（首屏外 ≥ 80%）",
            category="images",
            severity="info",
            status="skip",
            actual=f"img 數 {len(imgs)} ≤ 首屏估算 {_FIRST_VIEWPORT_IMAGES}",
            expected="首屏外 lazy 比例 ≥ 80%",
            fix_suggestion="",
        )
    rest = imgs[_FIRST_VIEWPORT_IMAGES:]
    lazy = sum(1 for i in rest if (i.get("loading") or "").lower() == "lazy")
    ratio = lazy / len(rest)
    if ratio >= _LAZY_PASS_RATIO:
        return AuditCheck(
            rule_id="I4",
            name="lazy loading 覆蓋（首屏外 ≥ 80%）",
            category="images",
            severity="info",
            status="pass",
            actual=f"{lazy}/{len(rest)} = {ratio:.0%}",
            expected=f"≥ {_LAZY_PASS_RATIO:.0%}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="I4",
        name="lazy loading 覆蓋（首屏外 ≥ 80%）",
        category="images",
        severity="info",
        status="warn",
        actual=f"{lazy}/{len(rest)} = {ratio:.0%}",
        expected=f"≥ {_LAZY_PASS_RATIO:.0%}",
        fix_suggestion='補 loading="lazy" 在非首屏 img；首屏 img 保 eager 給 LCP',
    )


# ── I5: WebP/AVIF 比例 ──


def _check_modern_format(soup: BeautifulSoup, base_url: str) -> AuditCheck:
    imgs = _all_imgs(soup)
    candidates = [i for i in imgs if (i.get("src") or "").strip()]
    if not candidates:
        return AuditCheck(
            rule_id="I5",
            name="WebP/AVIF modern format 比例 ≥ 50%",
            category="images",
            severity="info",
            status="skip",
            actual="頁面無可驗 img",
            expected="N/A",
            fix_suggestion="",
        )
    sample = candidates[:8]  # 限制 HEAD call 數量
    modern = 0
    for img in sample:
        full = urljoin(base_url, img.get("src", ""))
        ok, _status, ctype = _head_image(full)
        if ok and any(fmt in ctype for fmt in ("image/webp", "image/avif")):
            modern += 1
    ratio = modern / len(sample)
    if ratio >= _WEBP_AVIF_PASS_RATIO:
        return AuditCheck(
            rule_id="I5",
            name="WebP/AVIF modern format 比例 ≥ 50%",
            category="images",
            severity="info",
            status="pass",
            actual=f"{modern}/{len(sample)} = {ratio:.0%}（取樣前 {len(sample)} 張）",
            expected=f"≥ {_WEBP_AVIF_PASS_RATIO:.0%}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="I5",
        name="WebP/AVIF modern format 比例 ≥ 50%",
        category="images",
        severity="info",
        status="warn",
        actual=f"{modern}/{len(sample)} = {ratio:.0%}",
        expected=f"≥ {_WEBP_AVIF_PASS_RATIO:.0%}",
        fix_suggestion="主圖優化 WebP；舊 jpg/png 重新生成（SEOPress WebP plugin 可批次）",
    )
