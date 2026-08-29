<?php
/**
 * 002 — balances 加等級帶欄位（level_min_xp / next_level_xp / next_level_label）。
 *
 * 進度條要的三個數（現有 XP、本級門檻、下一級門檻）由 Sanji 算好帶進來，
 * plugin 依舊不知道整張等級曲線——只知道「這個人現在卡在哪兩個數中間」。
 *
 * balances 是可重建的投影，加欄位無風險（dbDelta 只加不刪）。
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
			level_min_xp bigint(20) NOT NULL DEFAULT 0,
			next_level_xp bigint(20) NOT NULL DEFAULT 0,
			next_level_label varchar(50) NOT NULL DEFAULT '',
			updated_at datetime NOT NULL,
			PRIMARY KEY  (user_id)
		) $charset;"
	);
};
