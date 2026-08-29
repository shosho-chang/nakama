<?php
/**
 * 版本號驅動的 migration runner（仿 nakama repo `migrations/` 慣例）。
 *
 * - option `nakama_gam_db_version` 記錄已跑到的號碼
 * - includes/migrations/NNN-*.php 一支一號，依號碼順序補跑
 * - 每支必須 idempotent（部署可能重入）；只加不改優先——
 *   加欄位(nullable)/加表/加索引安全；改型別或刪欄位走多步驟（新欄位→雙寫→回填→切讀→廢棄）
 * - 事件表/grants 表**不改語意只加欄位**；投影表可隨時砍掉重建
 *
 * _Avoid_: vendor 那種無版本號的 `if (!in_array($col)) ALTER` 堆積式寫法。
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class Migrations {

	public const OPT_DB_VERSION = 'nakama_gam_db_version';

	public static function current(): int {
		return (int) get_option( self::OPT_DB_VERSION, 0 );
	}

	public static function run_if_needed(): void {
		if ( self::current() < NAKAMA_GAM_DB_VERSION ) {
			self::run();
		}
	}

	/**
	 * 依序補跑缺號 migration。每跑完一支立刻寫版本號——
	 * 中途失敗時已完成的不重跑，下次從斷點續。
	 */
	public static function run(): void {
		$from = self::current();
		for ( $n = $from + 1; $n <= NAKAMA_GAM_DB_VERSION; $n++ ) {
			$file = self::file_for( $n );
			if ( null === $file ) {
				// 缺號是部署錯誤（檔案沒同步全）——停在斷點，不跳號。
				error_log( sprintf( '[nakama-gam] migration %03d missing, halted at %d', $n, $n - 1 ) );
				return;
			}
			$fn = require $file;
			if ( is_callable( $fn ) ) {
				$fn();
			}
			update_option( self::OPT_DB_VERSION, $n, true );
		}
	}

	private static function file_for( int $n ): ?string {
		$matches = glob( NAKAMA_GAM_PATH . sprintf( 'includes/migrations/%03d-*.php', $n ) );
		return ( $matches && count( $matches ) === 1 ) ? $matches[0] : null;
	}
}
