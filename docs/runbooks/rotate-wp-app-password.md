# Runbook — WP Application Password 輪換

**週期**：每 90 天輪換一次（ADR-005b §7）
**原則**：舊 password 留 30 天緩衝，新 password 驗證通過才刪舊的

---

## 前置

- VPS SSH 可登入（`nakama-vps` alias）
- shosho.tw wp-admin 登入權限（`bot_usopp` 的 owner 帳號）
- `.env` 權限 0600、owner `nakama`（ADR-005b §7）

---

## 步驟（shosho.tw）

### 1. wp-admin 建新 password

- [ ] 登入 `https://shosho.tw/<wps-hide-login 路徑>/wp-admin`
- [ ] Users → `bot_usopp` → profile
- [ ] Application Passwords 區塊 → 新增
  - Name: `nakama-usopp-publisher-<YYYYMMDD>`（e.g. `nakama-usopp-publisher-20260723`）
  - **不要刪舊的**；保留 30 天並列
- [ ] 複製新 password（只顯示一次，丟到暫存檔即可）

### 2. VPS .env 換人

```bash
ssh nakama-vps
cd /home/nakama/
cp .env .env.bak-$(date +%Y%m%d)
# 編輯 .env：把 WP_SHOSHO_APP_PASSWORD=<舊> 改成新的 24 字母 password
nano .env
# 驗證 .env 權限
stat -c '%a %U %G' .env    # 必須是 600 nakama nakama
```

### 3. 重啟兩個 service（`feedback_vps_two_services.md`）

```bash
systemctl restart thousand-sunny
systemctl restart nakama-gateway
systemctl status thousand-sunny nakama-gateway    # 確認 active (running)
```

### 4. 健康檢查驗證

```bash
curl -s https://nakama.shosho.tw/healthz | jq
# 期待 WP 連線欄位 status=ok
```

若 `/healthz` 的 `wp_shosho` 欄位回 `ok`，新 password 生效。

### 5. 30 天後：wp-admin 刪舊 password

- [ ] 回到 Application Passwords 清單
- [ ] 找到上一版 `nakama-usopp-publisher-<舊日期>`
- [ ] 按 Revoke

---

## 洩漏情境（緊急）

若懷疑 password 洩漏：

1. wp-admin → Revoke 舊 password（立即生效）
2. 建新 password（步驟 1）
3. VPS .env 換人（步驟 2–3）
4. 檢查 WP audit log（`/wp-admin` → 若有 Fluent Security audit plugin）看最近 `bot_usopp` 的動作
5. 把事件寫進 [Case Studies](../../KB/Case%20Studies/) 紀錄

---

## fleet.shosho.tw

同步驟，把 `WP_SHOSHO_*` 換成 `WP_FLEET_*`，user 換成 `bot_chopper`。Phase 3 Chopper 上線後才會用到，Phase 1 仍建議每 90 天輪換避免長期 password 放著。

---

## 相關

- ADR：[ADR-005b §7](../decisions/ADR-005b-usopp-wp-publishing.md)
- 兩 service 部署：[feedback_vps_two_services.md](../../memory/claude/feedback_vps_two_services.md)
- 帳號建立：[setup-wp-integration-credentials.md](setup-wp-integration-credentials.md)
