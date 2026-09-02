<?php
/**
 * Plugin Name:       Fleet Gamification
 * Description:       自由艦隊自研遊戲化系統（笨層）：hook 捕捉、append-only ledger、窄 REST API。規則與判定住在 nakama/Sanji（聰明層），本 plugin 不含任何規則邏輯。
 * Version:           0.1.0
 * Requires at least: 6.4
 * Requires PHP:      8.1
 * Author:            Nakama
 * Text Domain:       fleet-gamification
 *
 * 設計文件：nakama repo `agents/sanji/CONTEXT.md`（裁決紀錄）、
 * `docs/plans/fleet-gamification-master-plan.md`（營運方案）。
 *
 * 鐵則（違反即 review 打回）：
 *  1. 本 plugin 永不實作規則邏輯（分數表、streak、判定）——那是 nakama 的職責。
 *  2. ledger（grants 表）append-only：永不 UPDATE/DELETE 歷史，改錯帳走沖正事件。
 *  3. 對 FluentCommunity 的一切依賴集中在 FcBridge 一個 class——contract probe 的驗證面。
 *  4. FluentCommunity 的 class/function 只在 runtime handler 內引用，
 *     永不在檔案載入期引用（plugin 載入順序不保證 vendor 先於本 plugin）。
 */

declare( strict_types=1 );

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'NAKAMA_GAM_VERSION', '0.1.0' );
define( 'NAKAMA_GAM_DB_VERSION', 3 ); // 對應 includes/migrations/ 最大號
define( 'NAKAMA_GAM_PATH', plugin_dir_path( __FILE__ ) );
define( 'NAKAMA_GAM_FILE', __FILE__ );

require_once NAKAMA_GAM_PATH . 'includes/class-settings.php';
require_once NAKAMA_GAM_PATH . 'includes/class-migrations.php';
require_once NAKAMA_GAM_PATH . 'includes/class-plugin.php';

// plugins_loaded(20)：確保所有 plugin（含 FluentCommunity）都已載入完才 boot。
add_action( 'plugins_loaded', array( \NakamaGam\Plugin::class, 'boot' ), 20 );

register_activation_hook( __FILE__, array( \NakamaGam\Migrations::class, 'run' ) );
