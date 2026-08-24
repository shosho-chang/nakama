<?php
/**
 * Contract probe——驗證本 plugin 對 FluentCommunity 的每一個依賴仍然成立。
 *
 * 用法（VPS，一分鐘內跑完）：
 *   cd /var/www/fleet.shosho.tw && sudo -u u2_fleet_shosho wp eval-file \
 *     wp-content/plugins/fleet-gamification/tools/contract-probe.php
 *
 * 時機：①vendor 新版釋出、更新**之前**對新檔案跑 ②更新之後再跑 ③每次部署後的煙霧測試。
 * 輸出：逐項 PASS/FAIL＋總結。任何 FAIL = 不要更新 vendor / 檢查對應的捕捉或橋接程式。
 *
 * ⚠️ 唯讀：本腳本不寫任何資料。
 */

// 註：本檔跑在 wp eval-file 的 eval 語境——不得使用 declare(strict_types)（必須是檔案第一語句）。

if ( ! defined( 'ABSPATH' ) ) {
	exit( "run via wp eval-file\n" );
}

$results = array();
$check   = static function ( string $name, bool $ok, string $note = '' ) use ( &$results ): void {
	$results[] = array( $name, $ok, $note );
	echo ( $ok ? 'PASS' : 'FAIL' ) . "  {$name}" . ( $note && ! $ok ? "  ← {$note}" : '' ) . "\n";
};

$fc_dir  = WP_PLUGIN_DIR . '/fluent-community';
$pro_dir = WP_PLUGIN_DIR . '/fluent-community-pro';

$src = static function ( string $rel ) use ( $fc_dir ): string {
	$f = $fc_dir . $rel;
	return is_readable( $f ) ? (string) file_get_contents( $f ) : '';
};

echo "=== fleet-gamification contract probe ===\n";

/* ── 1. Models 與資料表 ─────────────────────────────── */
$check( 'class Feed exists', class_exists( '\FluentCommunity\App\Models\Feed' ) );
$check( 'class Media exists', class_exists( '\FluentCommunity\App\Models\Media' ) );
$check( 'class Comment exists', class_exists( '\FluentCommunity\App\Models\Comment' ) );
$check( 'class Reaction exists', class_exists( '\FluentCommunity\App\Models\Reaction' ) );

global $wpdb;
$cols = $wpdb->get_col( "DESCRIBE {$wpdb->prefix}fcom_post_reactions", 0 );
$need = array( 'id', 'user_id', 'object_id', 'object_type', 'type', 'created_at' );
$check(
	'fcom_post_reactions columns',
	is_array( $cols ) && ! array_diff( $need, $cols ),
	'missing: ' . implode( ',', array_diff( $need, (array) $cols ) )
);

/* ── 2. 捕捉層 hooks（原始碼字面驗證） ───────────────── */
$feeds_ctrl = $src( '/app/Http/Controllers/FeedsController.php' );
$react_ctrl = $src( '/app/Http/Controllers/ReactionController.php' );
$cmt_ctrl   = $src( '/app/Http/Controllers/CommentsController.php' );
$course_hlp = $src( '/Modules/Course/Services/CourseHelper.php' );
$feeds_hlp  = $src( '/app/Services/FeedsHelper.php' );

$check( "hook space_feed/created (FeedsController)", str_contains( $feeds_ctrl, "do_action('fluent_community/space_feed/created'" ) );
$check( "hook space_feed/created (FeedsHelper)", str_contains( $feeds_hlp, "do_action('fluent_community/space_feed/created'" ) );
$check( "hook feed/react_added(\$react, \$feed)", str_contains( $react_ctrl, "do_action('fluent_community/feed/react_added', \$react, \$feed)" ) );
$check( "hook feed/react_removed", str_contains( $react_ctrl, "do_action('fluent_community/feed/react_removed'" ) );
$check( "hook comment_added(\$comment, \$feed, ...)", str_contains( $cmt_ctrl, "do_action('fluent_community/comment_added', \$comment, \$feed" ) );
$check( "hook course/lesson_completed(\$lesson, \$userId)", str_contains( $course_hlp, "do_action('fluent_community/course/lesson_completed', \$lesson, \$userId)" ) );
$check( "hook course/completed(\$course, \$userId)", str_contains( $course_hlp, "do_action('fluent_community/course/completed', \$course, \$userId)" ) );
$check( 'hook track_activity in getTicker', str_contains( $feeds_ctrl, "do_action('fluent_community/track_activity')" ) );

$portal_view = $src( '/app/Views/portal/portal.php' );
$check( 'hook portal_sidebar in portal view', str_contains( $portal_view, "do_action('fluent_community/portal_sidebar'" ) );

$quiz_ctrl = is_readable( $pro_dir . '/app/Modules/Quiz/Http/Controllers/QuizController.php' )
	? (string) file_get_contents( $pro_dir . '/app/Modules/Quiz/Http/Controllers/QuizController.php' )
	: '';
$check( 'hook quiz/submitted (Pro)', str_contains( $quiz_ctrl, "do_action('fluent_community/quiz/submitted'" ) );

/* ── 3. 已知縫隙假設（變了代表 vendor 修了，捕捉層要跟著改） ── */
$check(
	"react hooks like-gated（收藏仍無 hook 的前提）",
	str_contains( $react_ctrl, "if (\$type == 'like')" ),
	'vendor 可能開始對 bookmark 發 hook——檢查 Capture::on_react_added 是否會重複入帳'
);

/* ── 4. FcBridge 留言通道 ───────────────────────────── */
$routes  = rest_get_server()->get_routes( 'fluent-community/v2' );
$has_cmt = false;
foreach ( array_keys( $routes ) as $r ) {
	if ( str_contains( $r, 'feeds' ) && str_ends_with( $r, '/comments' ) ) {
		$has_cmt = true;
		break;
	}
}
$check( 'REST route feeds/{id}/comments', $has_cmt );
$check( "comment payload key = 'comment'", str_contains( $cmt_ctrl, "Arr::get(\$data, 'comment')" ) );

/* ── 5. 本 plugin 自身 ──────────────────────────────── */
$check( 'nakama-gam/v1 routes registered', (bool) rest_get_server()->get_routes( 'nakama-gam/v1' ) );
$check( 'gam tables exist', (bool) $wpdb->get_var( "SHOW TABLES LIKE '{$wpdb->prefix}nakama_gam_grants'" ) );

/* ── 總結 ───────────────────────────────────────────── */
$fails = array_filter( $results, static fn( $r ) => ! $r[1] );
echo "=========================================\n";
echo sprintf( "%d checks, %d FAIL\n", count( $results ), count( $fails ) );
if ( $fails ) {
	echo "RESULT: RED — do NOT update vendor / investigate before deploy\n";
	exit( 1 );
}
echo "RESULT: GREEN\n";
