從以下使用者訊息提取 LifeOS Project 建立意圖的欄位，回傳 JSON。

欄位說明：
- title: 專案主題（必要；繁體中文；去掉「建立」「開個」「幫我」等指令詞）
- content_type: "youtube" / "blog" / "research" / "podcast" 其一，若無線索留 null
- area: "work" / "health" / "family" / "self-growth" / "play" / "visibility"，若無線索留 null
- priority: "first" / "high" / "medium" / "low"，若無線索留 null
- search_topic: SEO 關鍵字（只有 youtube / blog 需要），若無線索留 null

content_type 判斷：
- "影片 / YouTube / 拍 / 短片 / vlog" → youtube
- "部落格 / blog / 文章 / 長文 / SEO" → blog
- "研究 / 論文 / literature / 深入研究 / 搞懂" → research
- "podcast / 錄音 / 訪談 / 來賓" → podcast
- 主題是抽象概念（「X 是什麼」「X 的原理」）且未指定格式 → research
- 其他曖昧情況 → null（後續會問使用者）

area 判斷：
- 身心健康、睡眠、營養、運動 → health
- 家庭、親子、伴侶 → family
- 學習、技能、語言 → self-growth
- 娛樂、興趣 → play
- 社群、品牌、公開 → visibility
- 工作、專業內容創作 → work（內容創作常見；沒有明顯其他指向時的 fallback null，由 handler 決定）

範例：

使用者：幫我建立一個關於超加工食品到底是什麼，以及它對人體影響的 project
→ {{"title": "超加工食品", "content_type": "research", "area": "health", "priority": null, "search_topic": null}}

使用者：開個腸道菌的 YouTube project，優先級高一點
→ {{"title": "腸道菌", "content_type": "youtube", "area": "health", "priority": "high", "search_topic": "腸道菌"}}

使用者：建個「深度工作」的部落格專案
→ {{"title": "深度工作", "content_type": "blog", "area": "work", "priority": null, "search_topic": "深度工作"}}

使用者：新專案：台灣長照
→ {{"title": "台灣長照", "content_type": null, "area": "health", "priority": null, "search_topic": null}}

---

使用者訊息：{user_message}

只回覆 JSON，不要其他文字。
