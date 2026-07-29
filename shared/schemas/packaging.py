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

    @model_validator(mode="after")
    def _rank_gate_needs_panel_note(self) -> "TitleV1":
        if self.rank >= 4 and not self.panel_note:
            raise ValueError(
                f"rank {self.rank} title requires panel_note "
                "(gate display for rejected titles; VPS cannot read G: title_trace)"
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


class ApprovalV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cut_id: str
    approved: bool
    primary_package: int = Field(ge=1, le=3)
    reject_note: str | None = None
    decided_at: AwareDatetime


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
