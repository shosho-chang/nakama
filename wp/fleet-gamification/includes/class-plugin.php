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
		);
		foreach ( $optional as $file => $class ) {
			if ( file_exists( NAKAMA_GAM_PATH . $file ) ) {
				require_once NAKAMA_GAM_PATH . $file;
			}
		}

		// REST 永遠註冊（/health 不受開關影響；業務端點內部自行檢查開關回 503）。
		if ( class_exists( '\NakamaGam\Rest' ) ) {
			Rest::register();
		}

		if ( ! Settings::enabled() ) {
			return;
		}

		if ( class_exists( '\NakamaGam\Capture' ) ) {
			Capture::register();
		}
	}
}
