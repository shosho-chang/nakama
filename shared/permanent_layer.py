"""KB/Permanent/ 寫入紀律 — 人寫永久卡層的守門員（N520 / Centaur Zettelkasten）.

Centaur 規格 v0.2 §7 紅線 1：**AI 絕不寫 ``KB/Permanent/`` 正文與 status**。
永久卡是修修「怎麼想」的權威層，一卡一概念、用自己的話。AI 對這層的唯一
合法動作是「記帳」——回填它自己維護的 frontmatter 欄位（``source_refs`` /
``modified`` / ``aliases``），絕不碰正文、status、或任何其他 key。

本 module 提供三組原語，集中在一處避免 policy 散落：

1. :data:`PERMANENT_DIR` + :func:`is_permanent_path` — 路徑判定（給 indexer /
   search / promotion guard 共用）。
2. :func:`assert_not_permanent_target` — 負向 tripwire 用：任何 agent 寫入路徑
   解析若落在 ``KB/Permanent/`` 一律 raise（promotion resolver 在 chokepoint
   呼叫，見 ``shared/promotion_targets.py``）。
3. :func:`update_permanent_bookkeeping` — AI 對永久卡的**唯一**寫入口，白名單
   key、正文逐字保留。

設計刻意不 import 任何 promotion / kb_writer module，保持無循環依賴：
``promotion_targets`` 反過來 import 本 module 的 guard。
"""

from __future__ import annotations

from pathlib import Path

from shared.utils import extract_frontmatter

# ---------------------------------------------------------------------------
# 路徑常數 + 判定
# ---------------------------------------------------------------------------

#: vault-relative 永久層根目錄。路徑比對一律用 forward-slash posix 形式。
PERMANENT_DIR = "KB/Permanent"

#: AI 記帳唯一允許觸碰的 frontmatter key。其餘 key（含 ``status`` / ``type`` /
#: ``author`` / ``created``）與正文一律禁寫。
ALLOWED_BOOKKEEPING_KEYS = frozenset({"source_refs", "modified", "aliases"})


class PermanentWriteViolation(Exception):
    """嘗試以非法方式寫入 ``KB/Permanent/`` 時 raise（紅線 1 攔截）。

    兩個觸發點：
    - agent 寫入路徑解析落在 ``KB/Permanent/``（:func:`assert_not_permanent_target`）
    - bookkeeping 帶非白名單 key 或正文（:func:`update_permanent_bookkeeping`）
    """


def _normalize(path: str | Path) -> str:
    """把任意路徑正規化成 forward-slash 字串供前綴比對。

    顯式 replace 反斜線——不可用 ``Path.as_posix()``：它是**平台相依**的，Windows
    把 ``\\`` 當分隔符轉 ``/``，但 Linux 把 ``\\`` 當合法檔名字元保留，導致同一個
    Windows 絕對路徑（如 ``E:\\Shosho LifeOS\\KB\\Permanent\\card.md``）在 CI(Linux)
    上判定失敗。手動 replace 在兩平台行為一致。
    """
    return str(path).replace("\\", "/")


def is_permanent_path(path: str | Path) -> bool:
    """``path`` 是否落在 ``KB/Permanent/`` 之下（vault-relative 或絕對皆可）。

    用 segment-aware 比對，避免 ``KB/PermanentDrafts/`` 之類前綴誤判。
    """
    norm = _normalize(path)
    return norm == PERMANENT_DIR or f"/{PERMANENT_DIR}/" in f"/{norm}/"


def assert_not_permanent_target(path: str | Path | None) -> None:
    """negative tripwire：若 ``path`` 落在永久層則 raise（紅線 1）。

    promotion resolver chokepoint 在回傳前呼叫此函式，確保任何 ReviewItem
    （即使 manifest 蓄意把 ``target_kb_path`` 設成 ``KB/Permanent/...``）都無法
    讓 agent 寫入永久層。``None``（無 target）視為安全、直接通過。
    """
    if path is None:
        return
    if is_permanent_path(path):
        raise PermanentWriteViolation(
            f"agent write target resolved into {PERMANENT_DIR}/ "
            f"({_normalize(path)!r}); 紅線 1 — AI 不寫永久卡。"
            f" 永久卡只能由 human-authoring surface（N523 POST /kb/api/permanent）寫入。"
        )


# ---------------------------------------------------------------------------
# 記帳寫入口（AI 對永久卡的唯一合法寫入）
# ---------------------------------------------------------------------------


def _split_frontmatter_block(content: str) -> tuple[str, str, str]:
    """把檔案內容切成 (frontmatter_block, body, raw_fm_text)。

    回傳的 ``frontmatter_block`` 含起訖 ``---`` 與其間原始文字（逐字，未 re-dump）；
    ``body`` 是 frontmatter 之後的正文逐字內容。沒有 frontmatter 時
    ``frontmatter_block`` 為空字串、``body`` 為整份內容。
    """
    if not content.startswith("---"):
        return "", content, ""
    # 找第二個 '---' 行
    lines = content.splitlines(keepends=True)
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        return "", content, ""
    fm_block = "".join(lines[: end_idx + 1])
    raw_fm_text = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    return fm_block, body, raw_fm_text


def _dump_scalar(value: object) -> str:
    """最小 YAML scalar/list dump（記帳欄只會是 str / list[str] / date 字串）。"""
    if isinstance(value, list):
        if not value:
            return "[]"
        items = "\n".join(f"  - {_dump_inline(v)}" for v in value)
        return "\n" + items
    return f" {_dump_inline(value)}"


def _dump_inline(value: object) -> str:
    s = str(value)
    # 含特殊字元時加雙引號（永久卡 source_refs 常含 [[...]] ^anchor，需引）
    if any(c in s for c in ":#[]{}") or s != s.strip():
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return s


def update_permanent_bookkeeping(
    card_path: str | Path,
    updates: dict[str, object],
    *,
    vault_root: str | Path | None = None,
) -> None:
    """AI 對永久卡的唯一合法寫入：只改白名單記帳 key，正文逐字保留。

    Args:
        card_path: 永久卡路徑（vault-relative ``KB/Permanent/{name}.md`` 或絕對）。
        updates:   要寫的記帳 key→value，**只允許** ``source_refs`` /
                   ``modified`` / ``aliases``。
        vault_root: ``card_path`` 為相對路徑時的 vault 根；絕對路徑時忽略。

    Raises:
        PermanentWriteViolation:
            - ``updates`` 含白名單外的 key（含試圖塞 ``status`` / ``body``）
            - ``card_path`` 不在 ``KB/Permanent/`` 之下
        FileNotFoundError: 卡片不存在（記帳不負責建檔；建檔走 human surface）。

    紅線保證：本函式**永不**寫正文、**永不**新增/修改非白名單 key、**永不**建檔。
    它只 in-place 覆寫既有記帳欄或補上缺的白名單欄。
    """
    # 1) 路徑必須在永久層內（防止被誤用成通用 frontmatter writer）
    if not is_permanent_path(card_path):
        raise PermanentWriteViolation(
            f"update_permanent_bookkeeping 只能寫 {PERMANENT_DIR}/；收到 {_normalize(card_path)!r}"
        )

    # 2) 白名單 key 檢查 — 任何越界 key（尤其 status / type / author / 正文）即 raise
    illegal = set(updates) - ALLOWED_BOOKKEEPING_KEYS
    if illegal:
        raise PermanentWriteViolation(
            f"記帳只允許 {sorted(ALLOWED_BOOKKEEPING_KEYS)}；"
            f"拒絕越界 key {sorted(illegal)}（紅線 1 — AI 不改 status/正文）。"
        )
    if not updates:
        return

    abs_path = Path(card_path)
    if not abs_path.is_absolute():
        if vault_root is None:
            raise ValueError("card_path 為相對路徑時必須提供 vault_root")
        abs_path = Path(vault_root) / card_path

    if not abs_path.exists():
        raise FileNotFoundError(
            f"永久卡不存在：{abs_path}。記帳不建檔——建檔走 human-authoring surface。"
        )

    content = abs_path.read_text(encoding="utf-8")
    fm_block, body, _raw = _split_frontmatter_block(content)
    existing_fm, _ = extract_frontmatter(content)

    # 3) 合併：既有 fm + 白名單 updates（只動白名單 key），逐欄 re-dump 但正文逐字保留
    merged: dict[str, object] = dict(existing_fm)
    for key, value in updates.items():
        merged[key] = value

    # 4) 重組 frontmatter：保序——先既有 key 順序，新增白名單 key 補在尾端
    ordered_keys: list[str] = []
    for key in existing_fm:
        ordered_keys.append(key)
    for key in updates:
        if key not in ordered_keys:
            ordered_keys.append(key)

    fm_lines = ["---"]
    for key in ordered_keys:
        fm_lines.append(f"{key}:{_dump_scalar(merged[key])}")
    fm_lines.append("---")
    new_fm_block = "\n".join(fm_lines) + "\n"

    abs_path.write_text(new_fm_block + body, encoding="utf-8")
