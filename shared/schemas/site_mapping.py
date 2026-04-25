"""Host ↔ `TargetSite` 雙向對照表（ADR-009 triangulation T5）。

**為何獨立成檔**：原提案把 `host_to_target_site()` 放在 `gsc_client.py`；
triangulation T5 反對，理由是 gsc_client 是 I/O adapter，這種 pure
lookup 放進去會污染單元測試邊界、其他 skill（seo-audit-post 等）要複用
時形成反向 import。獨立成 `shared/schemas/site_mapping.py` 與 `publishing`
並列，保持 I/O 無依賴。

**窮舉不變式**：`set(HOST_TO_TARGET_SITE.values()) == set(TargetSite.__args__)`
由 `tests/shared/schemas/test_site_mapping.py` 守；新增 target 必同步兩邊。
"""

from __future__ import annotations

from shared.schemas.publishing import TargetSite

# Canonical host（去 `www.`）→ app-name。
# 對外 canonical URL 用的 host；GSC property 另存 env（見 `.env.example`），
# 因為同一 site 有 `sc-domain:` / `https://` 兩種 property 形式，與本表職責分離。
HOST_TO_TARGET_SITE: dict[str, TargetSite] = {
    "shosho.tw": "wp_shosho",
    "fleet.shosho.tw": "wp_fleet",
}


class UnknownHostError(ValueError):
    """Host 不在 `HOST_TO_TARGET_SITE` — 呼叫方需先驗 URL domain。"""


def host_to_target_site(host: str) -> TargetSite:
    """`host` → `TargetSite` app-name。未知 host raise `UnknownHostError`。

    `host` 應為 canonical lowercased hostname（不含 protocol / port / path）；
    呼叫方若從 URL 拆，請先 `urllib.parse.urlparse(url).hostname.lower()`。
    """
    try:
        return HOST_TO_TARGET_SITE[host]
    except KeyError as e:
        known = ", ".join(sorted(HOST_TO_TARGET_SITE))
        raise UnknownHostError(f"unknown host {host!r}; known: {known}") from e
