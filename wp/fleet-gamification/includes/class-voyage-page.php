<?php
/**
 * 航海日誌——profile tab 注入＋in-SPA 嵌入面板＋獨立頁。
 *
 * 三層架構（修修 2026-08-24：內容要顯示在 profile tabs 底下同一個 block）：
 *  1. Tab 注入：`fluent_community/profile_view_data`（ProfileController.php:150）
 *     純 PHP append nav item；不帶 route（vendor Vue router 無此路由名）。
 *  2. 嵌入面板：`fluent_community/portal_header` 印一段小 JS——攔截本 tab 的點擊、
 *     隱藏 tab bar 之後的同層內容、fetch `?embed=1` fragment 塞進同位置；
 *     點其他 tab／瀏覽器上下頁／SPA 換頁（pushState）都會先還原。
 *     選擇「隱藏同層兄弟」而非碰 Vue 管的節點內部——結構無關、Vue re-render 也安全。
 *  3. 獨立頁 `/?fleet_voyage={username}`：JS 失效時的 fallback（tab 的 href 本身），
 *     也可直接分享連結。
 *
 * 資料規則：任何登入成員可看任何人摘要（XP／貝里）；入帳明細只有本人可見。
 * gam_enabled 關閉時三層全 inert。
 */

declare( strict_types=1 );

namespace NakamaGam;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class VoyagePage {

	private const QUERY_KEY = 'fleet_voyage';

	/** 入帳來源 → 顯示名稱（呈現層；帳本只存英文 source key） */
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
		add_action( 'fluent_community/portal_header', array( self::class, 'print_tab_script' ) );
	}

	/* ─────────────────────────── tab 注入 ─────────────────────────── */

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
			// 刻意不帶 route：vendor Vue router 沒這個路由名。
		);

		return $profile;
	}

	/* ────────────────────────── 嵌入面板 JS ───────────────────────── */

	/** 印在 portal <head>（portal.php:3 的 do_action）。結構無關、失敗即退回整頁連結。 */
	public static function print_tab_script(): void {
		if ( ! Settings::enabled() ) {
			return;
		}
		?>
<script id="nakama-gam-voyage-tab">
(function(){
	var ACTIVE=false, saved=[], prevActive=null, box=null;
	function tabA(){ var li=document.querySelector('li.fcom_profile_voyage'); return li&&li.querySelector('a'); }
	function deactivate(){
		if(!ACTIVE) return; ACTIVE=false;
		if(box){ box.remove(); box=null; }
		saved.forEach(function(p){ p.el.style.display=p.d; }); saved=[];
		var a=tabA(); if(a){ a.classList.remove('router-link-active','router-link-exact-active'); }
		if(prevActive){ prevActive.classList.add('router-link-active','router-link-exact-active'); prevActive=null; }
	}
	function activate(url){
		var ul=document.querySelector('ul.fcom_profile_nav'); if(!ul){ window.location.href=url; return; }
		var card=ul.closest('div')||ul.parentElement, parent=card.parentElement;
		var sib=card.nextElementSibling;
		while(sib){ saved.push({el:sib,d:sib.style.display}); sib.style.display='none'; sib=sib.nextElementSibling; }
		box=document.createElement('div'); box.className='nakama-voyage-pane';
		box.innerHTML='<div style="padding:2.5rem;text-align:center;opacity:.55">載入航海日誌…</div>';
		parent.appendChild(box);
		var pa=ul.querySelector('a.router-link-exact-active')||ul.querySelector('a.router-link-active');
		if(pa && pa!==tabA()){ prevActive=pa; pa.classList.remove('router-link-active','router-link-exact-active'); }
		var a=tabA(); if(a){ a.classList.add('router-link-active','router-link-exact-active'); }
		ACTIVE=true;
		fetch(url+(url.indexOf('?')>-1?'&':'?')+'embed=1',{credentials:'same-origin'})
			.then(function(r){ if(!r.ok){ throw new Error(r.status); } return r.text(); })
			.then(function(h){ if(ACTIVE&&box){ box.innerHTML=h; } })
			.catch(function(){ if(ACTIVE&&box){ box.innerHTML='<div style="padding:2rem;text-align:center">載入失敗，<a href="'+url+'">改用完整頁開啟 →</a></div>'; } });
	}
	document.addEventListener('click',function(e){
		if(!e.target||!e.target.closest) return;
		var mine=e.target.closest('li.fcom_profile_voyage a');
		if(mine){ e.preventDefault(); e.stopPropagation(); if(!ACTIVE){ activate(mine.getAttribute('href')); } return; }
		if(ACTIVE && e.target.closest('ul.fcom_profile_nav a')){ deactivate(); }
	}, true);
	window.addEventListener('popstate', deactivate);
	['pushState','replaceState'].forEach(function(k){
		var o=history[k];
		history[k]=function(){ var r=o.apply(this,arguments); try{ deactivate(); }catch(_e){} return r; };
	});
})();
</script>
		<?php
	}

	/* ─────────────────────────── 頁面渲染 ─────────────────────────── */

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

		if ( ! headers_sent() ) {
			nocache_headers();
			header( 'X-LiteSpeed-Cache-Control: no-cache' );
			header( 'Content-Type: text/html; charset=utf-8' );
		}
		do_action( 'litespeed_control_set_nocache', 'nakama-gam voyage page' );

		$embed = isset( $_GET['embed'] ) && '1' === $_GET['embed'];

		// phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped — 內部逐項 escape
		echo $embed
			? self::render_fragment( (int) $target->user_id, get_current_user_id() )
			: self::render_html( (int) $target->user_id, get_current_user_id() );
		exit;
	}

	/** 嵌入 fragment：透明底、繼承 portal 主題色（currentColor＋透明度），選擇器全鎖 .nkv 之下。 */
	public static function render_fragment( int $target_user_id, int $viewer_user_id ): string {
		$d = self::collect_data( $target_user_id, $viewer_user_id );

		ob_start();
		?>
<div class="nkv">
<style>
	.nkv{ font-size:14.5px; line-height:1.8; margin-top:14px }
	.nkv .nkv-cards{ display:grid; grid-template-columns:1fr 1fr; gap:.7rem; margin-bottom:1.1rem }
	.nkv .nkv-card{ border:1px solid rgba(125,125,125,.22); border-radius:12px; padding:.9rem 1.1rem }
	.nkv .nkv-k{ font-size:.72rem; opacity:.6; letter-spacing:.05em }
	.nkv .nkv-v{ font-size:1.5rem; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.5 }
	.nkv .nkv-v small{ font-size:.75rem; font-weight:400; opacity:.55; margin-left:.15rem }
	.nkv h3{ font-size:.9rem; margin:0 0 .5rem }
	.nkv table{ width:100%; border-collapse:collapse; font-size:.83rem;
		border:1px solid rgba(125,125,125,.22); border-radius:12px; overflow:hidden }
	.nkv td{ padding:.45rem .85rem; border-top:1px solid rgba(125,125,125,.16) }
	.nkv tr:first-child td{ border-top:0 }
	.nkv .nkv-amt{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; font-weight:600 }
	.nkv .nkv-amt.neg{ color:#c0563b }
	.nkv .nkv-dt{ opacity:.55; font-size:.76rem; white-space:nowrap; text-align:right }
	.nkv .nkv-note{ opacity:.6; font-size:.78rem; margin-top:.8rem }
</style>
	<div class="nkv-cards">
		<div class="nkv-card"><div class="nkv-k">經驗值</div><div class="nkv-v"><?php echo esc_html( number_format_i18n( $d['xp'] ) ); ?><small>XP</small></div></div>
		<div class="nkv-card"><div class="nkv-k">貝里</div><div class="nkv-v"><?php echo esc_html( number_format_i18n( $d['berry'] ) ); ?></div></div>
	</div>
	<?php echo self::ledger_block_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
</div>
		<?php
		return (string) ob_get_clean();
	}

	/** 獨立完整頁（fallback／可分享連結）。 */
	public static function render_html( int $target_user_id, int $viewer_user_id ): string {
		$d = self::collect_data( $target_user_id, $viewer_user_id );

		$back_url = '';
		if ( '' !== $d['username'] && class_exists( '\FluentCommunity\App\Services\Helper' ) ) {
			$back_url = (string) \FluentCommunity\App\Services\Helper::baseUrl( 'u/' . $d['username'] . '/' );
		}

		ob_start();
		?>
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title><?php echo esc_html( $d['name'] ); ?>的航海日誌</title>
<style>
	:root{ --bg:#f6f6f4; --card:#ffffff; --ink:#23211e; --dim:#71706c; --line:rgba(60,60,55,.16); --accent:#e8913f }
	@media (prefers-color-scheme: dark){
		:root{ --bg:#1d1f23; --card:#26282d; --ink:#e8e7e4; --dim:#9b9a96; --line:rgba(230,230,225,.14) }
	}
	*{ box-sizing:border-box }
	body{ margin:0; background:var(--bg); color:var(--ink);
		font-family:"LINE Seed TW","Noto Sans TC","PingFang TC",ui-sans-serif,system-ui,sans-serif;
		line-height:1.8; font-size:15px }
	.wrap{ max-width:34rem; margin:0 auto; padding:2.2rem 1.2rem 4rem }
	a{ color:inherit }
	.back{ font-size:.85rem; color:var(--dim); text-decoration:none }
	.back:hover{ color:var(--accent) }
	header.h{ display:flex; align-items:center; gap:1rem; margin:1.4rem 0 1.6rem }
	.ava{ width:56px; height:56px; border-radius:50%; object-fit:cover; border:1px solid var(--line); background:#fff }
	h1{ font-size:1.25rem; margin:0; line-height:1.4 }
	.sub{ font-size:.8rem; color:var(--dim) }
	.nkv-cards{ display:grid; grid-template-columns:1fr 1fr; gap:.7rem; margin-bottom:1.8rem }
	.nkv-card{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:1rem 1.2rem }
	.nkv-k{ font-size:.75rem; color:var(--dim); letter-spacing:.05em }
	.nkv-v{ font-size:1.55rem; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.5 }
	.nkv-v small{ font-size:.8rem; font-weight:400; color:var(--dim); margin-left:.2rem }
	h3{ font-size:.95rem; margin:0 0 .6rem }
	table{ width:100%; border-collapse:collapse; font-size:.85rem; background:var(--card);
		border:1px solid var(--line); border-radius:12px; overflow:hidden }
	td{ padding:.5rem .9rem; border-top:1px solid var(--line) }
	tr:first-child td{ border-top:0 }
	.nkv-amt{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; font-weight:600 }
	.nkv-amt.neg{ color:#c0563b }
	.nkv-dt{ color:var(--dim); font-size:.78rem; white-space:nowrap; text-align:right }
	.nkv-note{ color:var(--dim); font-size:.8rem; margin-top:1rem }
</style>
</head>
<body>
<div class="wrap">
	<?php if ( $back_url ) : ?>
		<a class="back" href="<?php echo esc_url( $back_url ); ?>">← 返回個人檔案</a>
	<?php endif; ?>
	<header class="h">
		<?php if ( $d['avatar'] ) : ?>
			<img class="ava" src="<?php echo esc_url( $d['avatar'] ); ?>" alt="">
		<?php endif; ?>
		<div>
			<h1><?php echo esc_html( $d['name'] ); ?> 的航海日誌</h1>
			<div class="sub">⚓ 自由艦隊</div>
		</div>
	</header>
	<div class="nkv-cards">
		<div class="nkv-card"><div class="nkv-k">經驗值</div><div class="nkv-v"><?php echo esc_html( number_format_i18n( $d['xp'] ) ); ?><small>XP</small></div></div>
		<div class="nkv-card"><div class="nkv-k">貝里</div><div class="nkv-v"><?php echo esc_html( number_format_i18n( $d['berry'] ) ); ?></div></div>
	</div>
	<?php echo self::ledger_block_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
</div>
</body>
</html>
		<?php
		return (string) ob_get_clean();
	}

	/* ─────────────────────────── 共用底層 ─────────────────────────── */

	/** @return array{name:string,username:string,avatar:string,xp:int,berry:int,is_self:bool,rows:array} */
	private static function collect_data( int $target_user_id, int $viewer_user_id ): array {
		global $wpdb;

		$profile = \FluentCommunity\App\Models\XProfile::where( 'user_id', $target_user_id )->first();
		$bal     = $wpdb->get_row(
			$wpdb->prepare(
				'SELECT xp_total, berry_balance FROM ' . Ledger::balances_table() . ' WHERE user_id = %d',
				$target_user_id
			),
			ARRAY_A
		);

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

		return array(
			'name'     => $profile ? (string) $profile->display_name : '',
			'username' => $profile ? (string) $profile->username : '',
			'avatar'   => $profile ? (string) $profile->avatar : '',
			'xp'       => $bal ? (int) $bal['xp_total'] : 0,
			'berry'    => $bal ? (int) $bal['berry_balance'] : 0,
			'is_self'  => $is_self,
			'rows'     => is_array( $rows ) ? $rows : array(),
		);
	}

	/** 明細區塊（本人）／隱藏說明（他人）。輸出已逐項 escape。 */
	private static function ledger_block_html( array $d ): string {
		if ( ! $d['is_self'] ) {
			return '<p class="nkv-note">入帳明細只有本人看得到。</p>';
		}

		if ( ! $d['rows'] ) {
			return '<p class="nkv-note">還沒有入帳紀錄——發一篇有價值的文章，讓夥伴的讚替你開帳。</p>';
		}

		$out = '<h3>最近入帳</h3><table>';
		foreach ( $d['rows'] as $r ) {
			$xp_v  = (int) $r['xp'];
			$label = self::SOURCE_LABELS[ (string) $r['source'] ] ?? (string) $r['source'];
			$out  .= '<tr><td>' . esc_html( $label ) . '</td>'
				. '<td class="nkv-amt' . ( $xp_v < 0 ? ' neg' : '' ) . '">' . esc_html( ( $xp_v > 0 ? '+' : '' ) . number_format_i18n( $xp_v ) ) . ' XP</td>'
				. '<td class="nkv-dt">' . esc_html( mysql2date( 'n/j H:i', (string) $r['created_at'] ) ) . '</td></tr>';
		}
		$out .= '</table><p class="nkv-note">帳目可查、可申訴——有疑問直接私訊 Sanji 或船長。</p>';
		return $out;
	}
}
