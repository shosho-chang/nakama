---
name: Gateway 卡住診斷：py-spy dump
description: Slack Bolt handler 卡住或靜默失敗時，py-spy dump 看 thread stack 最快定位
type: feedback
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
當 Nami 或其他 gateway handler「收到訊息但沒回應」、log 沒 error 時，Bolt 可能靜默吃了 exception 或 handler 卡在 I/O。

## 快速診斷

```bash
# 1. 確認 process PID（systemctl 可看）
ps aux | grep nakama-gateway

# 2. dump 所有 thread stack
pip install py-spy
py-spy dump --pid <PID>

# 3. 看 thread 都在等什麼
#    - MainThread 在 wait → 正常（主 loop 等事件）
#    - Thread-1 在 recv/SSL → 正常（Slack socket 監聽）
#    - ThreadPoolExecutor 都 idle → handler 已經返回（可能 crash 靜默）
#    - 有 thread 卡在特定函式 → 該函式是瓶頸
```

## 搭配 log 過濾

```bash
# 只看非 INFO
journalctl -u nakama-gateway --since "5 minutes ago" | grep -vE "INFO|systemd"

# 找 Bolt 靜默吃的 exception
journalctl -u nakama-gateway | grep -iE "error|exception|traceback|slack_bolt"
```

**Why**：Slack Bolt handler 拋 exception 時不會自動 log 到 systemd（取決於 logger 設定），py-spy 是唯一能直接看現場的工具。

**How to apply**：Gateway/agent 出現「log 有收到訊息但沒後續」，先 py-spy dump，再看 log。
