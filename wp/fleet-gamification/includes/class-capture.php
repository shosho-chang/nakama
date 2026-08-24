<?php
/**
 * 捕捉層——把 FluentCommunity 的訊號寫成本地事件，不做任何判斷或計分。
 *
 * Hook 清單經 2026-08-23 對 FC 2.7.5 / Pro 2.7.6 原始碼逐一驗證（file:line 見各 handler）。
 * ⚠️ 已知縫隙（vendor 原始碼證實）：
 *  - `feed/react_added` / `react_removed` 只在 type=='like' 時觸發
 *    （ReactionController.php:132-139 被 `if ($type == 'like')` 包住）——
 *    **收藏（bookmark）沒有任何 hook**。收藏改由 Sanji 每日經 REST 增量掃描
 *    fcom_reactions（type='bookmark'）補入，承諾「最晚隔日入帳」因此仍成立。
 *  - `react_removed` 只帶 $feed 不帶誰移除——移除不即時沖正，交每日對帳 recount。
 *
 * 事件流是 raw stream：捕捉一切、不過濾人（測試帳號/Sanji 的排除在呈現層）。
 * 計分與否、給誰、給多少，全部是 nakama 規則引擎的事。
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class Capture {

	public static function register(): void {
		// 每日在場：portal ticker（FeedsController.php:1066 getTicker，45–75s/次）
		// ＋內容動作後（ActivityMonitorHandler.php:80,102）都會發這個 action。
		add_action( 'fluent_community/track_activity', array( self::class, 'on_activity' ), 10, 0 );

		// 打卡貼文：space 貼文建立（FeedsController.php:415、FeedsHelper.php:535，帶 $feed）。
		add_action( 'fluent_community/space_feed/created', array( self::class, 'on_space_feed_created' ), 10, 1 );

		// 被讚：ReactionController.php:139 / CommentsController.php:558（帶 $react, $feed；只有 like 會發）。
		add_action( 'fluent_community/feed/react_added', array( self::class, 'on_react_added' ), 10, 2 );

		// 讚被移除：只帶 $feed（不知道誰移除）→ 記 marker，對帳 recount。
		add_action( 'fluent_community/feed/react_removed', array( self::class, 'on_react_removed' ), 10, 1 );

		// 留言：CommentsController.php:181（帶 $comment, $feed, $mentions）。🟢 計分（受益人=貼文作者）。
		add_action( 'fluent_community/comment_added', array( self::class, 'on_comment_added' ), 10, 2 );

		// 課程：CourseHelper.php:211（$lesson, $userId）、:278/:290（$course, $userId）。
		add_action( 'fluent_community/course/lesson_completed', array( self::class, 'on_lesson_completed' ), 10, 2 );
		add_action( 'fluent_community/course/completed', array( self::class, 'on_course_completed' ), 10, 2 );

		// 測驗（Pro）：QuizController.php:177（$quizResult, $user, $quiz）。
		add_action( 'fluent_community/quiz/submitted', array( self::class, 'on_quiz_submitted' ), 10, 3 );
	}

	/**
	 * 每日在場（PTT 式一天一次）。
	 * ticker 對每個開著 portal 的 session 約每分鐘打一次——先用 object cache 短路，
	 * 真正的一天一次由 events.dedupe_key 的 unique constraint 保證。
	 */
	public static function on_activity(): void {
		$user_id = get_current_user_id();
		if ( ! $user_id ) {
			return;
		}

		$day       = current_time( 'Y-m-d' );
		$cache_key = "presence:{$user_id}:{$day}";
		if ( wp_cache_get( $cache_key, 'nakama_gam' ) ) {
			return;
		}

		Ledger::record_event(
			array(
				'event_type' => 'presence_day',
				'user_id'    => $user_id,
				'dedupe_key' => "presence:{$user_id}:{$day}",
			)
		);

		wp_cache_set( $cache_key, 1, 'nakama_gam', DAY_IN_SECONDS );
	}

	/**
	 * space 貼文分流：
	 *  - 啟航宣言 space → 船長儀式（身份，一次性，非經濟）
	 *  - allowlist space → 打卡事件（進判定漏斗）
	 */
	public static function on_space_feed_created( $feed ): void {
		$space_id = absint( $feed->space_id ?? 0 );
		$user_id  = absint( $feed->user_id ?? 0 );
		$feed_id  = absint( $feed->id ?? 0 );

		if ( ! $space_id || ! $user_id || ! $feed_id ) {
			return;
		}

		if ( $space_id === Settings::declaration_space() && $space_id > 0 ) {
			self::on_declaration( $user_id, $feed_id, $space_id );
			return;
		}

		if ( ! Settings::space_allowed( $space_id ) ) {
			return;
		}

		Ledger::record_event(
			array(
				'event_type'  => 'checkin_submitted',
				'user_id'     => $user_id,
				'object_type' => 'feed',
				'object_id'   => $feed_id,
				'meta'        => array(
					'space_id'        => $space_id,
					'feed_created_at' => (string) ( $feed->created_at ?? '' ), // 排程/審核時 hook 觸發時間 ≠ 內容時間
				),
				'dedupe_key'  => "checkin:feed:{$feed_id}",
			)
		);
	}

	/**
	 * 船長儀式：在啟航宣言 space 發出第一篇宣言 → 晉升「船長」。
	 *
	 * 這是**身份不是經濟**：不給 XP（宣言收到的讚照常入帳）、單向不可逆、
	 * 判定完全確定性（發文即成立），所以不走 Sanji——plugin 直接寫 user meta。
	 * 未來若加嚴條件（宣言＋導論課），邏輯搬去 Sanji，既有船長不受影響。
	 */
	private static function on_declaration( int $user_id, int $feed_id, int $space_id ): void {
		Ledger::record_event(
			array(
				'event_type'  => 'declaration_posted',
				'user_id'     => $user_id,
				'object_type' => 'feed',
				'object_id'   => $feed_id,
				'meta'        => array( 'space_id' => $space_id ),
				'dedupe_key'  => "declare:{$user_id}", // 一人一生一次
			)
		);

		if ( '' === (string) get_user_meta( $user_id, 'nakama_gam_captain_since', true ) ) {
			update_user_meta( $user_id, 'nakama_gam_captain_since', current_time( 'mysql' ) );
		}
	}

	/**
	 * 被讚。事件的主體（user_id）是「受益人＝貼文作者」，按讚者記在 meta.actor_id。
	 * 自讚也照記——要不要給分是規則引擎的事，捕捉層不做判斷。
	 */
	public static function on_react_added( $react, $feed ): void {
		$owner_id = absint( $feed->user_id ?? 0 );
		$feed_id  = absint( $feed->id ?? 0 );
		$react_id = absint( $react->id ?? 0 );

		if ( ! $owner_id || ! $feed_id || ! $react_id ) {
			return;
		}

		Ledger::record_event(
			array(
				'event_type'  => 'reaction_added',
				'user_id'     => $owner_id,
				'object_type' => 'feed',
				'object_id'   => $feed_id,
				'meta'        => array(
					'actor_id' => absint( $react->user_id ?? 0 ),
					'type'     => sanitize_key( (string) ( $react->type ?? 'like' ) ),
					'space_id' => absint( $feed->space_id ?? 0 ),
				),
				'dedupe_key'  => "react:{$react_id}",
			)
		);
	}

	/** 讚被移除：vendor 只給 $feed。記 marker，讓每日對帳知道該 recount 這篇。 */
	public static function on_react_removed( $feed ): void {
		$owner_id = absint( $feed->user_id ?? 0 );
		$feed_id  = absint( $feed->id ?? 0 );
		if ( ! $owner_id || ! $feed_id ) {
			return;
		}

		Ledger::record_event(
			array(
				'event_type'  => 'reaction_removed_marker',
				'user_id'     => $owner_id,
				'object_type' => 'feed',
				'object_id'   => $feed_id,
			)
		);
	}

	/**
	 * 被留言。與 reaction 同構：事件主體（user_id）= 受益人 = 貼文作者，
	 * 留言者記在 meta.actor_id。自留/Sanji 留言照記——排除是規則引擎的事。
	 * （2026-08-24 前的舊事件型別 comment_added 以留言者為主體，已停用；
	 * 規則引擎對它回 None，舊列無害。）
	 */
	public static function on_comment_added( $comment, $feed ): void {
		$owner_id     = absint( $feed->user_id ?? 0 );
		$feed_id      = absint( $feed->id ?? 0 );
		$commenter_id = absint( $comment->user_id ?? 0 );
		$comment_id   = absint( $comment->id ?? 0 );
		if ( ! $owner_id || ! $feed_id || ! $commenter_id || ! $comment_id ) {
			return;
		}

		Ledger::record_event(
			array(
				'event_type'  => 'comment_received',
				'user_id'     => $owner_id,
				'object_type' => 'feed',
				'object_id'   => $feed_id,
				'meta'        => array(
					'actor_id'   => $commenter_id,
					'comment_id' => $comment_id,
					'space_id'   => absint( $feed->space_id ?? 0 ),
				),
				'dedupe_key'  => "comment:{$comment_id}",
			)
		);
	}

	/** 完成單課。dedupe 一人一課一次（重複 toggle 完成狀態不再產生事件）。 */
	public static function on_lesson_completed( $lesson, $user_id ): void {
		$lesson_id = absint( $lesson->id ?? 0 );
		$user_id   = absint( $user_id );
		if ( ! $lesson_id || ! $user_id ) {
			return;
		}

		Ledger::record_event(
			array(
				'event_type'  => 'lesson_completed',
				'user_id'     => $user_id,
				'object_type' => 'lesson',
				'object_id'   => $lesson_id,
				'meta'        => array( 'course_id' => absint( $lesson->space_id ?? 0 ) ),
				'dedupe_key'  => "lesson:{$user_id}:{$lesson_id}",
			)
		);
	}

	/** 完成整門課。 */
	public static function on_course_completed( $course, $user_id ): void {
		$course_id = absint( $course->id ?? 0 );
		$user_id   = absint( $user_id );
		if ( ! $course_id || ! $user_id ) {
			return;
		}

		Ledger::record_event(
			array(
				'event_type'  => 'course_completed',
				'user_id'     => $user_id,
				'object_type' => 'course',
				'object_id'   => $course_id,
				'dedupe_key'  => "course:{$user_id}:{$course_id}",
			)
		);
	}

	/**
	 * 測驗提交。事件層「允許」重考（不 dedupe 掉重考），
	 * 「通過只給一次分」由 Sanji 在 grant 端用 idempotency_key `quiz:{uid}:{quiz_id}` 保證。
	 */
	public static function on_quiz_submitted( $quiz_result, $user, $quiz ): void {
		$user_id = absint( is_object( $user ) ? ( $user->ID ?? 0 ) : 0 );
		$quiz_id = absint( is_object( $quiz ) ? ( $quiz->id ?? 0 ) : 0 );
		if ( ! $user_id || ! $quiz_id ) {
			return;
		}

		$result_id = 0;
		$meta      = array( 'quiz_id' => $quiz_id );
		if ( is_object( $quiz_result ) ) {
			$result_id = absint( $quiz_result->id ?? 0 );
			foreach ( array( 'score', 'total_score', 'status', 'correct_answers' ) as $k ) {
				if ( isset( $quiz_result->{$k} ) && is_scalar( $quiz_result->{$k} ) ) {
					$meta[ $k ] = $quiz_result->{$k};
				}
			}
		} elseif ( is_array( $quiz_result ) ) {
			$result_id = absint( $quiz_result['id'] ?? 0 );
			foreach ( array( 'score', 'total_score', 'status', 'correct_answers' ) as $k ) {
				if ( isset( $quiz_result[ $k ] ) && is_scalar( $quiz_result[ $k ] ) ) {
					$meta[ $k ] = $quiz_result[ $k ];
				}
			}
		}

		Ledger::record_event(
			array(
				'event_type'  => 'quiz_submitted',
				'user_id'     => $user_id,
				'object_type' => 'quiz',
				'object_id'   => $quiz_id,
				'meta'        => $meta,
				'dedupe_key'  => $result_id ? "quizresult:{$result_id}" : null,
			)
		);
	}
}
