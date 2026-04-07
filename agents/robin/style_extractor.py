"""分析 Owner 既有文章，萃取寫作風格 profile。"""

import json
from pathlib import Path

from shared.anthropic_client import ask_claude
from shared.config import get_agent_config
from shared.log import get_logger
from shared.obsidian_writer import list_files
from shared.utils import read_text

logger = get_logger("nakama.robin.style")

_ROOT = Path(__file__).resolve().parent.parent.parent


def extract_style_profile(articles_dir: str = "KB/Raw/Articles") -> dict:
    """從 Owner 的文章中萃取寫作風格，存為 style-profile.json。

    Args:
        articles_dir: vault 內文章資料夾的相對路徑
    """
    files = list_files(articles_dir)
    if not files:
        logger.warning("找不到文章檔案，無法萃取風格")
        return {}

    # 取最多 5 篇文章作為樣本
    samples = []
    for f in files[:5]:
        content = read_text(f)
        samples.append(f"--- {f.name} ---\n{content[:3000]}")

    combined = "\n\n".join(samples)

    prompt = f"""分析以下文章的寫作風格，產出一份 JSON 格式的風格 profile。

分析維度：
1. tone（語調）：正式/半正式/口語
2. sentence_length（句子長度偏好）：短句為主/中等/長句為主
3. vocabulary_level（詞彙程度）：通俗/中等/專業
4. structure_preference（結構偏好）：列點/段落/混合
5. use_of_examples（舉例頻率）：少/中/多
6. personal_voice（個人風格特徵）：描述 2-3 個顯著特徵
7. common_phrases（常用語句/口頭禪）：列出 5-10 個
8. paragraph_length（段落長度）：短/中/長
9. use_of_data（引用數據偏好）：少/中/多
10. target_audience（目標讀者）：描述

回傳純 JSON，不要包含其他文字。

文章內容：

{combined}"""

    response = ask_claude(prompt, temperature=0.2)

    try:
        # 提取 JSON
        import re
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            profile = json.loads(json_match.group())
        else:
            logger.warning("未能解析風格 profile")
            return {}
    except json.JSONDecodeError:
        logger.warning("JSON 解析失敗")
        return {}

    # 儲存到 config/style-profile.json
    config = get_agent_config("robin")
    output_path = _ROOT / config.get("style_profile", "config/style-profile.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"已儲存風格 profile：{output_path}")
    return profile
