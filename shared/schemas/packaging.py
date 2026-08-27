"""Packaging pipeline schema — packages.json + approval.json (ADR-054 §附錄C/D15).

Two JSON files form the contract between skill (writer) and Bridge/publish layer (reader):
- packages.json  — title candidates + thumbnail packages per cut  → PackagesFileV1
- approval.json  — gate decisions, one per cut                    → ApprovalFileV1

附錄 C 草案的 approval.json 是單 cut 物件（ApprovalV1）；一集有多支長片要各自
approve，S7 落地時升級為 ApprovalFileV1 容器（approvals list，cut_id 唯一）。
ApprovalV1 保留為單筆 entry 模型。
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_serializer, model_validator

_PNG_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+\.png$")
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[/\\]")
_VARIANT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_abs_path(s: str) -> bool:
    return bool(_WIN_ABS_RE.match(s)) or s.startswith("/")


def _png_basename(vault_relative_path: str) -> str:
    return PurePosixPath(vault_relative_path).name


class TitleV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    archetype_id: str
    angle_combo: list[str]
    payoff: str
    cite: str
    rank: int = Field(ge=1, le=5)
    panel_note: str | None = None
    # 修修在 gate 手改字後填這兩欄。ADR-054 D11 的「UI 零 LLM」硬約束只是
    # 「VPS FastAPI 呼叫不到桌機 Cowork」——禁的是 LLM **生成**，不禁人工編輯；
    # 品味的最終裁決權永遠在他身上（feedback_hitl_gate_serves_subjective_taste）。
    # 留 original_text 是為了讓 archetype_id / angle_combo / cite / payoff 仍能
    # 溯源到它們實際描述的那句話——否則推導鏈會謊稱手改後的文字是 panel 跑出來的。
    original_text: str | None = None
    edited_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _rank_gate_needs_panel_note(self) -> "TitleV1":
        if self.rank >= 4 and not self.panel_note:
            raise ValueError(
                f"rank {self.rank} title requires panel_note "
                "(gate display for rejected titles; VPS cannot read G: title_trace)"
            )
        return self


class VariantV1(BaseModel):
    """同一條標題的一個封面變體 — 差在臉（cutout 表情）與封面大字。

    修修 2026-08-14 裁決：gate 上要能勾臉、能改封面大字。render 只能在桌機跑
    （Chrome/hyperframes/字型都在那），VPS 的 Bridge 叫不到，所以走「桌機先把
    變體 render 完 → gate 純勾選」的變體板路線（v2.5「感知量收斂 = 變體板」的
    延伸）。gate 端零 render、零 LLM，不違反 ADR-054 D11。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    variant_id: str
    thumbnail_png: str
    host_cutout: str
    guest_cutout: str
    big_text: list[str] = Field(min_length=1, max_length=3)
    highlight_text: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "VariantV1":
        if not _VARIANT_ID_RE.match(self.variant_id):
            raise ValueError(
                f"variant_id must match [A-Za-z0-9._-]+, got {self.variant_id!r} "
                "(進檔名與 form value，CJK/空白會壞掉)"
            )
        for name in ("thumbnail_png", "host_cutout", "guest_cutout"):
            val: str = getattr(self, name)
            if _is_abs_path(val):
                raise ValueError(f"{name} must be vault-relative path, got absolute: {val!r}")
            if val.lower().endswith(".png") and not _PNG_SLUG_RE.match(_png_basename(val)):
                raise ValueError(f"PNG filename must match [A-Za-z0-9._-]+\\.png, got {val!r}")
        if self.highlight_text and self.highlight_text not in "".join(self.big_text):
            raise ValueError(
                f"highlight_text {self.highlight_text!r} 不在 big_text 內 "
                "（橘框詞必須是大字的子字串，否則 render 出來不會有框）"
            )
        return self


class PackageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title_rank: int = Field(ge=1, le=5)
    thumbnail_png: str
    thumb_archetype_id: str
    joint_pairing_id: str
    host_cutout: str
    guest_cutout: str
    # The recipe belongs to the package it renders.  Older packages predate the
    # editor and remain readable with an explicit None (the UI must show that
    # absence instead of borrowing another package/cut-level request).
    render_recipe: "RenderRequestV1 | None" = None
    # 空 list = 這支還沒產變體（舊集數／短片）；gate 就退化成單張顯示。
    variants: list[VariantV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_variant_ids(self) -> "PackageV1":
        ids = [v.variant_id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate variant_id in package rank {self.title_rank}: {ids}")
        return self

    @model_validator(mode="after")
    def _validate_path_fields(self) -> "PackageV1":
        for name in ("thumbnail_png", "host_cutout", "guest_cutout"):
            val: str = getattr(self, name)
            if _is_abs_path(val):
                raise ValueError(
                    f"{name} must be vault-relative path, got absolute: {val!r} "
                    "(set VAULT_PATH env; paths like E:\\ or /home break cross-machine reads)"
                )
            if val.lower().endswith(".png"):
                basename = _png_basename(val)
                if not _PNG_SLUG_RE.match(basename):
                    raise ValueError(
                        f"PNG filename must match [A-Za-z0-9._-]+\\.png, "
                        f"got {basename!r} in {name}={val!r} (CJK/spaces break Syncthing + S3)"
                    )
        return self


class BrainstormTitleV1(BaseModel):
    """gate 候選池裡的一條標題（display-only，不佔 packages 的 5 條 rank）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    angle: str = ""
    note: str = ""


class BrainstormBigTextV1(BaseModel):
    """gate 候選池裡的一組封面大字。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lines: list[str] = Field(min_length=1, max_length=3)
    highlight: str = ""
    angle: str = ""

    @model_validator(mode="after")
    def _highlight_in_lines(self) -> "BrainstormBigTextV1":
        if self.highlight and self.highlight not in "".join(self.lines):
            raise ValueError(f"highlight {self.highlight!r} 不在 lines 內")
        return self


class BrainstormV1(BaseModel):
    """標題／大字候選池 — 修修 2026-08-14：「把好幾個方向都列出來讓我挑，挑完填到格子裡」。

    這是 **display-only** 的池子：不動 `titles`（長片固定 5 條、帶推導鏈）也不動
    packages。gate 上每條旁邊一個「填入」把文字塞進〈組封面〉的格子，全部在瀏覽器
    端做，沒有 LLM 呼叫（D11 不變）。內容由桌機端 brainstorm 後寫入。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    titles: list[BrainstormTitleV1] = Field(default_factory=list)
    bigtexts: list[BrainstormBigTextV1] = Field(default_factory=list)


class CutV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cut_id: str
    format: Literal["long", "short"]
    information_origin: Literal["full_text", "one_liner"]
    visual_recipe: Literal["podcast", "youtube_host", "youtube_book"]
    aspect: Literal["16:9"]
    titles: list[TitleV1]
    packages: list[PackageV1]
    citations: list[str] = Field(default_factory=list)
    brand_flags: list[str] = Field(default_factory=list)
    # Only field exempt from absolute-path rejection (D10 硬規則①): working-set relative,桌機 only.
    title_trace_ref: str | None = None
    # 標題／大字候選池（display-only；gate 上「填入」用）
    brainstorm: BrainstormV1 | None = None
    # Long cuts must NOT set this; short cuts MUST explicitly set null.
    # model_fields_set tells apart "explicitly null" from "omitted/defaulted".
    thumbnail: None = None

    @model_validator(mode="after")
    def _thumbnail_asymmetry(self) -> "CutV1":
        has_thumbnail = "thumbnail" in self.model_fields_set
        if self.format == "long" and has_thumbnail:
            raise ValueError(
                "long format cut must NOT include thumbnail field "
                "(thumbnail resolved via packages[approval.primary_package].thumbnail_png)"
            )
        if self.format == "short" and not has_thumbnail:
            raise ValueError(
                "short format cut must explicitly set thumbnail: null "
                "(omitting is ambiguous — cannot distinguish 'not needed' from 'not yet done')"
            )
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict:
        d = handler(self)
        # Omit thumbnail from serialized output for long cuts — the field is only meaningful
        # as an explicit null on short cuts; including it as null on long cuts breaks round-trip
        # because deserialization would see it as explicitly set and trigger the validator.
        if self.format == "long" and "thumbnail" not in self.model_fields_set:
            d.pop("thumbnail", None)
        return d

    @model_validator(mode="after")
    def _count_constraints(self) -> "CutV1":
        if self.format == "long":
            if len(self.titles) != 5:
                raise ValueError(
                    f"long format cut requires exactly 5 titles, got {len(self.titles)}"
                )
            if len(self.packages) != 3:
                raise ValueError(
                    f"long format cut requires exactly 3 packages, got {len(self.packages)}"
                )
        else:
            if len(self.titles) != 1:
                raise ValueError(
                    "short format cut requires exactly 1 title "
                    f"(LLM direct output), got {len(self.titles)}"
                )
            if self.packages:
                raise ValueError(
                    f"short format cut must have empty packages [], got {len(self.packages)}"
                )
        return self


class PackagesFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: str
    generated_at: str
    cuts: list[CutV1]


class GeometryV1(BaseModel):
    """兩張臉的位置與大小（%，直接對應 composition 的 CSS 變數）。

    修修 2026-08-15：「cutout 的位置跟大小都沒有很確定……有辦法讓我直接在 web UI
    去做調整嗎？」——solver 只保證參數自洽，看起來對不對是他的眼睛說了算。
    這六個數字由 gate 的拖曳/縮放介面寫進來；`None`（整個 geometry 不存在）＝
    照舊跑 solver。桌機端 render 完會把實際用的值寫回來當下次的起點。

    座標系跟 composition 一致：x 是離該側邊界的 %（負值＝往畫布外推），
    y 是離底部的 %，height 是佔畫布高的 %。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    host_height_pct: float = Field(gt=0, le=400)
    host_x_pct: float = Field(ge=-200, le=200)
    host_y_pct: float = Field(ge=-200, le=200)
    guest_height_pct: float = Field(gt=0, le=400)
    guest_x_pct: float = Field(ge=-200, le=200)
    guest_y_pct: float = Field(ge=-200, le=200)


class CenterGeometryV1(BaseModel):
    """N2 中央實拍卡的位置與大小，直接對應 thumbnail_reaction。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width_pct: float = Field(ge=50, le=100)
    height_px: float = Field(ge=120, le=720)
    x_pct: float = Field(ge=-50, le=150)
    y_pct: float = Field(ge=-50, le=150)

    @model_validator(mode="after")
    def _horizontal_card(self) -> "CenterGeometryV1":
        if self.width_pct / 100 * 1280 <= self.height_px:
            raise ValueError("N2 center card 必須是橫向長方形")
        half_w = self.width_pct / 2
        half_h = self.height_px / 720 * 50
        if not (half_w <= self.x_pct <= 100 - half_w):
            raise ValueError("N2 center card 左右不可超出畫布")
        if not (half_h <= self.y_pct <= 100 - half_h):
            raise ValueError("N2 center card 上下不可超出畫布")
        return self


class RenderRequestV1(BaseModel):
    """修修在 gate 上組好的封面配方 — 桌機端據此 **render 一次**（2026-08-14 裁決）。

    他原話：「比較好的方式是，先把標題、大字跟 cutout 選定之後，你再去做 render，
    這樣 render 一次就好了。」預先窮舉變體會爆炸（cutout 對 × 大字 = N×M 張），
    而且他要的組合往往不在裡面。gate 端只寫「配方」，不 render（D11 不變）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    composition: Literal["thumbnail_full", "thumbnail_reaction"] = "thumbnail_full"
    title_rank: int = Field(ge=1, le=5)
    host_cutout: str
    guest_cutout: str
    big_text: list[str] = Field(default_factory=list, max_length=3)
    highlight_text: str = ""
    # 大字的寬度預算（px @1280 畫布）。composition 是「整塊縮字不換行」：
    # fontSize = 100 * title_max_width / 實際行寬。所以這個值就是字級的旋鈕——
    # 調大字更大但更容易被臉蓋到，調小反之（修修 2026-08-15 要能自己選）。
    title_max_width: int = Field(default=580, ge=300, le=1000)
    # 來賓抬頭（「泛科學知識長 鄭國威」）。以前只活在桌機端的 spec 檔裡，靠 glob
    # 上一份 spec 撈——2026-08-15 把中間產物搬進 _work/ 就撈不到，整行從封面消失。
    # 收進配方後 gate 看得到也改得動，不再靠檔案系統的巧合。
    guest_credit: str = Field(default="", max_length=40)
    # N1 author interviews use the book as a dark, full-height background.
    # Keeping these values in the package recipe makes a Web rerender lossless;
    # previously render_request.py silently fell back to a plain background.
    book_cover: str | None = None
    book_cover_opacity: float = Field(default=0.42, ge=0, le=1)
    book_cover_brightness: float = Field(default=0.38, ge=0, le=1)
    book_cover_height_pct: float = Field(default=100, ge=20, le=150)
    # N2 only: the image and orange frame are one editable layer behind both people.
    # The path stays vault-relative so the desktop renderer can reproduce the exact package.
    center_visual_asset: str | None = None
    center_geometry: CenterGeometryV1 | None = None
    requested_at: AwareDatetime
    # geometry 兩種來源，靠 geometry_manual 分辨：
    #   False（預設）— solver 解完寫回來的，只當 gate 拖曳介面的起點，下次照樣重解
    #   True          — 修修自己拖的，render 端照抄不解算
    # 沒有這個旗標的話，solver 第一次寫回值就等於把自己鎖死（換臉也不會重解）。
    geometry: GeometryV1 | None = None
    geometry_manual: bool = False
    # 桌機端 render 完把成品 PNG 檔名寫回來，gate 才知道這份配方已經出圖。
    rendered_png: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "RenderRequestV1":
        for name in ("host_cutout", "guest_cutout"):
            val: str = getattr(self, name)
            if _is_abs_path(val):
                raise ValueError(f"{name} must be vault-relative path, got absolute: {val!r}")
            if not _PNG_SLUG_RE.match(_png_basename(val)):
                raise ValueError(f"cutout 檔名必須是 ASCII PNG，got {val!r}")
        if self.composition == "thumbnail_full" and not any(
            line.strip() for line in self.big_text
        ):
            raise ValueError("big_text 不可全為空白——封面大字是 N1 卡型的主體")
        if self.composition == "thumbnail_reaction" and any(
            line.strip() for line in self.big_text
        ):
            raise ValueError("thumbnail_reaction 是零文字 N2 卡型，big_text 必須為空")
        if self.highlight_text and self.highlight_text not in "".join(self.big_text):
            raise ValueError(
                f"highlight_text {self.highlight_text!r} 不在 big_text 內"
                "（橘框詞必須是大字的子字串，否則 render 出來不會有框）"
            )
        if self.book_cover is not None:
            if _is_abs_path(self.book_cover):
                raise ValueError("book_cover must be a vault-relative path")
            parts = PurePosixPath(self.book_cover).parts
            if "\\" in self.book_cover or ".." in parts:
                raise ValueError("book_cover must be a safe vault-relative path")
        if self.composition == "thumbnail_reaction":
            if not self.center_visual_asset or self.center_geometry is None:
                raise ValueError(
                    "thumbnail_reaction 必須帶 center_visual_asset 與 center_geometry"
                )
        if self.center_visual_asset is not None:
            if _is_abs_path(self.center_visual_asset):
                raise ValueError("center_visual_asset must be a vault-relative path")
            parts = PurePosixPath(self.center_visual_asset).parts
            if "\\" in self.center_visual_asset or ".." in parts:
                raise ValueError("center_visual_asset must be a safe vault-relative path")
        return self


# PackageV1 is declared before RenderRequestV1 because it is the long-standing
# public schema order.  Resolve the single forward reference after the recipe
# contract exists instead of duplicating its fields in another model.
PackageV1.model_rebuild()


class PackagingRevisionJobV1(BaseModel):
    """A human rejection queued for a desktop packaging revision agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["packaging-revision-job-v1"] = "packaging-revision-job-v1"
    request_id: str = Field(pattern=r"^revision-[a-f0-9]{16}$")
    feedback: str = Field(min_length=1, max_length=2000)
    requested_at: AwareDatetime
    source_packages_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_assets: dict[str, str]
    status: Literal["queued", "running", "ready_for_review", "failed"] = "queued"
    attempt: int = Field(default=0, ge=0)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    result_receipt: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _validate_paths_and_hashes(self) -> "PackagingRevisionJobV1":
        for path, digest in self.source_assets.items():
            parts = PurePosixPath(path).parts
            if (
                _is_abs_path(path)
                or "\\" in path
                or ".." in parts
                or not path.startswith("Attachments/packaging/")
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                raise ValueError(f"source_assets contains unsafe path/hash: {path!r}")
        if self.result_receipt is not None:
            parts = PurePosixPath(self.result_receipt).parts
            if (
                _is_abs_path(self.result_receipt)
                or "\\" in self.result_receipt
                or ".." in parts
                or not self.result_receipt.startswith(f"revisions/{self.request_id}/")
            ):
                raise ValueError("result_receipt must stay inside this revision directory")
        return self


class ApprovalV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cut_id: str
    approved: bool
    primary_package: int = Field(ge=1, le=3)
    reject_note: str | None = None
    decided_at: AwareDatetime
    # 真的按了 Approve／Reject 才有值；None = 只是挑了變體或打了大字，還沒裁決。
    # 舊檔沒這欄 → None，template 以 approved 回退判讀（見 packaging_board.html）。
    decision: Literal["approve", "reject"] | None = None
    # 修修在 gate 勾的封面變體（`VariantV1.variant_id`）。None = 還沒挑／該支沒變體。
    selected_variant: str | None = None
    # 變體都不滿意時打的字：`第一行／第二[橘框詞]` — VPS 不能 render，桌機端
    # thumbnail-brainstorm 讀到後重出一張新變體，不是即時生效。
    bigtext_request: str | None = None
    # 「先選好、再 render 一次」的配方（每支最多一份；要換就覆蓋）。
    render_request: RenderRequestV1 | None = None
    # Reject 會建立一筆 revision job；桌機 watcher 認領後交給獨立 Agent 重做，
    # 完成只回到 ready_for_review，永遠不由 worker 自動核准。
    revision_job: PackagingRevisionJobV1 | None = None


def parse_packages(path: "Path | str") -> PackagesFileV1:
    """Load and validate packages.json. Raises pydantic.ValidationError on bad shape."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return PackagesFileV1.model_validate(data)


class ApprovalFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: str
    approvals: list[ApprovalV1]

    @model_validator(mode="after")
    def _unique_cut_ids(self) -> "ApprovalFileV1":
        ids = [a.cut_id for a in self.approvals]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate cut_id in approvals: {ids}")
        return self


def parse_approval(path: "Path | str") -> ApprovalV1:
    """Load and validate a single-cut approval object. Raises pydantic.ValidationError."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ApprovalV1.model_validate(data)


def parse_approval_file(path: "Path | str") -> ApprovalFileV1:
    """Load and validate approval.json (multi-cut container). Raises pydantic.ValidationError."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ApprovalFileV1.model_validate(data)
