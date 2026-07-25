"""Pydantic models for foundry storyboard.yaml (ADR-032 §6).

extra="forbid" on every model per ADR-019 lesson.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Timing(BaseModel):
    model_config = {"extra": "forbid"}

    start: float
    duration: float


class Transitions(BaseModel):
    model_config = {"extra": "forbid"}

    in_transition: str | None = None
    out_transition: str | None = None


class AssetCandidate(BaseModel):
    """外部素材候選（ADR-051 D5：首選＋備選，修修審核時圈選）."""

    model_config = {"extra": "forbid"}

    url: str
    note: str | None = None


class AssetSpec(BaseModel):
    """外部素材 beat 的來源與出處（ADR-051 D5/D6/D8）.

    ``path`` 在素材取得（下載交接驗收 / 修修外供）後才填，episode 目錄
    相對路徑；render dispatcher 對 asset beat 只驗檔案存在，emit 直接引用。
    """

    model_config = {"extra": "forbid"}

    kind: Literal["stock", "kol", "screen_recording", "supplied"]
    path: str | None = None
    # 檔案 SHA-256（下載驗收時填）。防檔案在原路徑被替換後沿用過期的
    # visual_approved / render_status（Codex panel 2026-07-05 指出的 staleness 洞）。
    sha256: str | None = None
    source_url: str | None = None
    # KOL 片段的來源時間區間，"HH:MM:SS-HH:MM:SS"（ADR-051 D6 出處留痕）
    source_span: str | None = None
    # description 出處清單用的一行文字（D6 護欄：asset beat 強制留出處）
    attribution: str | None = None
    candidates: list[AssetCandidate] = Field(default_factory=list)


class BRollSpec(BaseModel):
    model_config = {"extra": "forbid"}

    render_target: Literal["hyperframes", "reader-playwright", "web-playwright", "asset"]
    component: str
    params: dict[str, Any]
    transitions: Transitions
    asset: AssetSpec | None = None

    @model_validator(mode="after")
    def _asset_iff_asset_target(self) -> BRollSpec:
        if self.render_target == "asset" and self.asset is None:
            raise ValueError("render_target='asset' 的 beat 必須帶 broll.asset")
        if self.render_target != "asset" and self.asset is not None:
            raise ValueError("broll.asset 僅限 render_target='asset' 使用")
        return self


class BeatStatus(BaseModel):
    model_config = {"extra": "forbid"}

    text_approved: bool = False
    render_status: Literal["pending", "rendering", "done", "failed"] = "pending"
    visual_approved: bool = False
    # ADR-038 §D2: 16-char sha256 prefix used in `out/b_roll_<cached_hash>.mp4`
    # filename. Filled by render_dispatcher when the beat is queued (or cache-
    # hit) and read by fcpxml_emitter to resolve the rendered mp4. None until
    # the beat has been considered for rendering at least once.
    cached_hash: str | None = None


class UserNote(BaseModel):
    model_config = {"extra": "forbid"}

    timestamp: str
    note: str


class VisualIntent(BaseModel):
    """Director 的「這句要什麼畫面」意圖層（修修 2026-07-17 裁決 A：意圖/實現分離）.

    Director 只填這裡；``broll``（component/params/asset）改由 brook-dp 依意圖落地。
    form/category 詞彙來自四支成片＋Ali/Jeff 對照的剪輯文法研究
    （docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md §六）。

    ``form`` 表達事件形態，其中 overlay/canvas_pip 在對應 layout/composition 落地前
    由 DP 降級為滿版或 none——意圖照實記錄，實現受當前 allowed_layouts 約束。
    """

    model_config = {"extra": "forbid"}

    form: Literal["cutaway", "overlay", "canvas_pip", "aside_marker"]
    category: Literal[
        "stock_scene",  # 畫面感語句 → 實拍（逐名詞、≤3s、只蓋 visual phrase）
        "keyword",  # 抽象概念名詞 → 關鍵字卡
        "person_inset",  # 人名 → 人物照 inset（對比人物雙卡並列）
        "book_cover",  # 書封（滿版或 inset；hook 內 ≥2 次曝光）
        "quote",  # 金句 → 首次唸到即上卡
        "chapter",  # 章節卡（與旁白唸出章節名同步）
        "worked_example",  # 數字/比較/流程 → 實算動畫
        "evidence_doc",  # 研究/文章引用 → 截圖＋黃 highlight
        "self_archive",  # 修修自供素材（vlog/對帳單/照片）
        "self_promo",  # 自家舊影片縮圖導流
        "kol_quote",  # 他人影片引用
        "screen_demo",  # 螢幕操作展示
        "meme",  # 梗圖/影劇梗 inset（版權留意）
        "bigstat",  # 大數字
    ]
    # 給 DP 的一句話 brief：觀眾在這個事件要看到什麼
    description: str
    # 需上畫面的文字（金句原文/關鍵字/章節名），逐字；無則 None
    on_screen_text: str | None = None
    # 快切連發：一個事件展開幾鏡（stock 連發 3-5；預設單鏡）
    shots_hint: int = 1
    # kol/self_archive 的來源線索（頻道名/影片名/檔案位置）
    source_hint: str | None = None


class Beat(BaseModel):
    model_config = {"extra": "forbid"}

    beat_id: int
    start_quote: str
    end_quote: str
    timing: Timing | None = None
    srt_line_ids: list[int] | None = None
    broll_decision: Literal["none", "cutaway"]
    layout: str
    # Director v2 意圖層（選填以保舊 storyboard 相容；cutaway beat 應填）
    visual_intent: VisualIntent | None = None
    broll: BRollSpec | None = None
    status: BeatStatus
    user_notes: list[UserNote]
