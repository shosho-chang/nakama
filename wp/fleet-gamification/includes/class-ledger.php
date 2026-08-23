<?php
/**
 * Ledger——事件流與帳本的唯一寫入口。本 class 不含任何規則（金額由 Sanji 經 REST 決定）。
 *
 * 兩層防重，職責不同：
 *  - events.dedupe_key   捕捉層去噪（同一事實不重複記錄）；raw stream 允許重複型事實（如測驗重考）
 *  - grants.idempotency_key  經濟層防線（同一授予永不入帳兩次）——DB unique constraint 是最後防線
 *
 * 帳本三鐵則：append-only（改錯帳走沖正 grant，reverses_grant_id 指向原帳）；
 * 每筆帶 idempotency_key；每筆帶 rule_version（規則改版只影響未來，永不回溯）。
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class Ledger {

	public static function events_table(): string {
		global $wpdb;
		return $wpdb->prefix . 'nakama_gam_events';
	}

	public static function grants_table(): string {
		global $wpdb;
		return $wpdb->prefix . 'nakama_gam_grants';
	}

	public static function balances_table(): string {
		global $wpdb;
		return $wpdb->prefix . 'nakama_gam_balances';
	}

	/**
	 * 記一筆事件。回傳新 event id；dedupe 命中（已記過）回傳 0。
	 *
	 * @param array{
	 *   event_type: string,
	 *   user_id: int,
	 *   object_type?: string,
	 *   object_id?: int,
	 *   meta?: array<string,mixed>,
	 *   dedupe_key?: string|null
	 * } $args
	 */
	public static function record_event( array $args ): int {
		global $wpdb;

		$user_id = absint( $args['user_id'] ?? 0 );
		$type    = sanitize_key( $args['event_type'] ?? '' );
		if ( ! $user_id || '' === $type ) {
			return 0;
		}

		$user  = get_userdata( $user_id );
		$email = $user ? (string) $user->user_email : '';

		$dedupe = isset( $args['dedupe_key'] ) && '' !== $args['dedupe_key']
			? substr( (string) $args['dedupe_key'], 0, 191 )
			: null;

		// INSERT IGNORE：dedupe_key 撞 unique 時靜默略過（這正是 dedupe 的語意）。
		$sql = $wpdb->prepare(
			'INSERT IGNORE INTO ' . self::events_table() .
			' (event_type, user_id, user_email, object_type, object_id, meta, dedupe_key, created_at)' .
			' VALUES (%s, %d, %s, %s, %d, %s, %s, %s)',
			$type,
			$user_id,
			$email,
			sanitize_key( (string) ( $args['object_type'] ?? '' ) ),
			absint( $args['object_id'] ?? 0 ),
			wp_json_encode( $args['meta'] ?? array() ),
			$dedupe,
			current_time( 'mysql' )
		);
		$wpdb->query( $sql );

		return $wpdb->rows_affected > 0 ? (int) $wpdb->insert_id : 0;
	}

	/**
	 * 入一筆帳（授予或沖正）。回傳 grant id；idempotency 命中回傳 0（冪等成功，不是錯誤）。
	 * 成功入帳後同步遞增 balances 投影。
	 *
	 * @param array{
	 *   user_id: int,
	 *   xp: int,
	 *   berry: int,
	 *   source: string,
	 *   idempotency_key: string,
	 *   rule_version: string,
	 *   season?: string,
	 *   ref_event_id?: int,
	 *   reverses_grant_id?: int,
	 *   reason?: string,
	 *   level_after?: int,
	 *   level_label?: string
	 * } $args
	 */
	public static function add_grant( array $args ): int {
		global $wpdb;

		$user_id = absint( $args['user_id'] ?? 0 );
		$source  = sanitize_key( (string) ( $args['source'] ?? '' ) );
		$idem    = substr( (string) ( $args['idempotency_key'] ?? '' ), 0, 191 );
		$rulev   = substr( (string) ( $args['rule_version'] ?? '' ), 0, 20 );

		if ( ! $user_id || '' === $source || '' === $idem || '' === $rulev ) {
			return 0;
		}

		$xp    = (int) ( $args['xp'] ?? 0 );
		$berry = (int) ( $args['berry'] ?? 0 );

		$user  = get_userdata( $user_id );
		$email = $user ? (string) $user->user_email : '';

		$sql = $wpdb->prepare(
			'INSERT IGNORE INTO ' . self::grants_table() .
			' (user_id, user_email, xp, berry, source, season, ref_event_id, reverses_grant_id, reason, idempotency_key, rule_version, created_at)' .
			' VALUES (%d, %s, %d, %d, %s, %s, %d, %d, %s, %s, %s, %s)',
			$user_id,
			$email,
			$xp,
			$berry,
			$source,
			substr( (string) ( $args['season'] ?? '' ), 0, 10 ),
			absint( $args['ref_event_id'] ?? 0 ),
			absint( $args['reverses_grant_id'] ?? 0 ),
			substr( (string) ( $args['reason'] ?? '' ), 0, 255 ),
			$idem,
			$rulev,
			current_time( 'mysql' )
		);
		$wpdb->query( $sql );

		if ( $wpdb->rows_affected <= 0 ) {
			return 0; // idempotency 命中：這筆帳已入過。
		}
		$grant_id = (int) $wpdb->insert_id;

		self::bump_balance(
			$user_id,
			$email,
			$xp,
			$berry,
			isset( $args['level_after'] ) ? absint( $args['level_after'] ) : null,
			isset( $args['level_label'] ) ? substr( (string) $args['level_label'], 0, 50 ) : null
		);

		return $grant_id;
	}

	/**
	 * 遞增投影。level 由 Sanji（規則引擎）算好帶進來——plugin 不知道等級曲線。
	 * 投影壞了可整表重建（rebuild_balance），帳本永遠是真相。
	 */
	private static function bump_balance( int $user_id, string $email, int $xp, int $berry, ?int $level, ?string $level_label ): void {
		global $wpdb;

		$level_sql = '';
		if ( null !== $level && $level > 0 ) {
			$level_sql .= $wpdb->prepare( ', level = %d', $level );
		}
		if ( null !== $level_label && '' !== $level_label ) {
			$level_sql .= $wpdb->prepare( ', level_label = %s', $level_label );
		}

		$wpdb->query(
			$wpdb->prepare(
				'INSERT INTO ' . self::balances_table() .
				' (user_id, user_email, xp_total, berry_balance, level, updated_at)' .
				' VALUES (%d, %s, %d, %d, %d, %s)' .
				' ON DUPLICATE KEY UPDATE' .
				' xp_total = xp_total + VALUES(xp_total),' .
				' berry_balance = berry_balance + VALUES(berry_balance),' .
				" user_email = VALUES(user_email), updated_at = VALUES(updated_at)$level_sql",
				$user_id,
				$email,
				$xp,
				$berry,
				max( 1, (int) $level ),
				current_time( 'mysql' )
			)
		);
	}

	/**
	 * 從帳本重算單一使用者的投影（每日對帳 & 投影損毀時用）。
	 * 回傳重算後的 [xp_total, berry_balance]。
	 */
	public static function rebuild_balance( int $user_id ): array {
		global $wpdb;

		$row = $wpdb->get_row(
			$wpdb->prepare(
				'SELECT COALESCE(SUM(xp),0) AS xp_total, COALESCE(SUM(berry),0) AS berry_total, MAX(user_email) AS email FROM ' .
				self::grants_table() . ' WHERE user_id = %d',
				$user_id
			),
			ARRAY_A
		);

		$xp    = (int) ( $row['xp_total'] ?? 0 );
		$berry = (int) ( $row['berry_total'] ?? 0 );

		$wpdb->query(
			$wpdb->prepare(
				'INSERT INTO ' . self::balances_table() .
				' (user_id, user_email, xp_total, berry_balance, updated_at)' .
				' VALUES (%d, %s, %d, %d, %s)' .
				' ON DUPLICATE KEY UPDATE xp_total = VALUES(xp_total), berry_balance = VALUES(berry_balance), updated_at = VALUES(updated_at)',
				$user_id,
				(string) ( $row['email'] ?? '' ),
				$xp,
				$berry,
				current_time( 'mysql' )
			)
		);

		return array( $xp, $berry );
	}
}
