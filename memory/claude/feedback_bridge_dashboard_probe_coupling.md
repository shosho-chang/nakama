---
name: Bridge /franky 的 _PROBE_TARGETS tuple 跟 .probe-row2 CSS grid 耦合
description: 加 probe 要同步改 tuple + template grid col 數，不只改 Python 那邊
type: feedback
originSessionId: 64ccfe1b-b7a7-4f86-8964-5a458e6eba6f
---
加新 probe 到 Bridge `/bridge/franky` dashboard 時，**不是只改 `_PROBE_TARGETS` tuple 就好**。Layout 有隱性耦合：

- `thousand_sunny/routers/franky.py:101` `_PROBE_TARGETS` 決定 `probe_cards` 陣列長度
- Template [`franky.html:383-430`](thousand_sunny/templates/bridge/franky.html) 硬編碼 `probe_cards[0]` (hero) / `probe_cards[1]` (vps) / `probe_cards[2:]` (row2 iteration)
- `.probe-row2 { grid-template-columns: ... }` 硬編碼欄數 — 當前是 4 欄（wp×2 + nakama-backup + xcloud-backup standalone card）
- Row2 迴圈加 1 個 probe → grid 要加 1 欄，否則 overflow

**Why:** 2026-04-24 PR #100 我一開始以為「加 tuple 一行就好」，實際發現 `.probe-row2` 原本是 3-col（wp×2 + xcloud 單張 card），新 probe 加進迴圈會變 4 卡擠 3 欄。

**How to apply:** 改 `_PROBE_TARGETS` 時同步檢查：
1. Template 的 probe_cards index 範圍（hero / vps / row2 loop）
2. `.probe-row2` grid-template-columns 欄數
3. row2 末尾是否有 standalone 非-probe card（目前 xcloud R2 summary 就是一張）
4. Mobile 斷點 `.probe-row2 { grid-template-columns: 1fr }` 還可以，不用動
