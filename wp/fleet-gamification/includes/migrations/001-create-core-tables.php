<?php
/**
 * 001 — 核心三表。
 *
 * events   捕捉層事件流（原生訊號＋checkin_submitted），Sanji 用 id 當 cursor 增量拉
 * grants   Ledger 本體（append-only）：一筆授予/沖正一列，idempotency_key 是 DB 層防重防線
 * balances 衍生投影（可砍掉重建）：加速 portal 顯示，每日對帳重算校驗
 *
 * 記人一律 user_id ＋ user_email snapshot 雙鍵——email 是跨平台耐久身分。
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
		"CREATE TABLE {$p}nakama_gam_events (
			id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
			event_type varchar(50) NOT NULL,
			user_id bigint(20) unsigned NOT NULL,
			user_email varchar(190) NOT NULL DEFAULT '',
			object_type varchar(30) NOT NULL DEFAULT '',
			object_id bigint(20) unsigned NOT NULL DEFAULT 0,
			meta longtext NULL,
			dedupe_key varchar(191) NULL,
			created_at datetime NOT NULL,
			PRIMARY KEY  (id),
			UNIQUE KEY dedupe_key (dedupe_key),
			KEY event_type (event_type),
			KEY user_id (user_id),
			KEY created_at (created_at)
		) $charset;"
	);

	dbDelta(
		"CREATE TABLE {$p}nakama_gam_grants (
			id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
			user_id bigint(20) unsigned NOT NULL,
			user_email varchar(190) NOT NULL DEFAULT '',
			xp int(11) NOT NULL DEFAULT 0,
			berry int(11) NOT NULL DEFAULT 0,
			source varchar(50) NOT NULL,
			season varchar(10) NOT NULL DEFAULT '',
			ref_event_id bigint(20) unsigned NOT NULL DEFAULT 0,
			reverses_grant_id bigint(20) unsigned NOT NULL DEFAULT 0,
			reason varchar(255) NOT NULL DEFAULT '',
			idempotency_key varchar(191) NOT NULL,
			rule_version varchar(20) NOT NULL DEFAULT '',
			created_at datetime NOT NULL,
			PRIMARY KEY  (id),
			UNIQUE KEY idempotency_key (idempotency_key),
			KEY user_id (user_id),
			KEY season (season),
			KEY source (source),
			KEY created_at (created_at)
		) $charset;"
	);

	dbDelta(
		"CREATE TABLE {$p}nakama_gam_balances (
			user_id bigint(20) unsigned NOT NULL,
			user_email varchar(190) NOT NULL DEFAULT '',
			xp_total bigint(20) NOT NULL DEFAULT 0,
			berry_balance bigint(20) NOT NULL DEFAULT 0,
			level smallint(6) NOT NULL DEFAULT 1,
			level_label varchar(50) NOT NULL DEFAULT '',
			updated_at datetime NOT NULL,
			PRIMARY KEY  (user_id)
		) $charset;"
	);
};
