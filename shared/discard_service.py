"""Discard pipeline for inbox / source files (Slice D, issue #356).

PRD: ``docs/plans/2026-05-04-stage-1-ingest-unify.md``

修修 在 reader header 或 inbox row 按「丟掉這篇」按鈕 → POST `/discard?file=&base=`
→ ``DiscardService.discard(path, base)``：

1. 計算該檔對應的 ``KB/Annotations/{slug}.md`` annotation 條數（給前端 confirm
   prompt 用，避免誤刪）。
2. 把原檔 send 進 Windows 回收桶（PowerShell `[FileSystem]::DeleteFile`，遵
   守 ``feedback_powershell_allow_exact_prefix.md`` 的 prefix 對齊規則）；
   非 Windows 直接 unlink。
3. annotation 檔（如存在）也送進回收桶 — 連動刪保證 vault 不留 orphan
   ``KB/Annotations/{slug}.md`` 指向已刪除的 source。

DiscardReport 帶回 ``deleted_file: bool`` + ``annotation_count: int`` +
``annotation_deleted: bool`` 三個欄位，呼叫端可以決定要不要在 redirect 後
flash「已刪 N 條 annotation」訊息（目前 redirect 不帶 message，但 schema 已留路）。

Interface 故意 simple — 整個 destructive 動作藏在這個 method 後面，呼叫端
（``thousand_sunny.routers.robin`` 的 `/discard` endpoint）只要 form-validate
+ resolve path + 呼叫 `discard()` 即可。
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shared.annotation_store import AnnotationStore, annotation_slug, get_annotation_store
from shared.log import get_logger
from shared.utils import extract_frontmatter, read_text

logger = get_logger("nakama.shared.discard")


@dataclass(frozen=True)
class DiscardReport:
    """Summary of a `DiscardService.discard()` call.

    All fields are populated even for partial failures (e.g. file already
    gone) so the caller has a single point to decide flash-message wording.
    """

    file_path: Path
    """The file path that was targeted (may not still exist if delete succeeded)."""

    slug: str
    """``annotation_slug`` derived from filename + frontmatter."""

    annotation_count: int
    """Number of annotation items in ``KB/Annotations/{slug}.md`` BEFORE the
    discard ran. ``0`` if the annotation file did not exist."""

    deleted_file: bool
    """True iff the source file existed at call-time AND the recycle-bin
    invocation completed without raising. Note: a Windows PowerShell call
    failing silently still flips this True (we don't shell-poll for
    success — the file disappearing is enough downstream)."""

    annotation_deleted: bool
    """True iff a ``KB/Annotations/{slug}.md`` existed AND was sent to
    recycle bin. False when no annotation file existed (most common case)."""


def _send_to_recycle_bin(path: Path) -> None:
    """Send ``path`` to Windows recycle bin or unlink on POSIX.

    Mirrors ``thousand_sunny.routers.robin._send_to_recycle_bin`` (kept as a
    parallel impl so DiscardService can be unit-tested without booting the
    FastAPI module). Both implementations MUST stay in sync with
    ``feedback_powershell_allow_exact_prefix.md`` — the PowerShell command
    prefix is matched character-by-character by ``.claude/settings.json``
    allow rules; **do NOT add ``-NoProfile``**, **do NOT change argument
    order**, **do NOT switch to ``send2trash``**.

    No-op if path does not exist (matches ``Path.unlink(missing_ok=True)``).
    """
    if not path.exists():
        return
    if platform.system() == "Windows":
        ps_cmd = (
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
            f"'{path}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
        )
        subprocess.run(["powershell", "-Command", ps_cmd], check=False)
    else:
        path.unlink(missing_ok=True)


class DiscardService:
    """Send a vault file to recycle bin and clean up its annotation companion.

    Constructor accepts an optional ``AnnotationStore`` (DI for tests) and an
    optional ``recycle_bin_fn`` callable (DI so tests can assert the recycle-
    bin path is invoked without subprocess.run side effects). Both default
    to the production singletons.
    """

    def __init__(
        self,
        *,
        annotation_store: AnnotationStore | None = None,
        recycle_bin_fn=_send_to_recycle_bin,
        annotations_dir: Path | None = None,
    ) -> None:
        """
        Args:
            annotation_store: Override for the shared singleton (test fixtures
                pass a temp-vault-pointed instance).
            recycle_bin_fn: Override for the recycle-bin call. Tests pass a
                MagicMock so they can assert how many times / with what path
                the function was invoked, without actually shelling out.
            annotations_dir: Override for the ``KB/Annotations`` directory
                lookup. Defaults to ``vault / KB / Annotations`` via
                ``get_vault_path()`` at the moment ``discard`` is called
                (deferred so tests that monkeypatch ``VAULT_PATH`` mid-test
                see the right path).
        """
        self._store = annotation_store if annotation_store is not None else get_annotation_store()
        self._recycle_bin = recycle_bin_fn
        self._annotations_dir = annotations_dir

    def annotation_count_for(self, file_path: Path) -> tuple[str, int]:
        """Return ``(slug, count)`` — used by frontend before showing confirm prompt.

        Reads the source file's frontmatter to derive the slug; falls back to
        filename stem if the file is missing or unreadable. Count is the total
        ``items`` length in ``KB/Annotations/{slug}.md`` (0 if no such file).
        """
        slug = self._slug_for(file_path)
        ann_set = self._store.load(slug)
        count = len(ann_set.items) if ann_set is not None else 0
        return slug, count

    def discard(self, file_path: Path, base: str) -> DiscardReport:
        """Send the source file + its annotation companion to recycle bin.

        Args:
            file_path: Absolute path of the file to discard. Caller is
                responsible for path-traversal validation (use
                ``thousand_sunny.helpers.safe_resolve`` upstream).
            base: ``inbox`` / ``sources`` — recorded in logs only, the
                discard operation itself does not branch on base.

        Returns:
            ``DiscardReport`` with annotation count + deletion booleans.
            **Does not raise** when the source file doesn't exist —
            returns a report with ``deleted_file=False`` so the caller can
            still redirect to inbox cleanly (idempotency for double-clicks).

        Side effects:
            - source file → Windows recycle bin (or POSIX unlink)
            - ``KB/Annotations/{slug}.md`` → recycle bin if it existed
        """
        slug = self._slug_for(file_path)
        ann_set = self._store.load(slug)
        ann_count = len(ann_set.items) if ann_set is not None else 0

        existed = file_path.exists()
        if existed:
            self._recycle_bin(file_path)
            logger.info("discard: %s (base=%s, annotations=%d)", file_path.name, base, ann_count)
        else:
            logger.info("discard noop (file already gone): %s (base=%s)", file_path.name, base)

        ann_path = self._annotation_path(slug)
        ann_existed = ann_path.exists()
        if ann_existed:
            self._recycle_bin(ann_path)
            logger.info("discard annotation: %s.md", slug)

        return DiscardReport(
            file_path=file_path,
            slug=slug,
            annotation_count=ann_count,
            deleted_file=existed,
            annotation_deleted=ann_existed,
        )

    # ── Internals ────────────────────────────────────────────────────────────

    def _slug_for(self, file_path: Path) -> str:
        """Derive the annotation slug from the source file's frontmatter (or stem)."""
        frontmatter: dict = {}
        if file_path.exists():
            try:
                content = read_text(file_path)
                frontmatter, _ = extract_frontmatter(content)
            except (OSError, ValueError):
                # Falls through to filename-stem slug (matches /read endpoint
                # behaviour when frontmatter is malformed).
                frontmatter = {}
        return annotation_slug(file_path.name, frontmatter)

    def _annotation_path(self, slug: str) -> Path:
        """Resolve ``KB/Annotations/{slug}.md`` from constructor override or vault."""
        if self._annotations_dir is not None:
            return self._annotations_dir / f"{slug}.md"
        # Late import + late call so VAULT_PATH monkeypatches in tests are honoured.
        from shared.config import get_vault_path

        return get_vault_path() / "KB" / "Annotations" / f"{slug}.md"
