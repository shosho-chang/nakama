<?php
/**
 * 003 — balances 加位階欄位（tier_label）。
 *
 * 位階名稱由 Sanji 根據 rules.py 算好後傳入；plugin 只儲存當前文字，
 * 不知道位階門檻或完整名稱表。balances 是可重建投影，加欄位無風險。
 */

declare( strict_types=1 );

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

return static function (): void {
	global $wpdb;
	require_once ABSPATH . 'wp-admin/includes/upgrade.php';

	$charset = $wpdb->get_charset_collate();
	$p       = $wpdb->prefix;

	dbDelta(
		"CREATE TABLE {$p}nakama_gam_balances (
			user_id bigint(20) unsigned NOT NULL,
			user_email varchar(190) NOT NULL DEFAULT '',
			xp_total bigint(20) NOT NULL DEFAULT 0,
			berry_balance bigint(20) NOT NULL DEFAULT 0,
			level smallint(6) NOT NULL DEFAULT 1,
			level_label varchar(50) NOT NULL DEFAULT '',
			tier_label varchar(50) NOT NULL DEFAULT '',
			level_min_xp bigint(20) NOT NULL DEFAULT 0,
			next_level_xp bigint(20) NOT NULL DEFAULT 0,
			next_level_label varchar(50) NOT NULL DEFAULT '',
			updated_at datetime NOT NULL,
			PRIMARY KEY  (user_id)
		) $charset;"
	);
};
