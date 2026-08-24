<?php
/**
 * Portal UI——會員面的最小顯示：側欄「我的航海帳」卡片（自己的 XP 與貝里）。
 *
 * 掛點：`fluent_community/portal_sidebar`（app/Views/portal/portal.php:9 直接
 * do_action 於側欄 PHP 模板內——不碰 Vue、不注入 JS，vendor 升級面最小）。
 * OptionController.php:37 的 'ajax' 情境（側欄重取）同樣會經過本 handler。
 *
 * 設計約束：
 * - 只給登入者看**自己的**數字（呈現層；排行榜刻意不在此 slice——修修 2026-08-24 裁決）
 * - 讀 balances 投影（PK 單列 SELECT，零 join）
 * - 色彩全部繼承 portal 主題（currentColor＋透明度），不硬編色票——
 *   卡片要融入 FluentCommunity 主題的亮／暗模式，不是套 Nakama 站的 design system
 * - gam_enabled 關閉時整張卡片消失（止血開關的 UI 面）
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class PortalUi {

	public static function register(): void {
		add_action( 'fluent_community/portal_sidebar', array( self::class, 'render_wallet_card' ), 10, 1 );
	}

	/** @param string $context 'headless' | 'ajax'（vendor 傳入，目前兩者同渲染） */
	public static function render_wallet_card( $context = '' ): void {
		if ( ! Settings::enabled() || ! is_user_logged_in() ) {
			return;
		}

		global $wpdb;
		$row = $wpdb->get_row(
			$wpdb->prepare(
				'SELECT xp_total, berry_balance FROM ' . Ledger::balances_table() . ' WHERE user_id = %d',
				get_current_user_id()
			),
			ARRAY_A
		);
		$xp    = $row ? (int) $row['xp_total'] : 0;
		$berry = $row ? (int) $row['berry_balance'] : 0;

		?>
		<div class="nakama-gam-wallet">
			<div class="nakama-gam-wallet__label">⚓ 我的航海帳</div>
			<div class="nakama-gam-wallet__nums">
				經驗值 <b><?php echo esc_html( number_format_i18n( $xp ) ); ?></b>
				<span class="nakama-gam-wallet__sep">·</span>
				貝里 <b><?php echo esc_html( number_format_i18n( $berry ) ); ?></b>
			</div>
		</div>
		<style>
			.nakama-gam-wallet{
				margin: 8px 0 12px;
				padding: 10px 14px;
				border: 1px solid rgba(125,125,125,.22);
				border-radius: 10px;
				font-size: 13px;
				line-height: 1.7;
			}
			.nakama-gam-wallet__label{
				font-size: 11.5px;
				letter-spacing: .04em;
				opacity: .62;
				margin-bottom: 2px;
			}
			.nakama-gam-wallet__nums b{
				font-variant-numeric: tabular-nums;
				font-weight: 600;
			}
			.nakama-gam-wallet__sep{
				opacity: .35;
				margin: 0 6px;
			}
		</style>
		<?php
	}
}
