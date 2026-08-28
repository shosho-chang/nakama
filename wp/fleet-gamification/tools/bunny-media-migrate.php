<?php
/**
 * 一次性遷移：把「直播錄影」課程裡的 Bunny iframe 嵌入，換成 FluentPlayer media。
 *
 * 為什麼要做：FluentPlayer 的 progression 引擎（伺服器權威的觀看判定）只認
 * `fluent_player_media` post，而且它的防偽地基是**伺服器自有的片長**
 * （ProgressionService::pickDuration() 伺服器優先）。lesson 只要還掛 iframe_html，
 * 就永遠拿不到 durationSource=server 的判定，遊戲化的「看完影片」就無從計分。
 *
 * 用法（VPS）：
 *   cd /var/www/fleet.shosho.tw && sudo -u u2_fleet_shosho wp eval-file \
 *     wp-content/plugins/fleet-gamification/tools/bunny-media-migrate.php [dry-run|apply|rollback]
 *
 *   dry-run （預設）＝ 只印計畫，零寫入
 *   apply         ＝ 建 media、改 lesson，改前自動備份
 *   rollback      ＝ 從最新的備份檔還原 lesson（media post 不刪，留著無害）
 *
 * 形狀來源：全部取自 2026-08-28 修修用後台 UI 實做的 lesson 141 / media 9896，
 * 不是推測。三層寫入缺一不可：
 *   ① fluent_player_media post（settings.provider=bunny、settings.duration=片長秒數）
 *   ② lesson.message 的 `wp:fluent-player/media` block（mediaId 連結就在這裡）
 *   ③ lesson.meta.media = {type: fluent_player, content_type: video, image: 縮圖}
 *
 * 刻意不做：不動 require_video_completion（維持 'no'）。開不開 80% 閘門是
 * 計分決策，不是搬家的一部分——一次只改一件事。
 *
 * ⚠️ 註：本檔跑在 wp eval-file 的 eval 語境——不得使用 declare(strict_types)。
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit( "run via wp eval-file\n" );
}

$mode = 'dry-run';
if ( isset( $args ) && is_array( $args ) && ! empty( $args[0] ) ) {
	$mode = (string) $args[0];
}
if ( ! in_array( $mode, array( 'dry-run', 'apply', 'rollback' ), true ) ) {
	exit( "unknown mode: {$mode}（用 dry-run / apply / rollback）\n" );
}

// 用 define 而非 const：eval 語境下重跑不會撞 redeclare。
if ( ! defined( 'NAKAMA_BUNNY_LIBRARY_ID' ) ) {
	define( 'NAKAMA_BUNNY_LIBRARY_ID', 579407 );
}
if ( ! defined( 'NAKAMA_BUNNY_BACKUP_DIR' ) ) {
	define( 'NAKAMA_BUNNY_BACKUP_DIR', 'nakama-gam' );
}

/**
 * 備份檔路徑（uploads 下，隨時間戳；rollback 取最新一份）。
 */
function nakama_bunny_backup_dir() {
	$uploads = wp_upload_dir();
	$dir     = trailingslashit( $uploads['basedir'] ) . NAKAMA_BUNNY_BACKUP_DIR;
	if ( ! is_dir( $dir ) ) {
		wp_mkdir_p( $dir );
	}
	return $dir;
}

/**
 * 從 lesson meta 的 iframe html 抽出 Bunny video GUID。抽不到回空字串——
 * 抽不到就跳過，絕不猜。
 */
function nakama_bunny_guid_from_meta( $meta ) {
	$blob = '';
	if ( is_array( $meta ) && isset( $meta['media'] ) && is_array( $meta['media'] ) ) {
		foreach ( $meta['media'] as $value ) {
			if ( is_string( $value ) ) {
				$blob .= ' ' . $value;
			}
		}
	}
	if ( ! preg_match( '/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i', $blob, $m ) ) {
		return '';
	}
	// 同一段 html 裡必須也出現 library id，否則不能確定是這個 library 的影片。
	if ( strpos( $blob, (string) NAKAMA_BUNNY_LIBRARY_ID ) === false ) {
		return '';
	}
	return strtolower( $m[0] );
}

/**
 * 既有的 fluent_player_media 是否已經指向這支 Bunny 影片（冪等：重跑不重建）。
 */
function nakama_bunny_find_media_by_guid( $guid ) {
	$ids = get_posts(
		array(
			'post_type'   => 'fluent_player_media',
			'post_status' => 'any',
			'numberposts' => -1,
			'fields'      => 'ids',
		)
	);
	foreach ( $ids as $id ) {
		$settings = get_post_meta( $id, 'settings', true );
		if ( is_string( $settings ) ) {
			$settings = maybe_unserialize( $settings );
		}
		if ( ! is_array( $settings ) ) {
			continue;
		}
		$vid = isset( $settings['bunny']['video_id'] ) ? strtolower( (string) $settings['bunny']['video_id'] ) : '';
		if ( $vid && $vid === $guid ) {
			return (int) $id;
		}
	}
	return 0;
}

/**
 * 依 lesson 141 / media 9896 的實測形狀組 settings。
 *
 * `duration`（頂層）是關鍵：ProgressionHandler 先讀它，讀到才會有
 * durationSource=server 的伺服器權威判定。
 */
function nakama_bunny_media_settings( $video ) {
	$guid  = (string) $video['guid'];
	$title = (string) $video['title'];
	$len   = (float) $video['length'];
	$host  = (string) $video['_hostname'];

	$src   = "https://{$host}/{$guid}/playlist.m3u8";
	$thumb = "https://{$host}/{$guid}/thumbnail.jpg";

	$mp4 = array();
	if ( ! empty( $video['MP4URLs'] ) && is_array( $video['MP4URLs'] ) ) {
		foreach ( $video['MP4URLs'] as $url ) {
			if ( is_string( $url ) && $url ) {
				$mp4[] = $url;
			}
		}
	}

	return array(
		'chapters'          => array(),
		'overlays'          => array(),
		'language_mappings' => array(),
		'viewType'          => 'video',
		'mediaType'         => 'video',
		'streamType'        => 'on-demand',
		'loadStrategy'      => 'visible',
		'preload'           => 'metadata',
		'title'             => $title,
		'post_status'       => 'private',
		'src'               => $src,
		'provider'          => 'bunny',
		'posterSrc'         => $thumb,
		'preset_slug'       => 'course',
		'playsInline'       => true,
		'mutedAutoplay'     => false,
		'autoplay'          => false,
		'aspectRatio'       => 'default',
		'brandingColor'     => '#DD1F13',
		'video_end_option'  => 'default',
		'bunny'             => array(
			'library_id'         => (string) NAKAMA_BUNNY_LIBRARY_ID,
			'video_id'           => $guid,
			'collection_id'      => null,
			'title'              => $title,
			'thumbnail'          => $thumb,
			'duration'           => $len,
			// library 沒開 Token Authentication；已實測未簽章與過期 token 皆回 200。
			// 開了會壞——安裝版本裡沒有任何簽章程式碼（見 tools/README 或 PR 說明）。
			'token_auth_enabled' => false,
			'is_audio'           => false,
			'mp4_urls'           => $mp4,
		),
		'duration'          => $len,
	);
}

// ── 讀 Bunny library ────────────────────────────────────────────────────────

if ( ! class_exists( '\FluentPlayerPro\App\Services\BunnyCDNService' ) ) {
	exit( "FluentPlayer Pro 未啟用——先確認外掛狀態\n" );
}
if ( ! class_exists( '\FluentPlayer\App\Models\Media' ) ) {
	exit( "FluentPlayer 未啟用\n" );
}
if ( ! class_exists( '\FluentCommunity\Modules\Course\Model\CourseLesson' ) ) {
	exit( "FluentCommunity Course 模組未啟用\n" );
}

$lesson_class = '\FluentCommunity\Modules\Course\Model\CourseLesson';
$media_class  = '\FluentPlayer\App\Models\Media';

if ( 'rollback' === $mode ) {
	$files = glob( nakama_bunny_backup_dir() . '/bunny-migrate-*.json' );
	if ( ! $files ) {
		exit( "找不到備份檔，無法 rollback\n" );
	}
	sort( $files );
	$file  = end( $files );
	$saved = json_decode( (string) file_get_contents( $file ), true );
	if ( ! is_array( $saved ) ) {
		exit( "備份檔壞掉：{$file}\n" );
	}
	echo "從 {$file} 還原 " . count( $saved ) . " 課\n";
	foreach ( $saved as $row ) {
		$lesson = $lesson_class::find( (int) $row['id'] );
		if ( ! $lesson ) {
			echo "  lesson {$row['id']} 找不到，跳過\n";
			continue;
		}
		$lesson->message = (string) $row['message'];
		$lesson->meta    = $row['meta'];
		$lesson->save();
		echo "  lesson {$row['id']} 已還原\n";
	}
	exit( "rollback 完成（media post 未刪除，留著無害）\n" );
}

$svc = new \FluentPlayerPro\App\Services\BunnyCDNService();
$res = $svc->getVideos( NAKAMA_BUNNY_LIBRARY_ID, array( 'per_page' => 200, 'page' => 1 ) );
if ( is_wp_error( $res ) ) {
	exit( 'Bunny API 失敗：' . $res->get_error_code() . ' / ' . $res->get_error_message() . "\n" );
}

$raw_settings = get_option( 'fluent_player_integrations_settings', '' );
$all_settings = is_string( $raw_settings ) ? json_decode( $raw_settings, true ) : $raw_settings;
$libraries    = array();
if ( is_array( $all_settings ) && isset( $all_settings['bunnycdn_stream']['libraries'] )
	&& is_array( $all_settings['bunnycdn_stream']['libraries'] ) ) {
	$libraries = $all_settings['bunnycdn_stream']['libraries'];
}

$hostname = '';
foreach ( $libraries as $lib ) {
	if ( is_array( $lib ) && (int) ( isset( $lib['Id'] ) ? $lib['Id'] : 0 ) === NAKAMA_BUNNY_LIBRARY_ID ) {
		$hostname = isset( $lib['Hostname'] ) ? (string) $lib['Hostname'] : '';
	}
}
if ( ! $hostname ) {
	exit( '拿不到 library ' . NAKAMA_BUNNY_LIBRARY_ID . " 的 pull zone hostname——先在後台開一次 Bunny 整合頁\n" );
}

$by_guid = array();
foreach ( (array) ( isset( $res['items'] ) ? $res['items'] : array() ) as $v ) {
	if ( ! is_array( $v ) || empty( $v['guid'] ) ) {
		continue;
	}
	$v['_hostname']                        = $hostname;
	$by_guid[ strtolower( $v['guid'] ) ]   = $v;
}

// ── 掃 lesson ───────────────────────────────────────────────────────────────

global $wpdb;
$rows = $wpdb->get_results(
	"SELECT id FROM {$wpdb->prefix}fcom_posts WHERE type = 'course_lesson' ORDER BY id",
	ARRAY_A
);

$plan   = array();
$skips  = array();

foreach ( $rows as $row ) {
	$lesson = $lesson_class::find( (int) $row['id'] );
	if ( ! $lesson ) {
		continue;
	}
	$meta = $lesson->meta;
	$meta = is_array( $meta ) ? $meta : array();

	$type = isset( $meta['media']['type'] ) ? (string) $meta['media']['type'] : '';
	if ( 'fluent_player' === $type ) {
		$skips[] = array( (int) $row['id'], '已經是 fluent_player' );
		continue;
	}

	$guid = nakama_bunny_guid_from_meta( $meta );
	if ( ! $guid ) {
		$skips[] = array( (int) $row['id'], $type ? "非 Bunny 來源（{$type}）" : '無媒體' );
		continue;
	}
	if ( ! isset( $by_guid[ $guid ] ) ) {
		$skips[] = array( (int) $row['id'], "GUID 不在 library：{$guid}" );
		continue;
	}

	$plan[] = array(
		'id'     => (int) $row['id'],
		'title'  => (string) $lesson->title,
		'guid'   => $guid,
		'video'  => $by_guid[ $guid ],
		'lesson' => $lesson,
		'meta'   => $meta,
	);
}

echo "=== 計畫（mode={$mode}）===\n";
foreach ( $plan as $p ) {
	$existing = nakama_bunny_find_media_by_guid( $p['guid'] );
	printf(
		"  lesson %-5s len=%-6s media=%-14s %s\n",
		$p['id'],
		(int) $p['video']['length'],
		$existing ? "沿用 {$existing}" : '新建',
		mb_substr( $p['title'], 0, 40 )
	);
}
echo '  --- 共 ' . count( $plan ) . " 課要處理\n";
echo "=== 跳過 ===\n";
foreach ( $skips as $s ) {
	printf( "  lesson %-5s %s\n", $s[0], $s[1] );
}

if ( 'dry-run' === $mode ) {
	exit( "\n[dry-run] 沒有寫入任何東西。確認無誤後跑 apply。\n" );
}

// ── apply ───────────────────────────────────────────────────────────────────

if ( ! $plan ) {
	exit( "\n沒有要處理的 lesson。\n" );
}

$backup = array();
foreach ( $plan as $p ) {
	$backup[] = array(
		'id'      => $p['id'],
		'message' => (string) $p['lesson']->message,
		'meta'    => $p['meta'],
	);
}
$backup_file = nakama_bunny_backup_dir() . '/bunny-migrate-' . gmdate( 'Ymd-His' ) . '.json';
file_put_contents( $backup_file, wp_json_encode( $backup, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES ) );
echo "\n備份寫到 {$backup_file}\n\n";

foreach ( $plan as $p ) {
	$video    = $p['video'];
	$media_id = nakama_bunny_find_media_by_guid( $p['guid'] );

	if ( ! $media_id ) {
		$media              = new $media_class();
		$media->title       = (string) $video['title'];
		$media->post_status = 'private';
		$media->settings    = nakama_bunny_media_settings( $video );
		$saved              = $media->save();
		$media_id           = (int) ( isset( $saved->ID ) ? $saved->ID : $media->id );
		echo "  lesson {$p['id']}：建立 media {$media_id}\n";
	} else {
		echo "  lesson {$p['id']}：沿用 media {$media_id}\n";
	}

	if ( ! $media_id ) {
		echo "  lesson {$p['id']}：media 建立失敗，跳過（不動 lesson）\n";
		continue;
	}

	$host  = (string) $video['_hostname'];
	$thumb = 'https://' . $host . '/' . strtolower( (string) $video['guid'] ) . '/thumbnail.jpg';

	// ② message 的 block——既有內文保留，block 放最前面（後台 UI 也是這個位置）。
	$block   = '<!-- wp:fluent-player/media {"mediaId":' . $media_id . ',"isFcomFeatureMedia":true} /-->';
	$message = (string) $p['lesson']->message;
	if ( strpos( $message, 'wp:fluent-player/media' ) === false ) {
		$message = trim( $message ) === ''
			? $block . "\n"
			: $block . "\n\n" . $message;
	}

	// ③ meta.media 換成 fluent_player；影片長度填進 video_length。
	$meta                    = $p['meta'];
	$meta['media']           = array(
		'type'         => 'fluent_player',
		'content_type' => 'video',
		// 用未簽章的縮圖網址：library 未開 token auth（已實測未簽章回 200），
		// 而 UI 產的簽章網址帶 expires、兩小時就過期。
		'image'        => $thumb,
	);
	$meta['enable_media']    = 'yes';
	$meta['video_length']    = (int) $video['length'];
	// 閘門欄位補上預設值（不啟用——開不開是計分決策，不是搬家的一部分）。
	foreach ( array(
		'require_video_completion'  => 'no',
		'auto_complete_on_video_end' => 'no',
		'video_completion_threshold' => 0,
	) as $k => $v ) {
		if ( ! array_key_exists( $k, $meta ) ) {
			$meta[ $k ] = $v;
		}
	}

	$lesson          = $p['lesson'];
	$lesson->message = $message;
	$lesson->meta    = $meta;
	$lesson->save();

	echo "  lesson {$p['id']}：已改掛 media {$media_id}（{$video['length']}s）\n";
}

echo "\n完成。驗證：隨便開一課看播放器有沒有出來；要退回跑 rollback。\n";
