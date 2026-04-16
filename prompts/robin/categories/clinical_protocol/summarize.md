你的任務是閱讀以下臨床指引或治療方案，並產出結構化的 Source Summary。

## 臨床指引摘要規範

此來源為臨床指引、治療方案、或生物駭客 Protocol，內容高度可操作。請著重於具體步驟和數值。

1. **適用對象（Target Population）**：
   - 此方案適用於誰（年齡、性別、健康狀態）
   - 納入條件與排除條件
   - 有無特殊族群考量（孕婦、老年人、特定疾病患者）
2. **方案摘要（Protocol Summary）**：以 step-by-step 清單呈現完整流程
3. **劑量與時序（Dosage & Timing）**：
   - 具體劑量（mg、g、IU 等）
   - 頻率（每日/每週/每月）
   - 持續時間
   - 漸進或調整規則
4. **禁忌與注意事項（Contraindications & Precautions）**：
   - 絕對禁忌
   - 相對禁忌
   - 藥物交互作用
   - 可能的副作用
5. **證據等級（Evidence Grade）**：
   - 每項建議的證據強度（Grade A/B/C/D 或 strong/moderate/weak）
   - 發布機構與年份
   - 是否有更新版本
6. **相關概念 / 實體（Related Concepts & Entities）**：使用 `[[]]` 格式
7. **監測指標（Monitoring & Outcomes）**：
   - 執行期間應追蹤的指標
   - 何時該調整或停止
   - 預期成效與時間框架

## 信心等級判定

根據建議的證據等級：
- **high**：Grade A / strong recommendation，有多個 RCT 支持
- **medium**：Grade B / moderate，有部分研究支持
- **low**：Grade C-D / weak / expert consensus / 民間方案無正式研究

## 格式

{writing_style}

## 來源資訊

- 標題：{title}
- 作者：{author}
- 類型：{source_type}
- 日期：{date}

## 使用者標註

此來源可能包含使用者標記的重點和註解：
- `==被標記的文字==` 表示使用者認為重要的段落
- `> [!annotation] 被標記的文字` 後續的 `>` 行是使用者的個人註解

如果來源中包含這些標記，請優先處理被標記的段落。

## 來源內容

{content}
