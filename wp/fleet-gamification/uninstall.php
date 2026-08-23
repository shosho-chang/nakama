<?php
/**
 * 反安裝時「刻意」不刪任何資料表。
 *
 * grants 是會員資產的帳本——十年營運方案（docs/plans/fleet-gamification-master-plan.md
 * §11 對會員的承諾）承諾貝里永不被沒收。誤刪 plugin 不得連帶蒸發帳本。
 * 真要清庫是人工決策：wp db export 備份後手動 DROP。
 */

if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}

// 只清 runtime 開關，資料表全數保留。
delete_option( 'nakama_gam_enabled' );
