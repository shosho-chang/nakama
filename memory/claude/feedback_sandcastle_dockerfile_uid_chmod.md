---
name: Sandcastle Dockerfile chmod /home/agent 必在所有 RUN install 之後跑
description: 任何在 USER agent 模式下 pip install --user 或 npm install -g 寫進 /home/agent 的檔案，都必須在後面再 chmod -R 0777 一次；否則 Mac UID 501 跑 container 時 transitive dep rewrite permission denied
type: feedback
created: 2026-05-02
updated: 2026-05-02
---

Dockerfile 設計 sandcastle image 時，凡是 image build 階段 `RUN ...pip install --user...` 或 `RUN ...npm install -g...` 寫進 `/home/agent/` 的檔案，都必須在那行 RUN 之後再 `USER root && chmod -R 0777 /home/agent/.local && USER agent`（或對應路徑）。

**Why:** Sandcastle docker run 用 `--user $hostUid:$hostGid`（src/sandboxes/docker.ts），Mac host UID 是 501-503。但 image build 的 USER agent 是 UID 1000（base node:22-bookworm 給 node user）。chmod 0777 只在 image build 那次有效；之後跑 container 時，host UID 501 跟 image UID 1000 不同 → 沒寫權限。

第一次 chmod 可能放在 dev tools pre-install 之前，那麼 pre-install 寫的檔案沒被 chmod 過 → 沒 0777 → host UID 501 跑時 readability-lxml 想 update transitive dep `/home/agent/.local/lib/python3.11/site-packages/sgmllib.py` 寫不進去 → `pip install -r requirements.txt -q` 整體 permission denied。

**How to apply:**
- Dockerfile 任何 `USER agent` 後面的 `RUN pip install --user ...` 或 `RUN npm install -g ...` 之後，必加 `USER root && chmod -R 0777 <touched-path> && USER agent`
- 受影響路徑常見：`/home/agent/.local`、`/usr/local/lib/node_modules`（npm global 走 root 安裝就不影響 user）、`/home/agent/.npm`（npm cache）
- 驗證：`docker run --rm --entrypoint ls sandcastle:nakama -la /home/agent/.local/lib/python3.11/site-packages/` 看 entries 是不是全 `drwxrwxrwx`（0777）
- Linux 桌機 UID 1000 dodges this；Windows Docker Desktop 也 dodges；Mac UID 501 一定踩到

教訓 PR：#318（chmod after pre-install fix） / #319（v3 加 xmllint + tsc pre-install）。
