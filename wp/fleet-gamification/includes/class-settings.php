<?php
/**
 * 設定存取。全部走 WP options（object cache 友善），不自建設定表。
 *
 * - nakama_gam_enabled          '1'|'0'  一鍵止血開關（預設關——UAT 通過才開）
 * - nakama_gam_space_allowlist  int[]    只有名單內的 space 會產生打卡事件
 *                                        （生產本來就需要：只有打卡 space 該給分；
 *                                        隱藏測試 Space 只是名單的第一個成員）
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class Settings {

	public const OPT_ENABLED   = 'nakama_gam_enabled';
	public const OPT_ALLOWLIST = 'nakama_gam_space_allowlist';

	public static function enabled(): bool {
		return get_option( self::OPT_ENABLED, '0' ) === '1';
	}

	public static function enable(): void {
		update_option( self::OPT_ENABLED, '1', true );
	}

	public static function disable(): void {
		update_option( self::OPT_ENABLED, '0', true );
	}

	/** @return int[] 打卡事件的 space 白名單（空陣列 = 不捕捉任何打卡） */
	public static function space_allowlist(): array {
		$raw = get_option( self::OPT_ALLOWLIST, array() );
		if ( ! is_array( $raw ) ) {
			return array();
		}
		return array_values( array_filter( array_map( 'absint', $raw ) ) );
	}

	public static function space_allowed( int $space_id ): bool {
		return in_array( $space_id, self::space_allowlist(), true );
	}
}
