---
name: 修修截圖固定放 G:/OneDrive/Pictures/螢幕擷取畫面
description: 修修操作結果常用截圖回報；新截圖永遠落這個資料夾，檔名 `Screenshot YYYY-MM-DD HHMMSS.png`。要看時用 `ls -t` 排最新
type: reference
---

修修所有 Windows 截圖（Win+Shift+S / Snipping Tool / PrintScreen）落點：

```
G:\OneDrive\Pictures\螢幕擷取畫面\
```

檔名格式 `Screenshot YYYY-MM-DD HHMMSS.png`（年月日 + 時分秒，**ISO order**所以 ls 自動時間序）。

## 使用模式

修修說「我把截圖放好了」/「看截圖」/「screenshot 給你」時：

```bash
ls -t "G:/OneDrive/Pictures/螢幕擷取畫面/" | head -3
# 抓最新那張，Read tool 直接吃 PNG（multimodal）
```

不要問「在哪」— 路徑固定的，省去回合。如果列出來最新的時間戳跟你以為的 context 對不上（例如修修剛說「DaVinci import 成功」但最新截圖時間是 2 小時前），那才回問是不是預期的那張。

## 跟其他工作流的關係

- `transcribe` skill 的音檔輸入跟這資料夾無關（音檔放 OneDrive 別處）
- 修修平常用 Obsidian 內嵌截圖（KB/Attachments/）跟這資料夾也無關 — 這只是「給 Claude 看的暫時截圖」
- 不要 commit 截圖檔本身到 repo；引用時用絕對路徑 `G:\...` 給 Claude 看就好
