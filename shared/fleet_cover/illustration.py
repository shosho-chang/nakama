"""用 OpenAI 圖片編輯 API 生一張封面插圖（無文字、透明背景）。

封面右側沒有書封可放時，用這張手繪風插圖當主圖。風格靠「參考圖」鎖住：把
`assets/illustration_style_ref.jpg`（品牌 banner）當 `image[]` 一起送進
`/v1/images/edits`，prompt 再明講只學右側人物的筆觸、不准抄場景、不准出現任何字。

`topic` 是呼叫端寫好的英文場景描述（一句話），這裡只負責接上固定的 STYLE 段落。
回傳 PNG bytes；任何 HTTP 錯誤／逾時／回應格式不對都包成 `IllustrationError`，
訊息只帶 status 與 body 前 300 字，絕不記錄 API key。
"""

from __future__ import annotations

import base64
import binascii
import os

import httpx

from shared.fleet_cover.fonts import ASSETS_DIR
from shared.log import get_logger

logger = get_logger("nakama.shared.fleet_cover.illustration")

OPENAI_IMAGES_EDITS_URL = "https://api.openai.com/v1/images/edits"
MODEL = "chatgpt-image-latest"
STYLE_REF_PATH = ASSETS_DIR / "illustration_style_ref.jpg"
_ERROR_BODY_CHARS = 300

STYLE = (
    "Match the illustration style of the hand-drawn character on the right side of the reference "
    "banner: thick slightly rough brand-blue outlines (#2C6C9B, #1E4E79), flat fills only in pale "
    "blue (#E6F1F9) and warm off-white (#F5F1E8), friendly simple faces, loose hand-drawn feel. "
    "Do NOT copy the reference scene, characters, waves, sparkles or any text. Absolutely no text, "
    "letters, numbers or logos anywhere. Single centered spot illustration, generous padding, "
    "transparent background."
)


class IllustrationError(RuntimeError):
    """插圖生成失敗（缺 key、HTTP 錯誤、逾時、回應格式不對）。"""


def build_prompt(topic: str) -> str:
    return f"{topic.strip()}\n\n{STYLE}"


def generate_illustration(
    topic: str, *, api_key: str | None = None, timeout_s: float = 180
) -> bytes:
    """依 `topic`（英文場景描述）生一張透明背景插圖，回傳 PNG bytes。"""
    if not topic or not topic.strip():
        raise ValueError("topic 不可為空")
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise IllustrationError("缺 OpenAI API key：請傳 api_key 或設定 env OPENAI_API_KEY")

    data = {
        "model": MODEL,
        "size": "1024x1024",
        "quality": "medium",
        "background": "transparent",
        "output_format": "png",
        "n": "1",
        "prompt": build_prompt(topic),
    }
    files = [("image[]", (STYLE_REF_PATH.name, STYLE_REF_PATH.read_bytes(), "image/jpeg"))]

    try:
        resp = httpx.post(
            OPENAI_IMAGES_EDITS_URL,
            headers={"Authorization": f"Bearer {key}"},
            data=data,
            files=files,
            timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise IllustrationError(f"OpenAI images/edits 逾時（{timeout_s}s）") from exc
    except httpx.HTTPError as exc:
        raise IllustrationError(f"OpenAI images/edits 連線失敗：{type(exc).__name__}") from exc

    if resp.status_code >= 400:
        body = resp.text[:_ERROR_BODY_CHARS]
        logger.warning("OpenAI images/edits HTTP %s：%s", resp.status_code, body)
        raise IllustrationError(f"OpenAI images/edits HTTP {resp.status_code}：{body}")

    try:
        b64 = resp.json()["data"][0]["b64_json"]
        png = base64.b64decode(b64, validate=True)
    except (ValueError, KeyError, IndexError, TypeError, binascii.Error) as exc:
        raise IllustrationError(
            f"OpenAI images/edits 回應格式不對：{resp.text[:_ERROR_BODY_CHARS]}"
        ) from exc
    if not png:
        raise IllustrationError("OpenAI images/edits 回傳空圖")
    return png
