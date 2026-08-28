/**
 * 影片觀看 tracker——把實際播放區段送給 FluentPlayer 的伺服器權威判定端點。
 *
 * 這支只做一件事：忠實記錄「哪幾段時間真的在播」，然後送出去。所有信任相關的
 * 計算（片長歸屬、區段夾 clamp、覆蓋率、達標與否）都在伺服器
 * （fluent-player/app/Services/Progression/），這裡送什麼都不影響判定的可信度。
 *
 * 兩個刻意的設計：
 *
 * 1. **永遠送 duration = 0**。伺服器的 pickDuration() 是「自有片長優先，沒有才
 *    退回客戶端送的數字」。我們送 0，等於強制判定只能是 durationSource='server'
 *    或 'none'，客戶端在結構上就沒有能力把信任層級降級。Bunny 匯入的 media 帶
 *    provider 片長 → 'server'；YouTube 課沒有 → 'none' → 覆蓋率恆 0、永不達標。
 *    這正是我們要的行為，而且不需要在伺服器端寫任何 YouTube 特例。
 *
 * 2. **從 DOM 讀 media id**，不靠 localize。portal 是 Vue SPA，播放器是換頁後才
 *    注入的，載入時 localize 的那份資料早就過期了。渲染出來的 DOM 帶著
 *    `data-media-id`（.fp-media-block 與 .fluent-player-container 上都有），
 *    跟著元素走，永遠是對的。
 */
(function () {
	'use strict';

	var cfg = window.nakamaGamVideoProgress;
	if (!cfg || !cfg.ajaxUrl || !cfg.nonce) {
		return;
	}

	var FLUSH_MS = (parseInt(cfg.flushSeconds, 10) || 30) * 1000;
	var SELECTOR = '.fluent-player media-player';
	var wired = [];

	function mediaIdOf(player) {
		var host = player.closest ? player.closest('[data-media-id]') : null;
		if (!host) {
			return 0;
		}
		return parseInt(host.getAttribute('data-media-id'), 10) || 0;
	}

	function isWired(player) {
		for (var i = 0; i < wired.length; i++) {
			if (wired[i] === player) {
				return true;
			}
		}
		return false;
	}

	function Tracker(player, mediaId) {
		this.player = player;
		this.mediaId = mediaId;
		this.segments = [];
		this.openAt = null;
		this.ended = false;
		this.inFlight = false;
		this.timer = null;
		this.bind();
	}

	Tracker.prototype.now = function () {
		var t = this.player.currentTime;
		return (typeof t === 'number' && isFinite(t) && t >= 0) ? t : null;
	};

	/** 收掉目前這段（播放中被暫停／跳轉／到底）。 */
	Tracker.prototype.close = function () {
		var end = this.now();
		if (this.openAt === null || end === null) {
			this.openAt = null;
			return;
		}
		// 只收正向且有長度的區段；倒退跳轉不產生負區段。
		if (end > this.openAt + 0.25) {
			this.segments.push({ start: this.openAt, end: end });
		}
		this.openAt = null;
	};

	Tracker.prototype.open = function () {
		this.openAt = this.now();
	};

	Tracker.prototype.mutedAutoplay = function () {
		var p = this.player;
		var isAuto = !!p.autoplay || (p.hasAttribute && p.hasAttribute('autoplay'));
		return !!(p.muted && isAuto);
	};

	/**
	 * 送出累積的區段。beacon=true 用於離頁（fetch 會被中斷，sendBeacon 不會）。
	 */
	Tracker.prototype.flush = function (beacon) {
		if (this.inFlight && !beacon) {
			return;
		}
		// 把播放中的那一段先落袋，再從現在重新開一段，避免長時間播放沒有回報。
		var wasOpen = this.openAt !== null;
		this.close();
		if (!this.segments.length) {
			if (wasOpen) {
				this.open();
			}
			return;
		}

		var body = new FormData();
		body.append('action', 'fluent_player_progression');
		body.append('nonce', cfg.nonce);
		body.append('media_id', String(this.mediaId));
		body.append('segments', JSON.stringify(this.segments));
		body.append('duration', '0'); // 見檔頭：永遠不讓客戶端提供片長
		body.append('ended', this.ended ? '1' : '0');
		body.append('muted_autoplay', this.mutedAutoplay() ? '1' : '0');

		// 區段已交出去；伺服器用 accumulate 跨場次做聯集，本地不需要留著重送。
		this.segments = [];
		if (wasOpen) {
			this.open();
		}

		if (beacon && navigator.sendBeacon) {
			try {
				navigator.sendBeacon(cfg.ajaxUrl, body);
			} catch (e) { /* 離頁時失敗就算了，下次播放會再累積 */ }
			return;
		}

		var self = this;
		this.inFlight = true;
		fetch(cfg.ajaxUrl, { method: 'POST', body: body, credentials: 'same-origin' })
			.catch(function () { /* 網路失敗不影響播放，靜默 */ })
			.then(function () { self.inFlight = false; });
	};

	Tracker.prototype.startTimer = function () {
		var self = this;
		this.stopTimer();
		this.timer = setInterval(function () {
			// SPA 換頁會把播放器從 DOM 移除——收尾後停掉，不留殭屍 timer。
			if (!document.contains(self.player)) {
				self.close();
				self.flush(true);
				self.stopTimer();
				return;
			}
			self.flush(false);
		}, FLUSH_MS);
	};

	Tracker.prototype.stopTimer = function () {
		if (this.timer) {
			clearInterval(this.timer);
			this.timer = null;
		}
	};

	Tracker.prototype.bind = function () {
		var self = this;
		var p = this.player;

		p.addEventListener('play', function () { self.open(); self.startTimer(); });
		p.addEventListener('seeking', function () { self.close(); });
		p.addEventListener('seeked', function () { if (!p.paused) { self.open(); } });
		p.addEventListener('pause', function () { self.close(); self.stopTimer(); self.flush(false); });
		p.addEventListener('ended', function () {
			self.ended = true;
			self.close();
			self.stopTimer();
			self.flush(false);
		});
	};

	function scan() {
		var players = document.querySelectorAll(SELECTOR);
		for (var i = 0; i < players.length; i++) {
			var player = players[i];
			if (isWired(player)) {
				continue;
			}
			var id = mediaIdOf(player);
			if (!id) {
				continue; // 沒有 media id 就沒得追，跳過而不是猜
			}
			wired.push(player);
			player.__nakamaTracker = new Tracker(player, id);
		}
	}

	function leaving() {
		for (var i = 0; i < wired.length; i++) {
			var player = wired[i];
			if (player.__nakamaTracker) {
				player.__nakamaTracker.close();
				player.__nakamaTracker.flush(true);
			}
		}
	}

	scan();
	// portal 是 SPA：播放器在換頁後才被注入，必須持續觀察。
	if (window.MutationObserver && document.body) {
		new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
	}
	document.addEventListener('visibilitychange', function () {
		if (document.visibilityState === 'hidden') {
			leaving();
		}
	});
	window.addEventListener('pagehide', leaving);
})();
