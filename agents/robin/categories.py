"""Robin 內容性質分類定義 — 決定 ingest 策略（prompt 選擇）。

兩層分類架構：
- Layer 1: source_type（媒體格式）→ 決定存放位置
- Layer 2: content_nature（內容性質）→ 決定 ingest prompt
"""

CONTENT_NATURES: dict[str, dict[str, str]] = {
    "research": {
        "label": "研究文獻",
        "description": "原始研究、系統回顧、統合分析",
    },
    "popular_science": {
        "label": "科普讀物",
        "description": "科普書、健康書、深度報導",
    },
    "textbook": {
        "label": "教科書",
        "description": "教科書、參考書、專業手冊",
    },
    "clinical_protocol": {
        "label": "臨床指引",
        "description": "治療方案、生物駭客 Protocol、劑量建議",
    },
    "narrative": {
        "label": "敘事經驗",
        "description": "回憶錄、自傳、自我實驗報告",
    },
    "commentary": {
        "label": "評論觀點",
        "description": "部落格、訪談逐字稿、Podcast、觀點文",
    },
}

DEFAULT_CONTENT_NATURE = "popular_science"
