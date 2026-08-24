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
		// 兩種 portal 模板：portal.php(headless) 只呼叫 portal_header；
		// portal_page.php 只呼叫 before_portal_dom——兩個都掛，print 端防重複。
		add_action( 'fluent_community/portal_header', array( self::class, 'print_tab_script' ) );
		add_action( 'fluent_community/before_portal_dom', array( self::class, 'print_tab_script' ) );
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

		$base = class_exists( '\FluentCommunity\App\Services\Helper' )
			? (string) \FluentCommunity\App\Services\Helper::baseUrl( 'u/' . $username . '/voyage' )
			: home_url( '/?' . self::QUERY_KEY . '=' . rawurlencode( $username ) );

		$profile['profile_navs'][] = array(
			'slug'          => 'voyage_log',
			'title'         => '航海日誌',
			'url'           => esc_url_raw( $base ), // 真路徑 /deck/u/{name}/voyage——JS 接手 pushState
			'wrapper_class' => 'fcom_profile_voyage',
			// 刻意不帶 route：vendor Vue router 沒這個路由名。
		);

		return $profile;
	}

	/* ────────────────────────── 嵌入面板 JS ───────────────────────── */

	/** 印在 portal <head>（portal.php:3 的 do_action）。結構無關、失敗即退回整頁連結。 */
	public static function print_tab_script(): void {
		static $printed = false;
		if ( $printed || ! Settings::enabled() ) {
			return;
		}
		$printed = true;
		?>
<script id="nakama-gam-voyage-tab">
(function(){
	/* 航海日誌：真路由 SPA 面板。
	   URL 形如 {portal}/u/{name}/voyage；內容取代 section.fcom_space_container（灰色 block）。
	   sync() 是唯一真相：依 location.pathname 決定 activate/deactivate——
	   點擊、上下頁、SPA 換頁（pushState wrapper）全部收斂到它。 */
	var RE=/\/u\/([^\/]+)\/voyage\/?$/;
	var ACTIVE=false, hidden=null, prevActive=null, box=null;

	function tabA(){ var li=document.querySelector('li.fcom_profile_voyage'); return li&&li.querySelector('a'); }
	function sectionEl(){
		var h=document.querySelector('div.object_header');
		var s=h&&h.nextElementSibling;
		return (s&&s.tagName==='SECTION')?s:null;
	}
	function deactivate(){
		if(!ACTIVE) return; ACTIVE=false;
		if(box){ box.remove(); box=null; }
		if(hidden){ hidden.el.style.display=hidden.d; hidden=null; }
		var a=tabA(); if(a){ a.classList.remove('router-link-active','router-link-exact-active'); }
		if(prevActive){ prevActive.classList.add('router-link-active','router-link-exact-active'); prevActive=null; }
	}
	function activate(username){
		var sec=sectionEl(); if(!sec) return false;
		hidden={el:sec,d:sec.style.display}; sec.style.display='none';
		box=document.createElement('section');
		box.className=sec.className+' nakama-voyage-pane'; // 繼承 section 版型（el-container 是 flex）
		box.innerHTML='<main class="el-main fcom_main"><div class="nkv" style="text-align:center;opacity:.55">載入航海日誌…</div></main>';
		sec.parentElement.insertBefore(box, sec);
		var ul=document.querySelector('ul.fcom_profile_nav');
		if(ul){
			var pa=ul.querySelector('a.router-link-exact-active')||ul.querySelector('a.router-link-active');
			if(pa && pa!==tabA()){ prevActive=pa; pa.classList.remove('router-link-active','router-link-exact-active'); }
		}
		var a=tabA(); if(a){ a.classList.add('router-link-active','router-link-exact-active'); }
		ACTIVE=true;
		fetch('/?fleet_voyage='+encodeURIComponent(username)+'&embed=1',{credentials:'same-origin'})
			.then(function(r){ if(!r.ok){ throw new Error(r.status); } return r.text(); })
			.then(function(h){ if(ACTIVE&&box){ box.innerHTML='<main class="el-main fcom_main">'+h+'</main>'; } })
			.catch(function(){ if(ACTIVE&&box){ box.innerHTML='<main class="el-main fcom_main"><div class="nkv" style="text-align:center">載入失敗，<a href="/?fleet_voyage='+encodeURIComponent(username)+'">改用完整頁開啟 →</a></div></main>'; } });
		return true;
	}
	function sync(){
		var m=location.pathname.match(RE);
		if(m){ if(!ACTIVE){ activate(decodeURIComponent(m[1])); } }
		else if(ACTIVE){ deactivate(); }
	}

	document.addEventListener('click',function(e){
		if(!e.target||!e.target.closest) return;
		var mine=e.target.closest('li.fcom_profile_voyage a');
		if(mine){
			e.preventDefault(); e.stopPropagation();
			if(!ACTIVE){ history.pushState(null,'',mine.getAttribute('href')); sync(); }
			return;
		}
		if(ACTIVE){
			var other=e.target.closest('ul.fcom_profile_nav a');
			if(other){
				var targetPath=new URL(other.href, location.origin).pathname;
				deactivate(); // 先還原，再讓 Vue 接手路由
				/* Vue 內部路由若「已在」目標 tab（進 voyage 前的那個 tab），它視為同路由
				   不導航也不改網址 → 網址卡在 /voyage。給它 80ms，沒動作就由我們矯正。 */
				setTimeout(function(){
					if(RE.test(location.pathname) && !ACTIVE){ _rawReplace(history.state,'',targetPath); }
				},80);
			}
		}
	}, true);

	/* head script 比 vendor bundle 先註冊——同 target 依註冊順序執行：
	   落在 /voyage 的 popstate 先到我們手上，擋掉 Vue router 的處理
	   （它不認識這條路由，會把 URL 替換回 base）；其他路徑放行給 Vue。 */
	window.addEventListener('popstate', function(e){
		if(RE.test(location.pathname)){ e.stopImmediatePropagation(); }
		setTimeout(sync,0);
	});

	/* 深連結／重新整理（要在 history wrapper 安裝前用原生方法做）：
	   vendor router 不認識 /voyage → 先把 URL 換回 profile 讓它正常渲染，
	   等 profile DOM 就緒再 push 回 /voyage 並接管。逾時退回獨立頁。 */
	var m0=location.pathname.match(RE);
	var _rawReplace=history.replaceState.bind(history), _rawPush=history.pushState.bind(history);
	if(m0){
		var user0=decodeURIComponent(m0[1]);
		var voyagePath=location.pathname;
		_rawReplace(null,'',location.pathname.replace(/voyage\/?$/,''));
		var waited=0, t=setInterval(function(){
			waited+=200;
			if(sectionEl()&&document.querySelector('ul.fcom_profile_nav')){
				clearInterval(t);
				_rawPush(null,'',voyagePath);
				sync();
			}else if(waited>=8000){
				clearInterval(t);
				window.location.href='/?fleet_voyage='+encodeURIComponent(user0);
			}
		},200);
	}

	/* vue-router push 前會 replaceState 覆寫「當前」entry——在 voyage 上時這會把
	   我們的 URL 改掉、害 back/forward 跳過本頁。攔下：URL 保持 voyage，
	   state 物件原樣放行（Vue 之後 back 回這個 entry 時，popstate 已被上面擋住）。 */
	history.replaceState=function(state,title,url){
		if(RE.test(location.pathname) && url!=null && !RE.test(String(url))){
			url=location.pathname+location.search;
		}
		var r=_rawReplace(state,title,url);
		try{ setTimeout(sync,0); }catch(_e){}
		return r;
	};
	history.pushState=function(){
		var r=_rawPush.apply(null,arguments);
		try{ setTimeout(sync,0); }catch(_e){}
		return r;
	};
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
	/* 與其他 tab 的內容卡同配方（vendor .about_wrap 規則：var 主題變數，暗色自動跟隨） */
	.nkv{ font-size:14.5px; line-height:1.8; display:block;
		background:var(--fcom-primary-bg, white); color:var(--fcom-menu-text, #545861);
		border-radius:5px; padding:20px; margin-bottom:20px }
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
