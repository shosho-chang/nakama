<?php
/**
 * Boot 序：migration → settings →（enabled 時）capture / REST / portal。
 *
 * gam_enabled 是一鍵止血開關：關閉時捕捉層停寫、業務 REST 回 503、portal UI 隱藏，
 * 只留 /health 供煙霧測試。社群本體完全不受影響。
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class Plugin {

	public static function boot(): void {
		// 每次載入比對 db version，缺號補跑（部署 = rsync 覆蓋檔案，不會重跑 activation hook）。
		Migrations::run_if_needed();

		$optional = array(
			'includes/class-ledger.php'      => '\NakamaGam\Ledger',
			'includes/class-capture.php'     => '\NakamaGam\Capture',
			'includes/class-fc-bridge.php'   => '\NakamaGam\FcBridge',
			'includes/class-rest.php'        => '\NakamaGam\Rest',
			'includes/class-projections.php' => '\NakamaGam\Projections',
			'includes/class-portal-ui.php'   => '\NakamaGam\PortalUi',
			'includes/class-voyage-page.php' => '\NakamaGam\VoyagePage',
			'includes/class-video-progress.php' => '\NakamaGam\VideoProgress',
		);
		foreach ( $optional as $file => $class ) {
			if ( file_exists( NAKAMA_GAM_PATH . $file ) ) {
				require_once NAKAMA_GAM_PATH . $file;
			}
		}

		// REST 永遠註冊（/health 不受開關影響；業務端點內部自行檢查開關回 503）。
		if ( class_exists( '\NakamaGam\Rest' ) ) {
			self::ensure_roles(); // 依賴 Rest::CAP，必須在 require 之後
			Rest::register();
		}

		if ( ! Settings::enabled() ) {
			return;
		}

		if ( class_exists( '\NakamaGam\Capture' ) ) {
			Capture::register();
		}
		if ( class_exists( '\NakamaGam\PortalUi' ) ) {
			PortalUi::register();
		}
		if ( class_exists( '\NakamaGam\VoyagePage' ) ) {
			VoyagePage::register();
		}
		if ( class_exists( '\NakamaGam\VideoProgress' ) ) {
			VideoProgress::register();
		}
	}

	/**
	 * 服務角色與 capability（idempotent，roles 快取在 alloptions，檢查近乎免費）。
	 * sanji 服務帳號＝一般社群成員 WP user ＋ 這個角色——最小權限，不是 administrator；
	 * administrator 同步拿 cap 供營運除錯。
	 */
	private static function ensure_roles(): void {
		if ( null === get_role( 'nakama_gam_service' ) ) {
			add_role(
				'nakama_gam_service',
				'Gamification Service',
				array(
					'read'           => true,
					Rest::CAP        => true,
				)
			);
		}

		$admin = get_role( 'administrator' );
		if ( $admin && ! $admin->has_cap( Rest::CAP ) ) {
			$admin->add_cap( Rest::CAP );
		}
	}
}
