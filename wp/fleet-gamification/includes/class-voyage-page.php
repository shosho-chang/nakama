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
 * 資料規則：任何登入成員可看任何人摘要（等級／XP）；入帳明細只有本人可見。
 * 貝里照記但**不顯示**——沒有商店的幣只會教會成員數字是裝飾（見 class-portal-ui.php 檔頭）。
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
		'comment_received'  => '貼文被留言',
		'bookmark_received' => '貼文被收藏',
		'lesson_completed'  => '完成課程單元',
		'course_completed'  => '完成整門課程',
		'quiz_passed'       => '通過測驗',
		'presence_day'      => '每日登入',
		'surprise'          => '驚喜',
		'captain_award'     => '艦長特別獎',
		'reversal'          => '沖正',
	);

	/**
	 * 篩選群組——這是**顯示分類**不是經濟規則（規則在 agents/sanji/rules.py）。
	 * 兩邊漂移由 tests/agents/test_sanji_rules.py 的 group 覆蓋測試抓。
	 */
	private const GROUP_LABELS = array(
		'all'       => '全部',
		'share'     => '分享',
		'challenge' => '挑戰',
		'learn'     => '課程',
		'other'     => '其他',
	);

	private const GROUP_SOURCES = array(
		'share'     => array( 'like_received', 'comment_received', 'bookmark_received' ),
		'challenge' => array( 'checkin_day', 'streak_7', 'full_attendance' ),
		'learn'     => array( 'lesson_completed', 'course_completed', 'quiz_passed' ),
		'other'     => array( 'presence_day', 'surprise', 'captain_award', 'reversal' ),
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
	var ACTIVE=false, hidden=null, prevActive=null, box=null, CURU='';

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
		ACTIVE=true; CURU=username;
		load(username,'all');
		return true;
	}
	/* 取 fragment 塞進 pane。group 是伺服器端過濾，所以換類型就是重取一次
	   （不是前端藏 row——20 筆視窗內藏 row 會讓「只看挑戰」看起來是 0 筆）。 */
	function load(u,group){
		if(!box) return;
		var url='/?fleet_voyage='+encodeURIComponent(u)+'&embed=1'
			+((group&&group!=='all')?('&group='+encodeURIComponent(group)):'');
		fetch(url,{credentials:'same-origin'})
			.then(function(r){ if(!r.ok){ throw new Error(r.status); } return r.text(); })
			.then(function(h){ if(ACTIVE&&box){ box.innerHTML='<main class="el-main fcom_main">'+h+'</main>'; } })
			.catch(function(){ if(ACTIVE&&box){ box.innerHTML='<main class="el-main fcom_main"><div class="nkv" style="text-align:center">載入失敗，<a href="/?fleet_voyage='+encodeURIComponent(u)+'">改用完整頁開啟 →</a></div></main>'; } });
	}
	function sync(){
		var m=location.pathname.match(RE);
		if(m){ if(!ACTIVE){ activate(decodeURIComponent(m[1])); } }
		else if(ACTIVE){ deactivate(); }
	}

	document.addEventListener('change',function(e){
		if(!e.target||!e.target.closest) return;
		var sel=e.target.closest('select[data-nkv-filter]');
		if(sel&&ACTIVE&&CURU){ load(CURU, sel.value); }
	});

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
		$group = isset( $_GET['group'] ) ? sanitize_key( wp_unslash( (string) $_GET['group'] ) ) : 'all';

		// phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped — 內部逐項 escape
		echo $embed
			? self::render_fragment( (int) $target->user_id, get_current_user_id(), $group )
			: self::render_html( (int) $target->user_id, get_current_user_id(), $group );
		exit;
	}

	/** 嵌入 fragment：透明底、繼承 portal 主題色（currentColor＋透明度），選擇器全鎖 .nkv 之下。 */
	public static function render_fragment( int $target_user_id, int $viewer_user_id, string $group = 'all' ): string {
		$d = self::collect_data( $target_user_id, $viewer_user_id, $group );

		ob_start();
		?>
<div class="nkv">
<style>
	/* 與其他 tab 的內容卡同配方（vendor .about_wrap 規則：var 主題變數，暗色自動跟隨） */
	.nkv{ font-size:14.5px; line-height:1.8; display:block;
		background:var(--fcom-primary-bg, white); color:var(--fcom-menu-text, #545861);
		border-radius:5px; padding:20px; margin-bottom:20px }
	.nkv .nkv-rank{ margin-bottom:1.15rem }
	.nkv .nkv-rank-head{ display:flex; align-items:baseline; gap:.55rem; flex-wrap:wrap; margin-bottom:.5rem }
	.nkv .nkv-lv{ font-size:.74rem; letter-spacing:.06em; opacity:.5; font-variant-numeric:tabular-nums }
	.nkv .nkv-title{ font-size:1.3rem; font-weight:700; line-height:1.35 }
	.nkv .nkv-next{ margin-left:auto; font-size:.78rem; opacity:.6; font-variant-numeric:tabular-nums }
	/* 橘只當線不當塊：4px 細規尺，不做大面積填色 */
	.nkv .nkv-bar{ height:4px; border-radius:4px; background:rgba(125,125,125,.2); overflow:hidden }
	.nkv .nkv-bar i{ display:block; height:100%; border-radius:4px; background:#e8913f;
		transition:width .6s cubic-bezier(.22,1,.36,1) }
	.nkv .nkv-rank--empty{ margin-bottom:.9rem }
	.nkv .nkv-rank--empty .nkv-note{ margin-top:0 }
	@media (prefers-reduced-motion: reduce){ .nkv .nkv-bar i{ transition:none } }
	.nkv .nkv-idt{ font-size:.72rem; line-height:1.5; padding:.1rem .55rem; border-radius:999px;
		letter-spacing:.05em; white-space:nowrap; align-self:center }
	.nkv .nkv-idt--full{ border:1px solid #e8913f; color:#e8913f }
	.nkv .nkv-idt--trainee{ border:1px solid rgba(125,125,125,.4); opacity:.65 }
	.nkv .nkv-declare{ margin:.7rem 0 0; font-size:.8rem }
	.nkv .nkv-rank-foot{ display:flex; align-items:baseline; gap:.6rem; margin-top:.55rem }
	.nkv .nkv-xp{ font-size:1.05rem; font-weight:700; font-variant-numeric:tabular-nums }
	.nkv .nkv-xp small{ font-size:.7rem; font-weight:400; opacity:.55; margin-left:.15rem }
	.nkv .nkv-lg-head{ display:flex; align-items:center; gap:.6rem; margin-bottom:.55rem }
	.nkv .nkv-lg-head h3{ margin:0 }
	.nkv .nkv-filter{ margin-left:auto; font:inherit; font-size:.76rem; line-height:1.5;
		padding:.18rem .45rem; border:1px solid rgba(125,125,125,.32); border-radius:8px;
		background:transparent; color:inherit; cursor:pointer }
	.nkv .nkv-act{ line-height:1.5 }
	.nkv .nkv-act-main{ display:block }
	.nkv .nkv-act-sub{ display:block; font-size:.72rem; opacity:.55; margin-top:.05rem }
	.nkv .nkv-lk{ color:inherit; text-decoration:none; border-bottom:1px solid rgba(125,125,125,.42) }
	.nkv .nkv-lk:hover{ color:#e8913f; border-bottom-color:#e8913f }
	.nkv h3{ font-size:.9rem; margin:0 0 .5rem }
	.nkv table{ width:100%; border-collapse:collapse; font-size:.83rem;
		border:1px solid rgba(125,125,125,.22); border-radius:12px; overflow:hidden }
	.nkv td{ padding:.5rem .85rem; border-top:1px solid rgba(125,125,125,.16); vertical-align:top }
	.nkv tr:first-child td{ border-top:0 }
	.nkv .nkv-amt{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; font-weight:600 }
	.nkv .nkv-amt.neg{ color:#c0563b }
	.nkv .nkv-dt{ opacity:.55; font-size:.76rem; white-space:nowrap; text-align:right }
	.nkv .nkv-note{ opacity:.6; font-size:.78rem; margin-top:.8rem }
</style>
	<?php echo self::rank_block_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
	<?php echo self::ledger_block_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
</div>
		<?php
		return (string) ob_get_clean();
	}

	/** 獨立完整頁（fallback／可分享連結）。 */
	public static function render_html( int $target_user_id, int $viewer_user_id, string $group = 'all' ): string {
		$d = self::collect_data( $target_user_id, $viewer_user_id, $group );

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
	.nkv-rank{ margin-bottom:1.5rem }
	.nkv-rank-head{ display:flex; align-items:baseline; gap:.55rem; flex-wrap:wrap; margin-bottom:.55rem }
	.nkv-lv{ font-size:.76rem; letter-spacing:.06em; color:var(--dim); font-variant-numeric:tabular-nums }
	.nkv-title{ font-size:1.4rem; font-weight:700; line-height:1.35 }
	.nkv-next{ margin-left:auto; font-size:.8rem; color:var(--dim); font-variant-numeric:tabular-nums }
	.nkv-bar{ height:4px; border-radius:4px; background:var(--line); overflow:hidden }
	.nkv-bar i{ display:block; height:100%; border-radius:4px; background:var(--accent);
		transition:width .6s cubic-bezier(.22,1,.36,1) }
	.nkv-rank--empty .nkv-note{ margin-top:0 }
	@media (prefers-reduced-motion: reduce){ .nkv-bar i{ transition:none } }
	.nkv-idt{ font-size:.75rem; line-height:1.5; padding:.12rem .6rem; border-radius:999px;
		letter-spacing:.05em; white-space:nowrap; align-self:center }
	.nkv-idt--full{ border:1px solid var(--accent); color:var(--accent) }
	.nkv-idt--trainee{ border:1px solid var(--line); color:var(--dim) }
	.nkv-declare{ margin:.75rem 0 0; font-size:.82rem }
	.nkv-rank-foot{ display:flex; align-items:baseline; gap:.6rem; margin-top:.6rem }
	.nkv-xp{ font-size:1.15rem; font-weight:700; font-variant-numeric:tabular-nums }
	.nkv-xp small{ font-size:.75rem; font-weight:400; color:var(--dim); margin-left:.15rem }
	.nkv-lg-head{ display:flex; align-items:center; gap:.6rem; margin-bottom:.6rem }
	.nkv-lg-head h3{ margin:0 }
	.nkv-filter{ margin-left:auto; font:inherit; font-size:.8rem; line-height:1.5;
		padding:.2rem .5rem; border:1px solid var(--line); border-radius:8px;
		background:var(--card); color:inherit; cursor:pointer }
	.nkv-act{ line-height:1.55 }
	.nkv-act-main{ display:block }
	.nkv-act-sub{ display:block; font-size:.75rem; color:var(--dim); margin-top:.05rem }
	.nkv-lk{ color:inherit; text-decoration:none; border-bottom:1px solid var(--line) }
	.nkv-lk:hover{ color:var(--accent); border-bottom-color:var(--accent) }
	h3{ font-size:.95rem; margin:0 0 .6rem }
	table{ width:100%; border-collapse:collapse; font-size:.85rem; background:var(--card);
		border:1px solid var(--line); border-radius:12px; overflow:hidden }
	td{ padding:.55rem .9rem; border-top:1px solid var(--line); vertical-align:top }
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
	<?php echo self::rank_block_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
	<?php echo self::ledger_block_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
</div>
<script>
document.addEventListener('change',function(e){
	var s=e.target&&e.target.closest&&e.target.closest('select[data-nkv-filter]');
	if(!s) return;
	var u=new URL(location.href);
	if(s.value==='all'){ u.searchParams.delete('group'); } else { u.searchParams.set('group',s.value); }
	location.href=u.toString();
});
</script>
</body>
</html>
		<?php
		return (string) ob_get_clean();
	}

	/* ─────────────────────────── 共用底層 ─────────────────────────── */

	/**
	 * @return array{name:string,username:string,avatar:string,xp:int,berry:int,
	 *               has_balance:bool,level:int,level_label:string,level_min_xp:int,
	 *               next_level_xp:int,next_level_label:string,is_self:bool,
	 *               identity:string,declare_url:string,
	 *               group:string,rows:array,feeds:array}
	 */
	private static function collect_data( int $target_user_id, int $viewer_user_id, string $group = 'all' ): array {
		global $wpdb;

		$profile = \FluentCommunity\App\Models\XProfile::where( 'user_id', $target_user_id )->first();
		$bal     = $wpdb->get_row(
			$wpdb->prepare(
				'SELECT xp_total, berry_balance, level, level_label, level_min_xp, next_level_xp, next_level_label' .
				' FROM ' . Ledger::balances_table() . ' WHERE user_id = %d',
				$target_user_id
			),
			ARRAY_A
		);

		$group   = isset( self::GROUP_LABELS[ $group ] ) ? $group : 'all';
		$is_self = $viewer_user_id === $target_user_id;

		// 身份三態：艦長（修修）／船長（發過啟航宣言）／見習船長（還沒）。
		// 身份是公開資訊（互稱文化的基礎），跟明細的本人限定無關。
		$identity = 'trainee';
		if ( user_can( $target_user_id, 'manage_options' ) ) {
			$identity = 'admiral';
		} elseif ( '' !== (string) get_user_meta( $target_user_id, 'nakama_gam_captain_since', true ) ) {
			$identity = 'captain';
		}

		// 見習船長（本人）看得到儀式引導：連去啟航宣言 space。
		$declare_url = '';
		if ( 'trainee' === $identity && $is_self && class_exists( FcBridge::class ) ) {
			$declare_url = FcBridge::space_permalink( Settings::declaration_space() );
		}
		$rows    = array();
		$feeds   = array();

		if ( $is_self ) {
			// LEFT JOIN events：帳目本身不存「為哪件事而發」，那是事件的職責。
			// 事件被清掉的舊帳仍要看得到，所以是 LEFT 不是 INNER。
			$sql    = 'SELECT g.xp, g.berry, g.source, g.reason, g.season, g.created_at,' .
				' e.event_type, e.object_type, e.object_id' .
				' FROM ' . Ledger::grants_table() . ' g' .
				' LEFT JOIN ' . Ledger::events_table() . ' e ON e.id = g.ref_event_id' .
				' WHERE g.user_id = %d';
			$params = array( $target_user_id );

			if ( isset( self::GROUP_SOURCES[ $group ] ) ) {
				$in     = implode( ', ', array_fill( 0, count( self::GROUP_SOURCES[ $group ] ), '%s' ) );
				$sql   .= " AND g.source IN ( $in )";
				$params = array_merge( $params, self::GROUP_SOURCES[ $group ] );
			}
			$sql .= ' ORDER BY g.id DESC LIMIT 30';

			$rows = $wpdb->get_results( $wpdb->prepare( $sql, ...$params ), ARRAY_A );
			$rows = is_array( $rows ) ? $rows : array();

			$feed_ids = array();
			foreach ( $rows as $r ) {
				if ( 'feed' === (string) $r['object_type'] ) {
					$feed_ids[] = (int) $r['object_id'];
				}
			}
			if ( $feed_ids && class_exists( FcBridge::class ) ) {
				$feeds = FcBridge::feed_digest( $feed_ids );
			}
		}

		return array(
			'name'     => $profile ? (string) $profile->display_name : '',
			'username' => $profile ? (string) $profile->username : '',
			'avatar'   => $profile ? (string) $profile->avatar : '',
			'xp'       => $bal ? (int) $bal['xp_total'] : 0,
			'berry'    => $bal ? (int) $bal['berry_balance'] : 0,
			// 等級帶全部由 Sanji 寫入——plugin 不知道曲線，只負責畫出來。
			'has_balance'      => (bool) $bal,
			'level'            => $bal ? (int) $bal['level'] : 0,
			'level_label'      => $bal ? (string) $bal['level_label'] : '',
			'level_min_xp'     => $bal ? (int) $bal['level_min_xp'] : 0,
			'next_level_xp'    => $bal ? (int) $bal['next_level_xp'] : 0,
			'next_level_label' => $bal ? (string) $bal['next_level_label'] : '',
			'identity'    => $identity,
			'declare_url' => $declare_url,
			'is_self'  => $is_self,
			'group'    => $group,
			'rows'     => $rows,
			'feeds'    => $feeds,
		);
	}

	/**
	 * 階級區塊：稱號 ＋ 到下一階的進度。等級與門檻都是 Sanji 算好存進投影的，
	 * 這裡只做除法跟 escape。還沒有任何帳的人看到的是邀請、不是 Lv.1——
	 * 沒賺到的階級不該先發（也避免把曲線知識洩進 plugin）。
	 */
	private static function rank_block_html( array $d ): string {
		if ( ! $d['has_balance'] || $d['level'] < 1 ) {
			$msg = $d['is_self']
				? '航海日誌還是空白的。到船塢分享你的第一則紀錄，被夥伴按讚就會開始累積。'
				: '這位夥伴還沒有航海紀錄。';
			return '<div class="nkv-rank nkv-rank--empty">'
				. '<div class="nkv-rank-head">' . self::identity_chip_html( $d ) . '</div>'
				. self::declare_hint_html( $d )
				. '<p class="nkv-note">' . esc_html( $msg ) . '</p></div>';
		}

		$xp    = (int) $d['xp'];
		$min   = (int) $d['level_min_xp'];
		$next  = (int) $d['next_level_xp'];
		$maxed = $next <= 0;

		$pct = 100;
		if ( ! $maxed ) {
			$span = max( 1, $next - $min );
			$pct  = (int) round( ( $xp - $min ) * 100 / $span );
			$pct  = max( 0, min( 100, $pct ) );
		}

		// 1 XP = 1 海里：距離用航程講，下一座島的名字自己會拉人。
		$tail = $maxed
			? '已抵達' . $d['level_label']
			: sprintf(
				'距離%s還有 %s 海里',
				$d['next_level_label'] ? $d['next_level_label'] : '下一座島',
				number_format_i18n( max( 0, $next - $xp ) )
			);

		ob_start();
		?>
<div class="nkv-rank">
	<div class="nkv-rank-head">
		<?php echo self::identity_chip_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
		<span class="nkv-lv">Lv.<?php echo esc_html( (string) $d['level'] ); ?></span>
		<span class="nkv-title"><?php echo esc_html( $d['level_label'] ); ?></span>
	</div>
	<div class="nkv-bar" role="progressbar" aria-valuenow="<?php echo esc_attr( (string) $pct ); ?>"
		aria-valuemin="0" aria-valuemax="100" aria-label="到下一階的進度">
		<i style="width:<?php echo esc_attr( (string) $pct ); ?>%"></i>
	</div>
	<div class="nkv-rank-foot">
		<span class="nkv-xp"><?php echo esc_html( number_format_i18n( $xp ) ); ?><small>XP</small></span>
		<span class="nkv-next"><?php echo esc_html( $tail ); ?></span>
	</div>
	<?php echo self::declare_hint_html( $d ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped ?>
</div>
		<?php
		return (string) ob_get_clean();
	}

	/** 身份 chip：艦長（修修）／船長／見習船長。公開顯示。 */
	private static function identity_chip_html( array $d ): string {
		$map = array(
			'admiral' => array( '艦長', 'nkv-idt--full' ),
			'captain' => array( '船長', 'nkv-idt--full' ),
			'trainee' => array( '見習船長', 'nkv-idt--trainee' ),
		);
		list( $label, $cls ) = $map[ $d['identity'] ] ?? $map['trainee'];
		return '<span class="nkv-idt ' . esc_attr( $cls ) . '">' . esc_html( $label ) . '</span>';
	}

	/** 船長儀式引導：只給「本人＋還是見習」看。發表啟航宣言＝正式成為船長。 */
	private static function declare_hint_html( array $d ): string {
		if ( 'trainee' !== $d['identity'] || ! $d['is_self'] ) {
			return '';
		}
		$text = '發表你的啟航宣言，正式成為船長';
		if ( '' !== $d['declare_url'] ) {
			return '<p class="nkv-declare"><a class="nkv-lk" href="' . esc_url( $d['declare_url'] ) . '">'
				. esc_html( $text ) . ' →</a></p>';
		}
		return '<p class="nkv-declare">' . esc_html( $text ) . '</p>';
	}

	/** 類型篩選下拉。value 就是 GROUP_LABELS 的 key；伺服器端過濾才是唯一真相。 */
	private static function filter_select_html( array $d ): string {
		$out = '<select class="nkv-filter" data-nkv-filter aria-label="篩選獎勵類型">';
		foreach ( self::GROUP_LABELS as $key => $label ) {
			$out .= '<option value="' . esc_attr( $key ) . '"' . selected( $d['group'], $key, false ) . '>'
				. esc_html( $label ) . '</option>';
		}
		return $out . '</select>';
	}

	/**
	 * 「活動」欄：這筆帳到底為哪件事而發。
	 *
	 * 主行 = 指得到具體對象就顯示它（貼文標題並連過去），否則退回來源名稱。
	 * 副行 = 補述：來源 · 空間（哪個挑戰）· 賽季 · 判定理由。
	 * 判定理由照原樣顯示（含 Sanji 的判定字串）——「帳目可申訴」的前提是看得到依據。
	 */
	private static function activity_cell( array $r, array $feeds ): string {
		$source = (string) $r['source'];
		$label  = self::SOURCE_LABELS[ $source ] ?? $source;
		$fid    = ( 'feed' === (string) ( $r['object_type'] ?? '' ) ) ? (int) $r['object_id'] : 0;
		$feed   = ( $fid && isset( $feeds[ $fid ] ) ) ? $feeds[ $fid ] : null;
		$title  = $feed ? (string) $feed['title'] : '';

		$bits = array();
		if ( '' !== $title ) {
			$main   = ( '' !== (string) $feed['url'] )
				? '<a class="nkv-lk" href="' . esc_url( (string) $feed['url'] ) . '">' . esc_html( $title ) . '</a>'
				: esc_html( $title );
			$bits[] = $label;
		} else {
			$main = esc_html( $label );
		}

		if ( $feed && '' !== (string) $feed['space'] ) {
			$bits[] = (string) $feed['space'];
		}
		if ( '' !== (string) ( $r['season'] ?? '' ) ) {
			$bits[] = (string) $r['season'];
		}
		// reason 是審計欄，值可能是機器參照（feed:291 / react:2936）——那對成員沒意義。
		// 判定的「為什麼」本來就在貼文底下 Sanji 的公開留言裡，這裡不重複也不洩內部 id。
		$reason = trim( (string) ( $r['reason'] ?? '' ) );
		if ( '' !== $reason && ! preg_match( '/^[a-z_]+:\d+$/', $reason ) ) {
			$bits[] = function_exists( 'mb_strimwidth' ) ? mb_strimwidth( $reason, 0, 52, '…' ) : $reason;
		}

		$sub = $bits ? '<span class="nkv-act-sub">' . esc_html( implode( ' · ', $bits ) ) . '</span>' : '';
		return '<span class="nkv-act-main">' . $main . '</span>' . $sub;
	}

	/** 明細區塊（本人）／隱藏說明（他人）。輸出已逐項 escape。 */
	private static function ledger_block_html( array $d ): string {
		if ( ! $d['is_self'] ) {
			return '<p class="nkv-note">入帳明細只有本人看得到。</p>';
		}

		// 一筆帳都還沒有：階級區塊的邀請已經說完了，別再補一句幾乎一樣的話。
		if ( ! $d['has_balance'] ) {
			return '';
		}

		$out = '<div class="nkv-lg-head"><h3>最近入帳</h3>' . self::filter_select_html( $d ) . '</div>';

		if ( ! $d['rows'] ) {
			$out .= 'all' === $d['group']
				? '<p class="nkv-note">還沒有入帳紀錄——發一篇有價值的文章，讓夥伴的讚替你開帳。</p>'
				: '<p class="nkv-note">這個類型還沒有紀錄。換個類型看看。</p>';
			return $out;
		}

		$out .= '<table>';
		foreach ( $d['rows'] as $r ) {
			$xp_v = (int) $r['xp'];
			$out .= '<tr><td class="nkv-act">' . self::activity_cell( $r, $d['feeds'] ) . '</td>'
				. '<td class="nkv-amt' . ( $xp_v < 0 ? ' neg' : '' ) . '">'
				. esc_html( ( $xp_v > 0 ? '+' : '' ) . number_format_i18n( $xp_v ) ) . ' XP</td>'
				. '<td class="nkv-dt">' . esc_html( mysql2date( 'n/j H:i', (string) $r['created_at'] ) ) . '</td></tr>';
		}
		$out .= '</table><p class="nkv-note">帳目可查、可申訴——有疑問直接私訊 Sanji 或艦長。</p>';
		return $out;
	}
}
