"""上架用的章節表——agent 切的，不是從轉場卡推的。

`resolve_chapters` 原本只有三個來源：Release 對應表、核准剪輯登錄的滿版轉場卡、
舊的 `tighten/<cut>_broll.json`。對**完整版**而言三個全都空——完整版沒有對應表、
沒有登錄檔、也沒有 broll 檔，所以 87 分鐘的節目上架時描述裡一個時間戳都沒有
（2026-09-11 20260721 呂冠緯 實測）。長片那邊也不保險：轉場卡少於兩張就回空，
20260721 的 story-L02 與 value-L02 各只有一張。

章節是**語意工作**：要讀逐字稿、判斷話題在哪裡轉、用觀眾看得懂的話命名。所以
這個檔由當下執行的 agent 產（見 `memory/claude/feedback_semantic_work_runs_on_host_agent`），
本模組只負責「什麼樣的章節表算合法」。

合法性照 YouTube 的規則，不是我們自己發明的：首章必須 0:00、至少 3 章、時間遞增、
每章至少 10 秒。違反任何一條，YouTube 會整份忽略而且**不會告訴你**——所以這裡擋，
不要讓它靜靜地不生效。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PUBLISH_CHAPTERS_SCHEMA = "nakama.publish_chapters.v1"

#: YouTube 的硬性下限：章節之間至少 10 秒。
MIN_CHAPTER_GAP_SEC = 10.0
#: YouTube 的硬性下限：至少 3 章（含 0:00）。
MIN_CHAPTER_COUNT = 3


class PublishChapterV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t0: float = Field(ge=0.0)
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        trimmed = " ".join(value.split())
        if not trimmed:
            raise ValueError("章節標題不能是空白")
        return trimmed


class PublishChaptersFileV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_: str = Field(alias="schema")
    episode: str = Field(min_length=1)
    cut_id: str = Field(min_length=1)
    generated_at: datetime
    #: 這份章節是讀哪一份逐字稿切出來的——換了稿就該重切。
    source: str = Field(min_length=1)
    chapters: list[PublishChapterV1]

    @field_validator("schema_")
    @classmethod
    def _exact_schema(cls, value: str) -> str:
        if value != PUBLISH_CHAPTERS_SCHEMA:
            raise ValueError(f"schema 必須是 {PUBLISH_CHAPTERS_SCHEMA}")
        return value

    @model_validator(mode="after")
    def _youtube_rules(self) -> PublishChaptersFileV1:
        rows = self.chapters
        if len(rows) < MIN_CHAPTER_COUNT:
            raise ValueError(f"至少要 {MIN_CHAPTER_COUNT} 章，YouTube 少於這個數字整份不生效")
        if rows[0].t0 != 0.0:
            raise ValueError("首章必須是 0:00，否則 YouTube 整份忽略")
        for earlier, later in zip(rows, rows[1:]):
            if later.t0 - earlier.t0 < MIN_CHAPTER_GAP_SEC:
                raise ValueError(
                    f"「{earlier.title}」與「{later.title}」相距 "
                    f"{later.t0 - earlier.t0:.1f}s，不足 {MIN_CHAPTER_GAP_SEC:.0f}s"
                )
        return self

    def as_pairs(self) -> list[tuple[float, str]]:
        """`resolve_chapters` 要的形狀。"""
        return [(row.t0, row.title) for row in self.chapters]
