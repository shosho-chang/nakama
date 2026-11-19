"""YouTube 影片（書籍介紹／主題介紹）的封面配方與上架標題紀錄。

vault 落點：`Attachments/packaging/<episode_slug>/`（與 podcast packaging 同目錄，Syncthing 同步）

- `youtube_cover.json`  — 封面配方（Bridge 存、桌機 render_watcher 出圖回填）
- `title_tracking.json` — 上架標題紀錄（微調文字、已上頻道＝參加 A/B、影片網址）

素材路徑一律是 **vault-relative**（例如 `Attachments/cutouts/shosho/explaining/2.png`、
`Attachments/packaging/<slug>/assets/bg.jpg`），由呼叫端決定怎麼解析：Bridge 轉成 asset 路由、
桌機出圖轉成 file://。原型（`.claude/skills/book-thumbnail/editor/`）的 `spec.json` 欄位一一對應，
只把 `book` 改名為 `feature`（主題介紹影片放的是主題圖，不一定是書）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator

AB_TITLE_MAX = 3  # YouTube「測試和比較」一次最多 3 個標題

FrameStyle = Literal["skew", "burst", "offset", "double", "steps", "brackets", "none"]

_ALLOWED_PREFIXES = ("Attachments/cutouts/shosho/", "Attachments/packaging/")


def _vault_rel(value: str) -> str:
    v = (value or "").replace("\\", "/").strip()
    if not v:
        return v
    if v.startswith("/") or ":" in v.split("/")[0] or ".." in v.split("/"):
        raise ValueError(f"素材路徑要是 vault-relative，不能是絕對路徑或含 ..：{value!r}")
    if not v.startswith(_ALLOWED_PREFIXES):
        raise ValueError(f"素材只能來自 {_ALLOWED_PREFIXES}：{value!r}")
    return v


VaultPath = Annotated[str, AfterValidator(_vault_rel)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverBackgroundV1(_Strict):
    src: VaultPath
    brightness: float = Field(0.40, ge=0, le=2)
    grayscale: float = Field(0.7, ge=0, le=1)
    blur: float = Field(1.5, ge=0, le=20)
    warm: float = Field(0.35, ge=0, le=1)


class CoverFeatureV1(_Strict):
    """中央主圖：書籍介紹＝書封；主題介紹＝主題圖。"""

    src: VaultPath
    cx: float
    cy: float
    h: float = Field(gt=10, le=2000)
    tilt: float = Field(-12, ge=-45, le=45)
    frame_style: FrameStyle = "skew"


class CoverPersonV1(_Strict):
    """去背人物。`person`＝修修（去背照庫）；`guest`＝來賓（本集 assets/guest-*.png）。"""

    src: VaultPath
    cx: float
    top: float
    h: float = Field(gt=10, le=4000)
    glow: bool = True
    flip: bool = False


class CoverTextV1(_Strict):
    x: float
    y: float
    size: float = Field(gt=8, le=400)
    rot: float = Field(-6, ge=-30, le=30)
    lead: str = Field("", max_length=40)
    main: str = Field("", max_length=40)
    hl: str = Field("", max_length=40)


class CoverLogoV1(_Strict):
    on: bool = False
    x: float = 30
    y: float = 30
    h: float = Field(64, gt=4, le=400)


class YouTubeCoverRecipeV1(_Strict):
    bg: CoverBackgroundV1
    feature: CoverFeatureV1
    person: CoverPersonV1
    guest: CoverPersonV1 | None = None  # 來賓（選用；疊在修修後面）
    text: CoverTextV1
    logo: CoverLogoV1 = CoverLogoV1()
    bars: bool = True


class YouTubeCoverFileV1(_Strict):
    schema_: Literal["nakama.youtube_cover.v1"] = Field("nakama.youtube_cover.v1", alias="schema")
    episode: str
    recipe: YouTubeCoverRecipeV1
    requested_at: AwareDatetime | None = None  # Bridge 存配方時更新 → watcher 出圖
    rendered_at: AwareDatetime | None = None  # watcher 出圖成功時回填
    rendered_png: VaultPath | None = None  # vault-relative
    render_error: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def needs_render(self) -> bool:
        if self.requested_at is None:
            return False
        return self.rendered_at is None or self.rendered_at < self.requested_at


class TrackedTitleV1(_Strict):
    id: str = Field(min_length=1, max_length=40)
    text: str = Field("", max_length=200)
    original_text: str = ""
    source: str = ""  # "packages#<rank>:<原文>" 或 "custom"
    published: bool = False  # 已上頻道＝有參加 A/B 測試（修修 2026-11-12：只要一個勾選）
    published_at: AwareDatetime | None = None
    edited_at: AwareDatetime | None = None
    ab: bool = False  # 舊欄位，與 published 同步保留

    def model_post_init(self, _ctx) -> None:  # noqa: D401
        object.__setattr__(self, "ab", self.published)


class TitleTrackingFileV1(BaseModel):
    model_config = ConfigDict(extra="allow")  # 容許原型多記的欄位（primary_title 等）

    video_url: str = ""
    video_id: str | None = None
    video_published_at: AwareDatetime | None = None
    check_after_days: int = 7
    updated_at: AwareDatetime | None = None
    titles: list[TrackedTitleV1] = Field(default_factory=list)

    @field_validator("titles")
    @classmethod
    def _ab_limit(cls, titles: list[TrackedTitleV1]) -> list[TrackedTitleV1]:
        n = sum(1 for t in titles if t.published)
        if n > AB_TITLE_MAX:
            raise ValueError(f"已上頻道（A/B）最多 {AB_TITLE_MAX} 個標題，目前 {n} 個")
        ids = [t.id for t in titles]
        if len(ids) != len(set(ids)):
            raise ValueError("標題 id 重複")
        return titles
