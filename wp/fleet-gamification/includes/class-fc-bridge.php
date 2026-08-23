<?php
/**
 * FcBridge——本 plugin 對 FluentCommunity 的「全部」接觸點，集中一個 class，
 * 讓 contract probe 有單一驗證面。任何新的 vendor 依賴都必須加在這裡，並同步進 probe。
 *
 * 留言策略（2026-08-23 裁決）：不重演 CommentsController 的私有流程（會隨版本漂移），
 * 改走**站內 REST dispatch**——以目前登入身分（sanji 服務帳號＝社群成員）打 vendor 自己的
 * route `POST /fluent-community/v2/feeds/{id}/comments`（活體驗證存在），
 * 驗證、防重、通知、`fluent_community/comment_added` 等副作用全部原生執行。
 *
 * Vendor 依賴清單（版本基準 FC 2.7.5 / Pro 2.7.6，2026-08-23 驗證）：
 *  - REST route: POST /fluent-community/v2/feeds/{feed_id}/comments（payload key: `comment`，
 *    CommentsController.php:421 validateCommentText 讀 Arr::get($data,'comment')）
 *  - \FluentCommunity\App\Models\Feed（fcom_posts）
 *  - \FluentCommunity\App\Models\Media（fcom_media_archive，public_url appended accessor）
 *  - 表 {prefix}fcom_post_reactions（欄位 2026-08-23 DESCRIBE 驗證，
 *    含 fca-multi-reactions 的 fca_reaction_type）——Rest::reactions 增量掃描用
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class FcBridge {

	public static function available(): bool {
		return class_exists( '\FluentCommunity\App\Models\Feed' );
	}

	/**
	 * 以目前登入使用者的身分在貼文下留言（站內 REST dispatch）。
	 *
	 * 已知前提（ops runbook）：呼叫身分必須是社群 active 成員、且是該 space 的成員
	 * （vendor 會跑 PortalPolicy + verifyCreateCommentPermission）。
	 * 已知風險：`fluent_community/check_rate_limit/create_comment` 若掛有限流器，
	 * 大量回覆可能被擋——UAT 時驗證，必要時對服務帳號放行。
	 *
	 * @return array{ok:bool, status:int, comment_id:int, error:string, data:mixed}
	 */
	public static function create_comment( int $feed_id, string $message ): array {
		$out = array(
			'ok'         => false,
			'status'     => 0,
			'comment_id' => 0,
			'error'      => '',
			'data'       => null,
		);

		if ( ! self::available() ) {
			$out['error'] = 'fluent-community not loaded';
			return $out;
		}
		if ( ! get_current_user_id() ) {
			$out['error'] = 'no authenticated user';
			return $out;
		}

		$req = new \WP_REST_Request( 'POST', '/fluent-community/v2/feeds/' . $feed_id . '/comments' );
		$req->set_body_params( array( 'comment' => $message ) );

		$res           = rest_do_request( $req );
		$out['status'] = (int) $res->get_status();

		if ( $res->is_error() ) {
			$err          = $res->as_error();
			$out['error'] = $err ? $err->get_error_message() : 'unknown error';
			return $out;
		}

		$data        = $res->get_data();
		$out['data'] = $data;
		$out['ok']   = true;

		// 回應形狀屬 vendor 內部——best-effort 抽 comment id，抽不到不視為失敗（留言已建立）。
		if ( is_array( $data ) ) {
			if ( isset( $data['comment']['id'] ) ) {
				$out['comment_id'] = (int) $data['comment']['id'];
			} elseif ( isset( $data['comment'] ) && is_object( $data['comment'] ) && isset( $data['comment']->id ) ) {
				$out['comment_id'] = (int) $data['comment']->id;
			}
		}

		return $out;
	}

	/**
	 * 讀取單篇貼文（含媒體）供 Sanji 判定。服務端讀取，繞過 per-user global scope。
	 *
	 * @return array<string,mixed>|null null = 不存在或 FC 未載入
	 */
	public static function get_feed( int $feed_id ): ?array {
		if ( ! self::available() ) {
			return null;
		}

		try {
			$feed = \FluentCommunity\App\Models\Feed::withoutGlobalScopes()->find( $feed_id );
		} catch ( \Throwable $e ) {
			return null;
		}
		if ( ! $feed ) {
			return null;
		}

		$media = array();
		if ( class_exists( '\FluentCommunity\App\Models\Media' ) ) {
			try {
				$rows = \FluentCommunity\App\Models\Media::where( 'feed_id', $feed_id )
					->where( 'is_active', 1 )
					->get();
				foreach ( $rows as $m ) {
					$media[] = array(
						'id'            => (int) $m->id,
						'url'           => (string) $m->public_url,
						'media_type'    => (string) ( $m->media_type ?? '' ),
						'object_source' => (string) ( $m->object_source ?? '' ),
						'sub_object_id' => (int) ( $m->sub_object_id ?? 0 ),
					);
				}
			} catch ( \Throwable $e ) {
				// 媒體抽取失敗不影響本文回傳；Sanji 端把「無媒體」視為需補件的訊號之一。
				$media = array();
			}
		}

		return array(
			'id'               => (int) $feed->id,
			'user_id'          => (int) $feed->user_id,
			'space_id'         => (int) ( $feed->space_id ?? 0 ),
			'status'           => (string) ( $feed->status ?? '' ),
			'content_type'     => (string) ( $feed->content_type ?? '' ),
			'title'            => (string) ( $feed->title ?? '' ),
			'message'          => (string) ( $feed->message ?? '' ),
			'message_rendered' => (string) ( $feed->message_rendered ?? '' ),
			'created_at'       => (string) ( $feed->created_at ?? '' ),
			'media'            => $media,
		);
	}
}
