# 自建 persona 指南

## 來源優先序

從**真實資料**長出來：訪談原話 > 策略文件的受眾定義 > 委託人口述 > 憑空想像。
最後一種**禁止單獨使用**——persona 越貼真實資料，回饋越有效度。

## 五要素（缺一即打回）

1. **一句身分**：年齡／職業／處境。
2. **3–6 條行為特質**：附資料來源（如「來自訪談：買過兩堂 AI 課因術語太多放棄」）。
3. **判準**：這個人買不買／留不留的那一句話（如蔡醫師＝「要花我多少時間、多久看到產出」）。沒有判準的 persona 只會給廢話讚美。
4. **流失點**：什麼會讓他關掉頁面。
5. **他最想聽到的一句話**。

## 組 set 的原則

- 三個 persona 要**互相拉扯**（一個怕難、一個算時間、一個帶資產）——他們的矛盾正是收斂的價值。
- Persona 定義裡不要塞「正確答案」（不要寫「你認為 5.6 有矛盾」）；只給人設與敏感點，讓發現自己長出來。
- 可選配一個 `role: lens` 的品牌/craft 視角（見 [brand-lens.md](brand-lens.md)）——它不是受眾，是守規格的檢查員。

## 檔案格式

每個 persona 一個 `.md`，frontmatter：

```yaml
---
name: 小資Kevin
set: finance-audience        # SKILL.md 用 set 名選一整組
role: persona                # persona | lens
status: draft                # draft | frozen；未經修修認可不得標 frozen
source: 2026-07-05 IG deck 實跑
---
```

Body 寫五要素全文。`status: draft` → 改動自由；`frozen` → 改動走 review。
