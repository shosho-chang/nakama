<?php
/**
 * UAT 一次性設置（冪等，可重跑）：
 *   1. sanji 服務帳號（WP user，角色 nakama_gam_service——非 admin）
 *   2. sanji 的社群 xprofile（active + verified，留言權限需要）
 *   3. 隱藏測試 space（privacy=secret），加入修修(user 2, admin) 與 sanji(member)
 *   4. space allowlist ← 測試 space id
 *
 * 用法：cd /var/www/fleet.shosho.tw && sudo -u u2_fleet_shosho wp eval-file \
 *   wp-content/plugins/fleet-gamification/tools/setup-uat.php
 *
 * 注意：app password 的建立不在本檔（避免密碼進 stdout）——用
 *   wp user application-password create sanji nakama-gam-service --porcelain
 *
 * ⚠️ UAT 期間 gam_enabled 開啟後，presence 捕捉是全站的（設計如此）。
 * 正式上線（Genesis T0）前必須清帳重來：備份 → TRUNCATE 三張 nakama_gam 表
 * → Sanji cursor 歸零。UAT 分數是測試資料，不得帶進正式帳。
 */

// 註：wp eval-file 的 eval 語境不允許 declare(strict_types)。

if ( ! defined( 'ABSPATH' ) ) {
	exit( "run via wp eval-file\n" );
}

$out = array();

/* ── 0. 前置檢查 ── */
foreach ( array(
	'\FluentCommunity\App\Models\Space',
	'\FluentCommunity\App\Models\BaseSpace',
	'\FluentCommunity\App\Models\User',
	'\FluentCommunity\App\Services\Helper',
) as $cls ) {
	if ( ! class_exists( $cls ) ) {
		echo "FATAL: {$cls} not found\n";
		exit( 1 );
	}
}
if ( null === get_role( 'nakama_gam_service' ) ) {
	echo "FATAL: role nakama_gam_service missing（plugin 未啟用？）\n";
	exit( 1 );
}

/* ── 1. sanji WP user ── */
$sanji_id = username_exists( 'sanji' );
if ( ! $sanji_id ) {
	$sanji_id = wp_insert_user(
		array(
			'user_login'   => 'sanji',
			'user_email'   => 'sanji@shosho.tw',
			'display_name' => 'Sanji',
			'user_pass'    => wp_generate_password( 32 ),
			'role'         => 'nakama_gam_service',
		)
	);
	if ( is_wp_error( $sanji_id ) ) {
		echo 'FATAL: user create failed: ' . $sanji_id->get_error_message() . "\n";
		exit( 1 );
	}
	$out['sanji_user'] = "created #{$sanji_id}";
} else {
	$u = new WP_User( $sanji_id );
	if ( ! in_array( 'nakama_gam_service', (array) $u->roles, true ) ) {
		$u->set_role( 'nakama_gam_service' );
	}
	$out['sanji_user'] = "exists #{$sanji_id}";
}

/* ── 2. sanji xprofile（社群成員身分） ── */
$fc_user = \FluentCommunity\App\Models\User::find( $sanji_id );
$profile = $fc_user ? $fc_user->syncXProfile() : null;
if ( $profile ) {
	$profile->status      = 'active';
	$profile->is_verified = 1;
	$profile->save();
	$out['xprofile'] = "active #{$profile->user_id}";
} else {
	$out['xprofile'] = 'FAIL: syncXProfile returned null';
}

/* ── 3. 測試 space（secret） ── */
$slug  = 'test-dock';
$space = \FluentCommunity\App\Models\BaseSpace::onlyMain()->where( 'slug', $slug )->first();
if ( ! $space ) {
	// settings 以既有 space 為模板（避免手拼 20 鍵的序列化陣列），
	// 但拔掉商業掛鉤（cart_product_ids）並改 emoji。
	$tpl      = \FluentCommunity\App\Models\BaseSpace::onlyMain()->find( 10 ); // 睡眠 space
	$settings = $tpl ? $tpl->settings : array();
	if ( is_array( $settings ) ) {
		$settings['cart_product_ids'] = array();
		$settings['emoji']            = '⚓';
		$settings['can_request_join'] = 'no';
	}
	$space = \FluentCommunity\App\Models\Space::create(
		array(
			'title'       => '測試船塢',
			'slug'        => $slug,
			'type'        => 'community',
			'privacy'     => 'secret',
			'status'      => 'published',
			'created_by'  => 2,
			'serial'      => 99,
			'description' => 'gamification UAT 專用（secret，僅測試成員可見）',
			'settings'    => $settings,
		)
	);
	$out['space'] = "created #{$space->id}";
} else {
	$out['space'] = "exists #{$space->id}";
}

/* ── 4. 成員：修修(admin) + sanji(member)；補快取（addToSpace 不刷快取的地雷） ── */
foreach ( array(
	array( 2, 'admin' ),
	array( (int) $sanji_id, 'member' ),
) as $pair ) {
	list( $uid, $role ) = $pair;
	\FluentCommunity\App\Services\Helper::addToSpace( $space, $uid, $role, 'by_admin' );
	$m = \FluentCommunity\App\Models\User::find( $uid );
	if ( $m ) {
		$m->cacheAccessSpaces();
	}
}
$out['members'] = "user 2 (admin), user {$sanji_id} (member)";

/* ── 5. allowlist ── */
update_option( 'nakama_gam_space_allowlist', array( (int) $space->id ), true );
$out['allowlist'] = array( (int) $space->id );

echo wp_json_encode( $out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT ) . "\n";
