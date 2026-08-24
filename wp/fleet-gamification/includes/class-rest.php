<?php
/**
 * 窄 REST API（namespace `nakama-gam/v1`）——Sanji 與本 plugin 的唯一 production 通道。
 *
 * 認證：WP Application Password（HTTPS basic auth）＋自訂 capability `nakama_gam_api`
 * （sanji 服務帳號的專屬角色持有；administrator 也放行，供營運除錯）。
 * 業務端點在 gam_enabled 關閉時回 503（/health 除外）——一鍵止血的 REST 面。
 *
 * 端點：
 *   GET  /health            狀態探針（煙霧測試用；不受開關影響）
 *   GET  /events            事件流 cursor 增量拉（after_id → id 升冪）
 *   GET  /reactions         vendor fcom_post_reactions 增量掃描（唯讀；收藏無 hook 的補洞）
 *   GET  /feeds/{id}        單篇貼文＋媒體（Sanji 判定用）
 *   POST /comments          以 sanji 身分留言（站內 dispatch vendor route）
 *   POST /grants            批次入帳（idempotency_key 冪等；金額由 Sanji 算好）
 *   GET  /balances/{id}     投影查詢（?rebuild=1 由帳本重算）
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class Rest {

	public const NS  = 'nakama-gam/v1';
	public const CAP = 'nakama_gam_api';

	public static function register(): void {
		add_action( 'rest_api_init', array( self::class, 'routes' ) );
		// ⚠️ LiteSpeed page cache 會快取 REST GET（2026-08-24 實測：帶認證的 /health
		// 回應被 cache、原樣餵給匿名請求）。整個 namespace 一律 no-cache。
		add_filter( 'rest_post_dispatch', array( self::class, 'no_cache_headers' ), 10, 3 );
	}

	/**
	 * @param \WP_REST_Response $result
	 * @param \WP_REST_Server   $server
	 * @param \WP_REST_Request  $request
	 * @return \WP_REST_Response
	 */
	public static function no_cache_headers( $result, $server, $request ) {
		if ( str_starts_with( (string) $request->get_route(), '/' . self::NS ) ) {
			$result->header( 'Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0' );
			$result->header( 'X-LiteSpeed-Cache-Control', 'no-cache' );
		}
		return $result;
	}

	public static function routes(): void {
		register_rest_route(
			self::NS,
			'/health',
			array(
				'methods'             => 'GET',
				'permission_callback' => array( self::class, 'can_access' ),
				'callback'            => array( self::class, 'health' ),
			)
		);

		register_rest_route(
			self::NS,
			'/events',
			array(
				'methods'             => 'GET',
				'permission_callback' => array( self::class, 'can_access' ),
				'callback'            => array( self::class, 'events' ),
				'args'                => array(
					'after_id' => array( 'type' => 'integer', 'default' => 0, 'minimum' => 0 ),
					'limit'    => array( 'type' => 'integer', 'default' => 200, 'minimum' => 1, 'maximum' => 500 ),
					'types'    => array( 'type' => 'string', 'default' => '' ),
				),
			)
		);

		register_rest_route(
			self::NS,
			'/reactions',
			array(
				'methods'             => 'GET',
				'permission_callback' => array( self::class, 'can_access' ),
				'callback'            => array( self::class, 'reactions' ),
				'args'                => array(
					'after_id' => array( 'type' => 'integer', 'default' => 0, 'minimum' => 0 ),
					'limit'    => array( 'type' => 'integer', 'default' => 200, 'minimum' => 1, 'maximum' => 500 ),
					'types'    => array( 'type' => 'string', 'default' => 'bookmark' ),
				),
			)
		);

		register_rest_route(
			self::NS,
			'/feeds/(?P<id>\d+)',
			array(
				'methods'             => 'GET',
				'permission_callback' => array( self::class, 'can_access' ),
				'callback'            => array( self::class, 'feed' ),
			)
		);

		register_rest_route(
			self::NS,
			'/comments',
			array(
				'methods'             => 'POST',
				'permission_callback' => array( self::class, 'can_access' ),
				'callback'            => array( self::class, 'comment' ),
			)
		);

		register_rest_route(
			self::NS,
			'/grants',
			array(
				'methods'             => 'POST',
				'permission_callback' => array( self::class, 'can_access' ),
				'callback'            => array( self::class, 'grants' ),
			)
		);

		register_rest_route(
			self::NS,
			'/balances/(?P<id>\d+)',
			array(
				'methods'             => 'GET',
				'permission_callback' => array( self::class, 'can_access' ),
				'callback'            => array( self::class, 'balance' ),
			)
		);
	}

	public static function can_access(): bool {
		return current_user_can( self::CAP ) || current_user_can( 'manage_options' );
	}

	/** 業務端點的止血閘：關閉時回 503。 */
	private static function gate(): ?\WP_Error {
		if ( Settings::enabled() ) {
			return null;
		}
		return new \WP_Error( 'gam_disabled', 'gamification is disabled (gam_enabled=0)', array( 'status' => 503 ) );
	}

	public static function health(): array {
		return array(
			'ok'             => true,
			'enabled'        => Settings::enabled(),
			'db_version'     => Migrations::current(),
			'plugin_version' => NAKAMA_GAM_VERSION,
			'fc_available'   => class_exists( '\NakamaGam\FcBridge' ) && FcBridge::available(),
			'time'           => current_time( 'mysql' ),
		);
	}

	public static function events( \WP_REST_Request $req ) {
		if ( $err = self::gate() ) {
			return $err;
		}
		global $wpdb;

		$after = absint( $req['after_id'] );
		$limit = max( 1, min( 500, absint( $req['limit'] ) ) );
		$types = self::csv_keys( (string) $req['types'] );

		$sql = 'SELECT id, event_type, user_id, user_email, object_type, object_id, meta, dedupe_key, created_at FROM ' .
			Ledger::events_table() . ' WHERE id > %d';
		$params = array( $after );
		if ( $types ) {
			$sql     .= ' AND event_type IN (' . implode( ',', array_fill( 0, count( $types ), '%s' ) ) . ')';
			$params   = array_merge( $params, $types );
		}
		$sql     .= ' ORDER BY id ASC LIMIT %d';
		$params[] = $limit;

		$rows = $wpdb->get_results( $wpdb->prepare( $sql, $params ), ARRAY_A );
		$max  = 0;
		foreach ( $rows as &$r ) {
			$r['id']        = (int) $r['id'];
			$r['user_id']   = (int) $r['user_id'];
			$r['object_id'] = (int) $r['object_id'];
			$r['meta']      = json_decode( (string) $r['meta'], true );
			$max            = max( $max, $r['id'] );
		}
		unset( $r );

		return array( 'events' => $rows, 'max_id' => $max, 'count' => count( $rows ) );
	}

	/** vendor reactions 增量掃描（唯讀）。收藏（type=bookmark）沒有 hook，Sanji 每日掃這裡補入。 */
	public static function reactions( \WP_REST_Request $req ) {
		if ( $err = self::gate() ) {
			return $err;
		}
		global $wpdb;

		$after = absint( $req['after_id'] );
		$limit = max( 1, min( 500, absint( $req['limit'] ) ) );
		$types = self::csv_keys( (string) $req['types'] );
		if ( ! $types ) {
			$types = array( 'bookmark' );
		}

		$table = $wpdb->prefix . 'fcom_post_reactions';
		$sql   = "SELECT id, user_id, object_id, parent_id, object_type, type, fca_reaction_type, created_at FROM {$table} WHERE id > %d" .
			' AND type IN (' . implode( ',', array_fill( 0, count( $types ), '%s' ) ) . ')' .
			' ORDER BY id ASC LIMIT %d';

		$rows = $wpdb->get_results( $wpdb->prepare( $sql, array_merge( array( $after ), $types, array( $limit ) ) ), ARRAY_A );
		$max  = 0;
		foreach ( $rows as &$r ) {
			foreach ( array( 'id', 'user_id', 'object_id', 'parent_id' ) as $k ) {
				$r[ $k ] = (int) $r[ $k ];
			}
			$max = max( $max, $r['id'] );
		}
		unset( $r );

		return array( 'reactions' => $rows, 'max_id' => $max, 'count' => count( $rows ) );
	}

	public static function feed( \WP_REST_Request $req ) {
		if ( $err = self::gate() ) {
			return $err;
		}
		$data = FcBridge::get_feed( absint( $req['id'] ) );
		if ( null === $data ) {
			return new \WP_Error( 'not_found', 'feed not found or fluent-community unavailable', array( 'status' => 404 ) );
		}
		return $data;
	}

	public static function comment( \WP_REST_Request $req ) {
		if ( $err = self::gate() ) {
			return $err;
		}
		$feed_id = absint( $req->get_param( 'feed_id' ) );
		$message = trim( (string) $req->get_param( 'comment' ) );
		if ( ! $feed_id || '' === $message ) {
			return new \WP_Error( 'invalid_params', 'feed_id and comment are required', array( 'status' => 400 ) );
		}

		$result = FcBridge::create_comment( $feed_id, $message );
		if ( ! $result['ok'] ) {
			return new \WP_Error( 'comment_failed', $result['error'], array( 'status' => $result['status'] ?: 500 ) );
		}
		return array( 'ok' => true, 'comment_id' => $result['comment_id'], 'status' => $result['status'] );
	}

	/**
	 * 批次入帳。單批上限 100；逐筆回報 created / duplicate / invalid——
	 * duplicate（idempotency 命中）是冪等成功，Sanji 重放不會重複入帳。
	 */
	public static function grants( \WP_REST_Request $req ) {
		if ( $err = self::gate() ) {
			return $err;
		}
		$items = $req->get_param( 'grants' );
		if ( ! is_array( $items ) || ! $items ) {
			return new \WP_Error( 'invalid_params', 'grants array is required', array( 'status' => 400 ) );
		}
		if ( count( $items ) > 100 ) {
			return new \WP_Error( 'too_many', 'max 100 grants per batch', array( 'status' => 400 ) );
		}

		$results = array();
		foreach ( $items as $item ) {
			if ( ! is_array( $item ) ) {
				$results[] = array( 'status' => 'invalid', 'grant_id' => 0, 'idempotency_key' => '' );
				continue;
			}
			$key      = (string) ( $item['idempotency_key'] ?? '' );
			$grant_id = Ledger::add_grant( $item );
			if ( $grant_id > 0 ) {
				$results[] = array( 'status' => 'created', 'grant_id' => $grant_id, 'idempotency_key' => $key );
			} else {
				$valid     = '' !== $key && absint( $item['user_id'] ?? 0 ) && '' !== (string) ( $item['source'] ?? '' ) && '' !== (string) ( $item['rule_version'] ?? '' );
				$results[] = array( 'status' => $valid ? 'duplicate' : 'invalid', 'grant_id' => 0, 'idempotency_key' => $key );
			}
		}

		return array( 'results' => $results, 'count' => count( $results ) );
	}

	public static function balance( \WP_REST_Request $req ) {
		if ( $err = self::gate() ) {
			return $err;
		}
		global $wpdb;

		$user_id = absint( $req['id'] );
		if ( '1' === (string) $req->get_param( 'rebuild' ) ) {
			Ledger::rebuild_balance( $user_id );
		}

		$row = $wpdb->get_row(
			$wpdb->prepare( 'SELECT * FROM ' . Ledger::balances_table() . ' WHERE user_id = %d', $user_id ),
			ARRAY_A
		);
		if ( ! $row ) {
			return array( 'user_id' => $user_id, 'xp_total' => 0, 'berry_balance' => 0, 'level' => 1, 'exists' => false );
		}
		foreach ( array( 'user_id', 'xp_total', 'berry_balance', 'level' ) as $k ) {
			$row[ $k ] = (int) $row[ $k ];
		}
		$row['exists'] = true;
		return $row;
	}

	/** @return string[] csv → sanitize_key 過的清單 */
	private static function csv_keys( string $csv ): array {
		if ( '' === trim( $csv ) ) {
			return array();
		}
		$out = array();
		foreach ( explode( ',', $csv ) as $t ) {
			$t = sanitize_key( trim( $t ) );
			if ( '' !== $t ) {
				$out[] = $t;
			}
		}
		return array_values( array_unique( $out ) );
	}
}
