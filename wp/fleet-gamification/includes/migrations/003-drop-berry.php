<?php
/**
 * 003 — 貝里退役：拿掉 grants.berry 與 balances.berry_balance。
 *
 * 修修 2026-09-02 裁決：貝里完全退出實作，不再出現在任何一層。
 *
 * 無損性已證：貝里恆等於 XP÷10（`rules.berry_of` 是純函式，沒有其他寫入者）。
 * 上線資料抽驗 2,294 筆授予與 70 筆餘額，`berry <> FLOOR(xp/10)` 為 0 筆——
 * 兩欄不帶任何 XP 以外的資訊，刪掉不會失去可還原的東西。
 *
 * dbDelta 只加不刪，所以這裡走裸 ALTER；欄位可能已不存在（重跑、或新站從未
 * 建過），因此先查 information_schema 再決定要不要動。
 */

declare( strict_types=1 );

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

return static function (): void {
	global $wpdb;

	$targets = array(
		$wpdb->prefix . 'nakama_gam_grants'   => 'berry',
		$wpdb->prefix . 'nakama_gam_balances' => 'berry_balance',
	);

	foreach ( $targets as $table => $column ) {
		$exists = $wpdb->get_var(
			$wpdb->prepare(
				'SELECT COUNT(*) FROM information_schema.COLUMNS' .
				' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s',
				$table,
				$column
			)
		);

		if ( (int) $exists > 0 ) {
			// $table / $column 都是本檔寫死的常數，非使用者輸入——可安全內插。
			$wpdb->query( "ALTER TABLE `{$table}` DROP COLUMN `{$column}`" ); // phpcs:ignore WordPress.DB.PreparedSQL.NotPrepared
		}
	}
};
