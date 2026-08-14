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


class RenderRequestV1(BaseModel):
    """修修在 gate 上組好的封面配方 — 桌機端據此 **render 一次**（2026-08-14 裁決）。

    他原話：「比較好的方式是，先把標題、大字跟 cutout 選定之後，你再去做 render，
    這樣 render 一次就好了。」預先窮舉變體會爆炸（cutout 對 × 大字 = N×M 張），
    而且他要的組合往往不在裡面。gate 端只寫「配方」，不 render（D11 不變）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title_rank: int = Field(ge=1, le=5)
    host_cutout: str
    guest_cutout: str
    big_text: list[str] = Field(min_length=1, max_length=3)
    highlight_text: str = ""
    requested_at: AwareDatetime
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
        if not any(line.strip() for line in self.big_text):
            raise ValueError("big_text 不可全為空白——封面大字是 N1 卡型的主體")
        if self.highlight_text and self.highlight_text not in "".join(self.big_text):
            raise ValueError(
                f"highlight_text {self.highlight_text!r} 不在 big_text 內"
                "（橘框詞必須是大字的子字串，否則 render 出來不會有框）"
            )
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
