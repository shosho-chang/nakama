<?php
/**
 * 航海日誌——會員的 gamification 個人頁＋profile tab 注入。
 *
 * Tab 注入：`fluent_community/profile_view_data` filter（ProfileController.php:150，
 * 在 vendor 組完 profile_navs 之後執行）——純 PHP append 一個 nav item，零 JS。
 * 自訂 tab 的 route.name 不存在於 vendor Vue router，因此 nav item 不帶 route、
 * 只帶 url → 前端渲染成一般連結，導向本 class 渲染的獨立頁。
 *
 * 獨立頁：`/?fleet_voyage={username}`（template_redirect 攔截）。
 * - 任何登入成員可看任何人的摘要（XP／貝里）——與榜單文化一致
 * - 只有本人看得到入帳明細（帳目透明承諾的落地：可查、可申訴）
 * - gam_enabled 關閉時整條路徑 inert（止血面）
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class VoyagePage {

	private const QUERY_KEY = 'fleet_voyage';

	/** 入帳來源 → 顯示名稱（呈現層；帳本本身只存英文 source key） */
	private const SOURCE_LABELS = array(
		'checkin_day'       => '挑戰打卡',
		'streak_7'          => '連續 7 天獎',
		'full_attendance'   => '全勤獎',
		'like_received'     => '貼文被讚',
		'bookmark_received' => '貼文被收藏',
		'lesson_completed'  => '完成課程單元',
		'course_completed'  => '完成整門課程',
		'quiz_passed'       => '通過測驗',
		'presence_day'      => '每日登入',
		'surprise'          => '驚喜',
		'captain_award'     => '船長特別獎',
		'reversal'          => '沖正',
	);

	public static function register(): void {
		add_filter( 'fluent_community/profile_view_data', array( self::class, 'inject_profile_tab' ), 10, 3 );
		add_action( 'template_redirect', array( self::class, 'maybe_render_page' ) );
	}

	/**
	 * @param array $profile  vendor 組好的 profile payload（含 profile_navs）
	 * @param object $xprofile 被瀏覽者的 XProfile
	 * @param bool $is_admin
	 * @return array
	 */
	public static function inject_profile_tab( $profile, $xprofile, $is_admin ) {
		if ( ! is_array( $profile ) || empty( $profile['profile_navs'] ) || ! is_array( $profile['profile_navs'] ) ) {
			return $profile;
		}
		$username = (string) ( $xprofile->username ?? '' );
		if ( '' === $username ) {
			return $profile;
		}

		$profile['profile_navs'][] = array(
			'slug'          => 'voyage_log',
			'title'         => '航海日誌',
			'url'           => esc_url_raw( home_url( '/?' . self::QUERY_KEY . '=' . rawurlencode( $username ) ) ),
			'wrapper_class' => 'fcom_profile_voyage',
			// 刻意不帶 route：vendor Vue router 沒有這個路由名，帶了會壞導航。
		);

		return $profile;
	}

	public static function maybe_render_page(): void {
		$username = isset( $_GET[ self::QUERY_KEY ] ) ? sanitize_title( wp_unslash( (string) $_GET[ self::QUERY_KEY ] ) ) : '';
		if ( '' === $username ) {
			return;
		}

		if ( ! is_user_logged_in() ) {
			wp_safe_redirect( wp_login_url( home_url( '/?' . self::QUERY_KEY . '=' . rawurlencode( $username ) ) ) );
			exit;
		}

		if ( ! Settings::enabled() || ! class_exists( '\FluentCommunity\App\Models\XProfile' ) ) {
			wp_die( esc_html( '航海日誌目前未開放。' ), 404 );
		}

		$target = \FluentCommunity\App\Models\XProfile::where( 'username', $username )->first();
		if ( ! $target ) {
			wp_die( esc_html( '找不到這位夥伴。' ), 404 );
		}

		// LiteSpeed：個人頁絕不可快取（登入者各自不同）
		if ( ! headers_sent() ) {
			nocache_headers();
			header( 'X-LiteSpeed-Cache-Control: no-cache' );
		}
		do_action( 'litespeed_control_set_nocache', 'nakama-gam voyage page' );

		// phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped — render_html 內部逐項 escape
		echo self::render_html( (int) $target->user_id, get_current_user_id() );
		exit;
	}

	/** 純函式渲染（wp eval 可直接呼叫驗證）。 */
	public static function render_html( int $target_user_id, int $viewer_user_id ): string {
		global $wpdb;

		$profile = \FluentCommunity\App\Models\XProfile::where( 'user_id', $target_user_id )->first();
		$name    = $profile ? (string) $profile->display_name : '';
		$uname   = $profile ? (string) $profile->username : '';
		$avatar  = $profile ? (string) $profile->avatar : '';

		$bal   = $wpdb->get_row(
			$wpdb->prepare(
				'SELECT xp_total, berry_balance FROM ' . Ledger::balances_table() . ' WHERE user_id = %d',
				$target_user_id
			),
			ARRAY_A
		);
		$xp    = $bal ? (int) $bal['xp_total'] : 0;
		$berry = $bal ? (int) $bal['berry_balance'] : 0;

		$is_self = $viewer_user_id === $target_user_id;
		$rows    = array();
		if ( $is_self ) {
			$rows = $wpdb->get_results(
				$wpdb->prepare(
					'SELECT xp, berry, source, reason, created_at FROM ' . Ledger::grants_table() .
					' WHERE user_id = %d ORDER BY id DESC LIMIT 20',
					$target_user_id
				),
				ARRAY_A
			);
		}

		$back_url = '';
		if ( '' !== $uname && class_exists( '\FluentCommunity\App\Services\Helper' ) ) {
			$back_url = (string) \FluentCommunity\App\Services\Helper::baseUrl( 'u/' . $uname . '/' );
		}

		ob_start();
		?>
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title><?php echo esc_html( $name ); ?>的航海日誌</title>
<style>
	:root{
		--bg:#f6f6f4; --card:#ffffff; --ink:#23211e; --dim:#71706c;
		--line:rgba(60,60,55,.16); --accent:#e8913f;
	}
	@media (prefers-color-scheme: dark){
		:root{ --bg:#1d1f23; --card:#26282d; --ink:#e8e7e4; --dim:#9b9a96; --line:rgba(230,230,225,.14); }
	}
	*{ box-sizing:border-box }
	body{
		margin:0; background:var(--bg); color:var(--ink);
		font-family:"LINE Seed TW","Noto Sans TC","PingFang TC",ui-sans-serif,system-ui,sans-serif;
		line-height:1.8; font-size:15px;
	}
	.wrap{ max-width:34rem; margin:0 auto; padding:2.2rem 1.2rem 4rem }
	a{ color:inherit }
	.back{ font-size:.85rem; color:var(--dim); text-decoration:none }
	.back:hover{ color:var(--accent) }
	header.h{ display:flex; align-items:center; gap:1rem; margin:1.4rem 0 1.6rem }
	.ava{ width:56px; height:56px; border-radius:50%; object-fit:cover; border:1px solid var(--line); background:#fff }
	h1{ font-size:1.25rem; margin:0; line-height:1.4 }
	.sub{ font-size:.8rem; color:var(--dim) }
	.cards{ display:grid; grid-template-columns:1fr 1fr; gap:.7rem; margin-bottom:1.8rem }
	.card{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:1rem 1.2rem }
	.card .k{ font-size:.75rem; color:var(--dim); letter-spacing:.05em }
	.card .v{ font-size:1.55rem; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.5 }
	.card .v small{ font-size:.8rem; font-weight:400; color:var(--dim); margin-left:.2rem }
	h2{ font-size:.95rem; margin:0 0 .6rem }
	table{ width:100%; border-collapse:collapse; font-size:.85rem; background:var(--card);
		border:1px solid var(--line); border-radius:12px; overflow:hidden }
	td{ padding:.5rem .9rem; border-top:1px solid var(--line) }
	tr:first-child td{ border-top:0 }
	.amt{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; font-weight:600 }
	.amt.neg{ color:#c0563b }
	.dt{ color:var(--dim); font-size:.78rem; white-space:nowrap; text-align:right }
	.note{ color:var(--dim); font-size:.8rem; margin-top:1rem }
</style>
</head>
<body>
<div class="wrap">
	<?php if ( $back_url ) : ?>
		<a class="back" href="<?php echo esc_url( $back_url ); ?>">← 返回個人檔案</a>
	<?php endif; ?>

	<header class="h">
		<?php if ( $avatar ) : ?>
			<img class="ava" src="<?php echo esc_url( $avatar ); ?>" alt="">
		<?php endif; ?>
		<div>
			<h1><?php echo esc_html( $name ); ?> 的航海日誌</h1>
			<div class="sub">⚓ 自由艦隊</div>
		</div>
	</header>

	<div class="cards">
		<div class="card"><div class="k">經驗值</div><div class="v"><?php echo esc_html( number_format_i18n( $xp ) ); ?><small>XP</small></div></div>
		<div class="card"><div class="k">貝里</div><div class="v"><?php echo esc_html( number_format_i18n( $berry ) ); ?></div></div>
	</div>

	<?php if ( $is_self ) : ?>
		<h2>最近入帳</h2>
		<?php if ( $rows ) : ?>
			<table>
				<?php foreach ( $rows as $r ) :
					$xp_v   = (int) $r['xp'];
					$label  = self::SOURCE_LABELS[ (string) $r['source'] ] ?? (string) $r['source'];
					?>
					<tr>
						<td><?php echo esc_html( $label ); ?></td>
						<td class="amt<?php echo $xp_v < 0 ? ' neg' : ''; ?>"><?php echo esc_html( ( $xp_v > 0 ? '+' : '' ) . number_format_i18n( $xp_v ) ); ?> XP</td>
						<td class="dt"><?php echo esc_html( mysql2date( 'n/j H:i', (string) $r['created_at'] ) ); ?></td>
					</tr>
				<?php endforeach; ?>
			</table>
			<p class="note">帳目可查、可申訴——有疑問直接私訊 Sanji 或船長。</p>
		<?php else : ?>
			<p class="note">還沒有入帳紀錄——發一篇有價值的文章，讓夥伴的讚替你開帳。</p>
		<?php endif; ?>
	<?php else : ?>
		<p class="note">入帳明細只有本人看得到。</p>
	<?php endif; ?>
</div>
</body>
</html>
		<?php
		return (string) ob_get_clean();
	}
}
