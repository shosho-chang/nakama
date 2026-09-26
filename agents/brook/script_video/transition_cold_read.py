"""轉場卡冷讀回收測試——不靠人眼判斷「只看這張卡，知不知道這節在講什麼」。

修修 2026-09-08 訂下滿版轉場卡的唯一驗收標準：「只要看這個 transition 的字卡，
就可以知道這個小節大概在講什麼。」那一集六張卡全部沒頭沒尾，而唯一發現的方式
是他自己看完 11 分鐘的 timeline。

這支把那個標準變成可執行的測試，作法照 `title-brainstorm` 的 PANEL 慣例——
**生成器 ≠ 評審**：

1. **盲讀**：一個隔離的 worker 只拿到卡片文字（不給逐字稿、不給時間、不給
   `summary`），回答每張卡「這一節在講什麼」。
2. **回收判定**：第二個隔離的 worker 只拿到（盲讀答案, canonical summary）配對，
   **看不到卡片文字**，判斷兩者講的是不是同一件事。不給卡片是為了避免它回頭
   照著卡片合理化盲讀的答案。
3. **確定性 gate**：任何一張回收失敗或盲讀時就自承讀不完整，整批不通過。

兩次呼叫都走 `ask_via_cli`，每次都是全新 subprocess，彼此與呼叫端沒有共享脈絡——
這是「冷讀」成立的前提。`ask` 可注入，測試用假的。
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from shared.llm_json import extract_json_object

__all__ = ["ColdReadCard", "ColdReadReport", "ColdReadSection", "cold_read_sections"]

AskFn = Callable[[str], str]

_BLIND_SYSTEM = (
    "你是一個沒看過這集節目的觀眾。你只會看到幾張影片裡的章節卡片文字，"
    "沒有其他任何資訊。誠實回答你從卡片上讀到什麼，不要腦補、不要假裝看得懂。"
)

_BLIND_PROMPT = """下面是一支影片裡的幾張章節卡片。每張卡片會滿版蓋住畫面約三秒，
觀眾在那三秒裡只看得到這行字。

{cards}

針對每一張卡片回答：

- `guess`：只看這行字，你認為這一小節在講什麼？用一句話說。真的看不出來就寫「看不出來」。
- `self_contained`：這行字**找不找得到它在講誰**？true / false。只有卡片指向一個你無從辨認的
  對象時才填 false——「她不是你爸」不知道「她」是誰、「不是牽拖」不知道什麼不是牽拖。
  中文常態省略（祈使句的「你」、泛指句沒有指名主詞）**一律算 true**。
- `missing`：填 false 時說明缺什麼，只能填 "主詞" / "結論" / "指涉不明"；true 就填 "none"

只輸出 JSON，格式：

{{"cards": [{{"index": 1, "guess": "...", "self_contained": true, "missing": "none"}}]}}"""

_RECOVERY_SYSTEM = (
    "你在比對兩段文字講的是不是同一件事。你看不到原始素材，只做語意比對，"
    "不要替任何一邊補完或善意解讀。"
)

_RECOVERY_PROMPT = """下面每一組有兩段話。A 是某個人只看了一張章節卡片之後、猜這一小節在講什麼；
B 是這一小節實際完成的論點。

{pairs}

針對每一組回答：

- `recovered`：A 有沒有抓到 B 的**主要論點**？true / false

  **判準是主要論點，不是涵蓋率。** A 來自一張只有十幾個字的卡片，B 是一整段幾分鐘內容的
  摘要，裡面必然有卡片載不動的例子、鋪陳、與下一段的接口。**A 漏掉這些支線不算落空。**
  只有在下面兩種情況才填 false：

  - A 講的方向跟 B 的主要論點**不同或相反**（例如 B 說「現在的人串著過去的人」，A 說
    「上位者的情緒向下傳染」）。
  - A **完全沒抓到** B 在講什麼（例如 B 在講完美主義式的拖延，A 只複述一個金額）。

- `why`：填 false 時說明 A 是誤解了方向還是完全沒抓到，一句話。true 就寫 "none"。

只輸出 JSON，格式：

{{"pairs": [{{"index": 1, "recovered": true, "why": "none"}}]}}"""


@dataclass(frozen=True, slots=True)
class ColdReadSection:
    section_id: str
    summary: str
    transition_title: str


@dataclass(frozen=True, slots=True)
class ColdReadCard:
    section_id: str
    transition_title: str
    summary: str
    guess: str
    self_contained: bool
    missing: str
    recovered: bool
    why: str

    @property
    def passed(self) -> bool:
        """兩個判準：回收得到主要論點，而且卡片上找得到它在講誰。

        兩者都必要，缺一就跟修修的判定對不上。2026-09-08 拿蘇予昕 punch-L04 的
        舊版（修修判 4 張不及格）與定版（判 6 張都可以）實測：

        - 只看回收 → 舊版放行「不是牽拖，是第一個線索」「修修：她不是你爸」，
          正是修修點名沒頭沒尾的那兩張。冷讀者猜得方向對，但那是猜的不是讀的。
        - 只看 `self_contained` → 放行「情緒像粽子，主管底下是一整串」（讀得完整
          但論點錯成「上位者情緒向下傳染」）與「修修：設備花了一百萬」。

        兩個一起用，12 項判定與修修全中。`self_contained` 的措辭要維持「找不找得到
        它在講誰」的窄義——放寬成「主詞在不在」會把中文常態省略（祈使句的「你」）
        誤殺，那一版擋掉了修修親自認可的卡。
        """
        return self.recovered and self.self_contained


@dataclass(frozen=True, slots=True)
class ColdReadReport:
    cards: tuple[ColdReadCard, ...]

    @property
    def passed(self) -> bool:
        return all(card.passed for card in self.cards)

    @property
    def failures(self) -> tuple[ColdReadCard, ...]:
        return tuple(card for card in self.cards if not card.passed)


def _numbered(rows: Sequence[str]) -> str:
    return "\n".join(f"{index}. {row}" for index, row in enumerate(rows, start=1))


def _by_index(payload: dict, key: str, expected: int) -> dict[int, dict]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"冷讀 worker 沒有回傳 {key!r} 陣列")
    parsed: dict[int, dict] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("index"), int):
            parsed[row["index"]] = row
    missing = [index for index in range(1, expected + 1) if index not in parsed]
    if missing:
        raise ValueError(f"冷讀 worker 漏答第 {missing} 張")
    return parsed


def cold_read_sections(
    sections: Sequence[ColdReadSection],
    ask: AskFn,
    *,
    seed: int | None = None,
) -> ColdReadReport:
    """對一組轉場卡跑盲讀 → 回收判定，回傳逐張結果。

    卡片會先打亂再送進盲讀：照時間順序排列會讓評審靠前後文推理，那就不是冷讀了。
    """
    if not sections:
        return ColdReadReport(())

    order = list(range(len(sections)))
    random.Random(seed).shuffle(order)
    shuffled = [sections[index] for index in order]

    blind = _by_index(
        extract_json_object(
            ask(_BLIND_PROMPT.format(cards=_numbered([s.transition_title for s in shuffled])))
        ),
        "cards",
        len(shuffled),
    )

    pairs = _numbered(
        [
            f"A：{blind[index]['guess']}\n   B：{shuffled[index - 1].summary}"
            for index in range(1, len(shuffled) + 1)
        ]
    )
    recovery = _by_index(
        extract_json_object(ask(_RECOVERY_PROMPT.format(pairs=pairs))),
        "pairs",
        len(shuffled),
    )

    cards = [
        ColdReadCard(
            section_id=section.section_id,
            transition_title=section.transition_title,
            summary=section.summary,
            guess=str(blind[index].get("guess", "")),
            self_contained=bool(blind[index].get("self_contained")),
            missing=str(blind[index].get("missing", "none")),
            recovered=bool(recovery[index].get("recovered")),
            why=str(recovery[index].get("why", "none")),
        )
        for index, section in enumerate(shuffled, start=1)
    ]
    # 回到原本的 section 順序，讓報告讀起來跟片子一致。
    by_id = {card.section_id: card for card in cards}
    return ColdReadReport(tuple(by_id[section.section_id] for section in sections))


def blind_system_prompt() -> str:
    return _BLIND_SYSTEM


def recovery_system_prompt() -> str:
    return _RECOVERY_SYSTEM


def report_as_json(report: ColdReadReport) -> str:
    return json.dumps(
        {
            "passed": report.passed,
            "cards": [
                {
                    "section_id": card.section_id,
                    "transition_title": card.transition_title,
                    "guess": card.guess,
                    "self_contained": card.self_contained,
                    "missing": card.missing,
                    "recovered": card.recovered,
                    "why": card.why,
                    "passed": card.passed,
                }
                for card in report.cards
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
