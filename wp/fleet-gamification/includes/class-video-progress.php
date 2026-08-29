<?php
/**
 * 影片觀看橋接——把 FluentPlayer 的伺服器權威判定接成本地事件。
 *
 * 為什麼需要這一層：FluentPlayer 內建一套 progression 引擎，客戶端只送原始觀看
 * 區段、伺服器自己擁有片長並重算覆蓋率（ProgressionService.php 檔頭：「The client
 * is never trusted」）。那套引擎**已經無條件註冊在站上**（fluent-player/app/Hooks/
 * actions.php:18 註冊 wp_ajax_fluent_player_progression），但它的瀏覽器端 tracker
 * 只在 LearnDash 整合裡 enqueue（LearnDashIntegration.php:388）。LearnDash 沒裝，
 * 所以 FluentCommunity 的課程頁從來不會送資料進去，`fluent_player/watch_recorded`
 * 永遠不發。
 *
 * 我們補的就是這兩頭——中間那段防偽核心不重寫、不修改任何 vendor 檔案：
 *
 *   課程頁載入 tracker（本檔 enqueue）
 *        ↓ 送原始區段
 *   wp_ajax_fluent_player_progression        ← vendor，已在跑
 *        ↓ 伺服器夾 clamp、重算覆蓋率
 *   do_action('fluent_player/watch_recorded')← vendor，已會發
 *        ↓
 *   本檔 on_watch_recorded() → nakama_gam_events  ← 我們接這裡
 *
 * ⚠️ 現階段**只記錄、不計分**。`video_watched` 刻意不進 SanjiConfig.scored_sources，
 * 先累積數個月真實觀看資料，再決定看完影片值多少 XP、80% 閘門要不要開
 * （修修 2026-08-28 裁決）。
 *
 * 發分守則（未來啟用時）：**只認 durationSource === 'server'**。伺服器拿得到片長
 * 才算數——Bunny 匯入的 media 有 provider 片長，YouTube 沒有，所以 YouTube 課自動
 * 落在給分範圍外，不需要任何特例程式碼。
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class VideoProgress {

	/** tracker 送出的節流間隔（秒）——播放中每隔這麼久回報一次。 */
	private const FLUSH_SECONDS = 30;

	public static function register(): void {
		// portal 是 Vue SPA：這個 hook 在 portal 外殼渲染時觸發一次，
		// tracker 自己用 MutationObserver 等後續動態掛上的播放器。
		add_action( 'fluent_community/portal_sidebar', array( self::class, 'enqueue_tracker' ), 5, 0 );

		// 判定完成 → 記事件。vendor 發的 payload 帶 duration / durationSource /
		// coverage / verdict / policy / context（ProgressionService.php:187）。
		add_action( 'fluent_player/watch_recorded', array( self::class, 'on_watch_recorded' ), 10, 3 );

		// 課程影片要跨場次累計——一支 60 分鐘的直播錄影沒有人一口氣看完。
		// vendor 預設 accumulate=false（stateless），只對我們自己的 context 開。
		add_filter( 'fluent_player/progression/policy', array( self::class, 'policy_for_course' ), 10, 4 );
	}

	/**
	 * 在 portal 掛上 tracker。
	 *
	 * nonce 用 vendor 的 `fluent_player_frontend`——那是 ProgressionHandler::handle()
	 * 驗的那一支（check_ajax_referer('fluent_player_frontend', 'nonce', false)）。
	 */
	public static function enqueue_tracker(): void {
		if ( ! is_user_logged_in() ) {
			return; // 端點本身也擋（no nopriv variant），提早退出省一支 JS。
		}
		if ( ! defined( 'FLUENT_PLAYER_VERSION' ) ) {
			return; // FluentPlayer 沒啟用，沒有播放器可追。
		}

		wp_enqueue_script(
			'nakama-gam-video-progress',
			plugins_url( 'assets/video-progress.js', NAKAMA_GAM_FILE ),
			array(),
			NAKAMA_GAM_VERSION,
			true
		);

		wp_localize_script(
			'nakama-gam-video-progress',
			'nakamaGamVideoProgress',
			array(
				'ajaxUrl'      => admin_url( 'admin-ajax.php' ),
				'nonce'        => wp_create_nonce( 'fluent_player_frontend' ),
				'flushSeconds' => self::FLUSH_SECONDS,
			)
		);
	}

	/**
	 * 課程情境下開啟跨場次累計。
	 *
	 * @param array $policy   vendor 預設合併後的 policy
	 * @param int   $media_id
	 * @param int   $user_id
	 * @param array $context  我們 tracker 送上來的 course_id / step_id
	 */
	public static function policy_for_course( $policy, $media_id, $user_id, $context ) {
		if ( ! is_array( $policy ) ) {
			return $policy;
		}
		// 只在這支 media 確實掛在某一課上時改動——其他來源維持 vendor 預設。
		// 用伺服器端反查而非 tracker 送上來的 context：客戶端說什麼都不算數。
		if ( ! self::lesson_for_media( $media_id ) ) {
			return $policy;
		}
		$policy['accumulate'] = true;

		return $policy;
	}

	/**
	 * 這支 media 掛在哪一課？回傳 ['lesson_id' => int, 'course_id' => int] 或 null。
	 *
	 * 連結存在 lesson.message 的 Gutenberg block：
	 *   <!-- wp:fluent-player/media {"mediaId":9896,"isFcomFeatureMedia":true} /-->
	 *
	 * 用 REGEXP 而非 LIKE——`"mediaId":989` 的 LIKE 會誤中 9896。
	 * 每 request 快取，避免同一支影片的多次回報重複查。
	 */
	private static function lesson_for_media( int $media_id ): ?array {
		static $cache = array();

		$media_id = absint( $media_id );
		if ( ! $media_id ) {
			return null;
		}
		if ( array_key_exists( $media_id, $cache ) ) {
			return $cache[ $media_id ];
		}

		global $wpdb;
		$table = $wpdb->prefix . 'fcom_posts';
		$row   = $wpdb->get_row(
			$wpdb->prepare(
				"SELECT id, space_id FROM {$table} WHERE type = 'course_lesson' AND message REGEXP %s LIMIT 1",
				'"mediaId":' . $media_id . '([^0-9]|$)'
			),
			ARRAY_A
		);

		$cache[ $media_id ] = $row
			? array(
				'lesson_id' => absint( $row['id'] ),
				'course_id' => absint( $row['space_id'] ),
			)
			: null;

		return $cache[ $media_id ];
	}

	/**
	 * 伺服器判定完成 → 寫事件。
	 *
	 * 這裡**不判斷給不給分**，只忠實記錄。冪等鍵刻意做成「每位使用者每支影片一筆」，
	 * 由 Ledger 的 dedupe 擋重複；同一支影片後續的回報會被視為重複而不再寫入，
	 * 所以 events 表裡留下的是**第一次達標**的那一刻。
	 *
	 * @param int   $media_id
	 * @param int   $user_id
	 * @param array $payload duration / durationSource / coverage / verdict / policy / context
	 */
	public static function on_watch_recorded( $media_id, $user_id, $payload ): void {
		$media_id = absint( $media_id );
		$user_id  = absint( $user_id );
		if ( ! $media_id || ! $user_id || ! is_array( $payload ) ) {
			return;
		}

		// verdict 形狀＝['complete' => bool, 'reason' => 'ended'|'threshold'|null]
		// （fluent-player/app/Services/Progression/Evaluator.php:37-44）
		$verdict = isset( $payload['verdict'] ) && is_array( $payload['verdict'] ) ? $payload['verdict'] : array();
		if ( empty( $verdict['complete'] ) ) {
			return; // 還沒看完，不記——記了會變成每 30 秒一筆垃圾。
		}

		$source = isset( $payload['durationSource'] ) ? (string) $payload['durationSource'] : 'unknown';
		// 課程歸屬一律伺服器端反查，不採信 tracker 送上來的 context。
		$lesson = self::lesson_for_media( $media_id );

		Ledger::record_event(
			array(
				'event_type'  => 'video_watched',
				'user_id'     => $user_id,
				'object_type' => 'media',
				'object_id'   => $media_id,
				'meta'        => array(
					// 發分的唯一判準——'server' 才是伺服器擁有片長的判定。
					'duration_source' => $source,
					'duration'        => isset( $payload['duration'] ) ? (float) $payload['duration'] : 0.0,
					'coverage'        => isset( $payload['coverage'] ) ? (float) $payload['coverage'] : 0.0,
					'threshold'       => isset( $payload['policy']['threshold'] ) ? (float) $payload['policy']['threshold'] : 0.0,
					// 'ended'（真的播到尾）vs 'threshold'（覆蓋率達標）——之後分析
					// 觀看行為時，這兩種達標方式的意義不一樣。
					'reason'          => isset( $verdict['reason'] ) ? (string) $verdict['reason'] : '',
					'course_id'       => $lesson ? $lesson['course_id'] : 0,
					'lesson_id'       => $lesson ? $lesson['lesson_id'] : 0,
				),
				'dedupe_key'  => "video:{$user_id}:{$media_id}",
			)
		);
	}
}
