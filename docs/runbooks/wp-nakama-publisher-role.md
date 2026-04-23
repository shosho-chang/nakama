# Runbook — `nakama_publisher` 自訂 WP Role

**目的**：ADR-005b §7 規定 bot 帳號用最小權限自訂 role，不直接套用 Editor。避免 `.env` 外流即全站掉。

**適用**：`bot_usopp` on shosho.tw、未來 `bot_chopper` on fleet.shosho.tw

---

## 能做

| Capability | 原因 |
|---|---|
| `read` | 必要 |
| `edit_posts`、`edit_published_posts`、`edit_others_posts` | 建/改 post |
| `publish_posts` | 切 draft → publish |
| `delete_posts`、`delete_published_posts`、`delete_others_posts` | 必要時可移除（錯發立即回收） |
| `upload_files` | featured image / inline media |
| `manage_categories` | 建 category（**被 ADR-005b §6 禁止使用**，但若運維手動 reset 會要回） |
| `edit_categories`、`edit_tags` | 同上 |
| `litespeed_manage_options` | LiteSpeed purge REST endpoint auth（若 Day 1 實測走方案 A） |

## 明確禁止

| Capability | 為何禁止 |
|---|---|
| `edit_users`、`create_users`、`delete_users`、`promote_users` | 不允許動其他使用者 |
| `manage_options` | 全站 setting 動不得 |
| `install_plugins`、`activate_plugins`、`delete_plugins`、`update_plugins` | plugin 動不得 |
| `install_themes`、`switch_themes`、`delete_themes`、`update_themes` | theme 動不得 |
| `unfiltered_html` | ADR-005b §7：ADR-005a AST 白名單已安全，不需要這 cap。若 Brook 產出破了白名單，是 schema 層該修，不是靠 WP 放行 |
| `edit_files` | 絕對禁止 — 能讀寫 PHP = 全站淪陷 |
| `export`、`import` | 大量資料搬移要手動 |

---

## 建立方式

### 選項 A — plugin（推薦，可版控）

建 `/wp-content/mu-plugins/nakama-publisher-role.php`：

```php
<?php
/**
 * Plugin Name: Nakama Publisher Role
 * Description: Register nakama_publisher role with whitelisted capabilities.
 * Version:     0.1.0
 */

add_action('init', function () {
    if (get_role('nakama_publisher') !== null) {
        return;
    }
    $caps = [
        'read'                     => true,
        'edit_posts'               => true,
        'edit_published_posts'     => true,
        'edit_others_posts'        => true,
        'publish_posts'            => true,
        'delete_posts'             => true,
        'delete_published_posts'   => true,
        'delete_others_posts'      => true,
        'upload_files'             => true,
        'manage_categories'        => true,
        'edit_categories'          => true,
        'edit_tags'                => true,
        'litespeed_manage_options' => true,
    ];
    add_role('nakama_publisher', 'Nakama Publisher', $caps);
});
```

`mu-plugins/` 目錄下的 PHP 檔案會自動載入、不能被 wp-admin 停用（必要安全特性）。

### 選項 B — functions.php（不推薦，失蹤容易）

把上面 `add_action` 貼到當前 theme 的 `functions.php`。缺點：切 theme 就消失、任何 child theme 重建都要記得保留。

---

## `register_post_meta` snippet（必備）

ADR-005b §2 兩層 idempotency 需要 WP 這邊打開 REST meta 查詢：

```php
add_action('init', function () {
    register_post_meta('post', 'nakama_draft_id', [
        'show_in_rest'   => true,
        'single'         => true,
        'type'           => 'string',
        'sanitize_callback' => 'sanitize_text_field',
        'auth_callback'  => function () { return current_user_can('edit_posts'); },
    ]);
});
```

沒有這段，`wordpress_client.find_by_meta()` 會靜默回空陣列，雙層防護退化為單層 → advisory lock 崩潰時會產生重複文章。

---

## 驗證 checklist（VPS 部署後）

- [ ] wp-admin → Users → `bot_usopp` → profile
- [ ] Role 欄位：`Nakama Publisher`（若仍是 Editor，手動切換一次）
- [ ] `curl -u bot_usopp:<app_pass> https://shosho.tw/wp-json/wp/v2/users/me` 回 200 + `roles: ["nakama_publisher"]`
- [ ] 手動發 draft → publish → 刪除流程在 staging 測過一次

---

## 相關

- ADR：[ADR-005b §7](../decisions/ADR-005b-usopp-wp-publishing.md)
- Password 輪換：[rotate-wp-app-password.md](rotate-wp-app-password.md)
- LiteSpeed purge：[litespeed-purge.md](litespeed-purge.md)（`litespeed_manage_options` cap 的用途）
