(() => {
  'use strict';

  const channel = 'nakama-carousel-editor-v1';
  // 哪些卡片有「來賓去背照幾何」可以調——封面與金句。金句原本沒有，位置寫死在
  // 算圖 CSS 裡，所以怎麼拖都沒反應。兩者欄位相同、界線不同（金句的去背照小得多）。
  const guestLayoutSpecs = {
    cover: {
      selector: '#canvas.cover .guest',
      bounds: {
        guest_right_px: [-540, 240],
        guest_bottom_px: [-400, 240],
        guest_height_px: [480, 1400],
        title_font_size_px: [72, 160],
      },
      // 封面比金句多一個標題字級。它不在去背照身上，量不到的話整組 layout
      // 會湊不齊、停在 null，拖曳就算出 NaN——而 `style.right = 'NaNpx'` 是
      // 被瀏覽器靜默忽略的，症狀正好是「綁得到、游標會變、就是不動」。
      extraMeasure: () => {
        const raw = getComputedStyle(document.documentElement)
          .getPropertyValue('--type-cover-title');
        const value = Math.round(parseFloat(raw));
        return Number.isFinite(value) ? {title_font_size_px: value} : {};
      },
    },
    quote: {
      selector: '#canvas.quote-a .guest, #canvas.quote-b .guest-panel img',
      bounds: {
        guest_right_px: [-540, 240],
        guest_bottom_px: [-400, 240],
        guest_height_px: [200, 1000],
      },
    },
  };
  let layout = null;
  let textLayouts = [];
  let textSafeRects = {};
  let diagnosticsRequestId = null;
  let diagnosticsFingerprint = null;
  let selectedTextRegion = null;
  let visualScale = 1;

  // 展示模式的樣式一律注入。它跟這張卡有沒有去背照無關——原本寫在
  // `configureGuestInteraction()` 裡，而那個函式只有封面／金句會走到，
  // 於是 hook 那類卡片在主畫面縮圖上仍然帶著文字控制點與選取外框。
  (() => {
    const style = document.createElement('style');
    style.dataset.presentationMode = 'true';
    style.textContent = '[data-presentation="true"] .carousel-preview-resize,'
      + '[data-presentation="true"] .carousel-preview-text-resize{display:none!important}'
      + '[data-presentation="true"] [data-fit-region]{outline:0!important;cursor:default!important}'
      + '[data-presentation="true"] .guest{cursor:default!important;outline:0!important}';
    document.head.append(style);
  })();

  function emit(type, payload = {}) {
    window.parent.postMessage({channel, type, ...payload}, '*');
  }

  // 角色以**畫布本身**判斷。原本是走 `currentPage()`——那要 `__CAROUSEL_SPEC__`
  // 加上 `?page=` 查詢字串都對，是我在改寫時引進的相依；只要其中一個不如預期，
  // `applyLayout` 與 `configureGuestInteraction` 都會安靜地 return，
  // 症狀就是「什麼都沒發生、也沒有錯誤」。畫布的 class 是算圖時就寫死的，
  // 它在，去背照就在。
  const canvasRoles = [
    ['cover', '.cover'],
    ['quote', '.quote-a'],
    ['quote', '.quote-b'],
  ];

  function guestRole() {
    const canvas = document.getElementById('canvas');
    if (canvas) {
      const hit = canvasRoles.find(([, klass]) => canvas.matches(klass));
      if (hit) return hit[0];
    }
    return currentPage()?.role || null;
  }

  function guestSpec() {
    const role = guestRole();
    return role ? guestLayoutSpecs[role] || null : null;
  }

  function boundedLayout(values, bounds) {
    return Object.fromEntries(Object.entries(bounds).map(([name, [minimum, maximum]]) => {
      const value = Number(values[name]);
      return [name, Math.round(Math.max(minimum, Math.min(maximum, value)))];
    }));
  }

  // 金句沒有 default 幾何可以寫進 schema（A 版與 B 版的算圖預設不同），所以
  // 由預覽量出目前的實際值當基準，母頁面拿它當「還沒調整過」的起點。
  function discoverGuestLayout() {
    // 這三個提早 return 原本什麼都不說。封面看不出來（母頁面一定會送
    // apply-layout，綁定另有其路），但**金句在還沒存過 override 時**，母頁面
    // 刻意不送 apply-layout——綁定完全押在這裡。失敗就又回到「拖了沒反應、
    // 沒有任何線索」（2026-09-03 review 抓到）。
    const spec = guestSpec();
    const page = currentPage();
    if (!spec) {
      emit('guest-editor-state', {bound: false, reason: '這張卡沒有可調整的去背照幾何'});
      return;
    }
    const guest = document.querySelector(spec.selector);
    if (!guest) {
      emit('guest-editor-state', {bound: false, reason: '預覽找不到來賓去背照'});
      return;
    }
    const computed = getComputedStyle(guest);
    const values = {
      guest_right_px: Math.round(parseFloat(computed.right)),
      guest_bottom_px: Math.round(parseFloat(computed.bottom)),
      guest_height_px: Math.round(parseFloat(computed.height)),
    };
    if (Object.values(values).some((value) => !Number.isFinite(value))) {
      emit('guest-editor-state', {bound: false, reason: '量不到去背照的位置與尺寸'});
      return;
    }
    // `page` 只用來標記 baseline 的角色；量測與綁定都不依賴它。
    emit('guest-layout-baseline', {role: page ? page.role : guestRole(), values});
    const seed = {...values, ...(spec.extraMeasure ? spec.extraMeasure() : {})};
    // 拖曳的綁定不可以押在「母頁面有沒有送 apply-layout」上。金句沒有 schema
    // default，母頁面要等這個基準值回去才知道要送什麼——先有雞先有蛋，結果就是
    // 金句永遠綁不上。這裡一量到就自己綁。
    if (layout === null && Object.keys(spec.bounds).every((name) => name in seed)) {
      layout = boundedLayout(seed, spec.bounds);
    }
    configureGuestInteraction();
  }

  function renderRich(node, text, emphasis, {kind = 'box', cover = false, preferredBreak = false} = {}) {
    if (!node) throw new Error('找不到預覽文字區塊');
    node.replaceChildren();
    const appendSegment = (segment) => {
      const at = segment.indexOf(emphasis);
      if (at < 0) throw new Error('強調文字不在主要文字中');
      node.append(document.createTextNode(segment.slice(0, at)));
      const mark = document.createElement('span');
      mark.className = kind === 'orange' ? 'em-orange' : 'em-box';
      mark.textContent = emphasis;
      node.append(mark, document.createTextNode(segment.slice(at + emphasis.length)));
    };
    if (cover) {
      const at = text.indexOf(emphasis);
      if (at > 0) {
        node.append(document.createTextNode(text.slice(0, at)), document.createElement('br'));
        const mark = document.createElement('span');
        mark.className = 'em-orange';
        mark.textContent = emphasis;
        node.append(mark, document.createTextNode(text.slice(at + emphasis.length)));
        return;
      }
    }
    const breakAt = preferredBreak ? text.indexOf('，') : -1;
    if (breakAt >= 0) {
      const first = text.slice(0, breakAt + 1);
      const second = text.slice(breakAt + 1);
      if (first.includes(emphasis)) {
        appendSegment(first);
        node.append(document.createElement('br'), document.createTextNode(second));
      } else {
        node.append(document.createTextNode(first), document.createElement('br'));
        appendSegment(second);
      }
      return;
    }
    appendSegment(text);
  }

  function refit() {
    window.requestAnimationFrame(() => {
      if (typeof window.__carouselRefit !== 'function') {
        emit('preview-error', {
          message: '預覽缺少可信的版面檢查器', fingerprint: diagnosticsFingerprint,
        });
        return;
      }
      const diagnostics = window.__carouselRefit();
      emit('diagnostics', {
        diagnostics, requestId: diagnosticsRequestId, fingerprint: diagnosticsFingerprint,
      });
    });
  }

  function applyCopy(role, values) {
    if (role === 'cover') {
      renderRich(document.querySelector('.cover-title'), values.headline, values.emphasis, {
        kind: 'orange', cover: true,
      });
      document.querySelector('.guest-label strong').textContent = values.guest_name;
      document.querySelector('.guest-label span').textContent = values.guest_title;
    } else if (role === 'hook') {
      renderRich(document.querySelector('.hook-title'), values.question, values.emphasis);
      document.querySelector('.hook-bridge').textContent = values.bridge;
    } else if (role === 'point') {
      renderRich(document.querySelector('.point-title'), values.headline, values.emphasis);
      document.querySelector('.point-body').textContent = values.body;
    } else if (role === 'quote') {
      renderRich(
        document.querySelector('.quote-a-text, .guest-answer .rich'),
        values.text,
        values.emphasis,
        {kind: 'orange'},
      );
      const byline = document.querySelector('.quote-by');
      if (byline) byline.textContent = `${values.guest_name}／訪談中`;
      const hostQuestion = document.querySelector('.host-question');
      if (hostQuestion && values.host_question) hostQuestion.textContent = values.host_question;
    } else if (role === 'cta') {
      renderRich(
        document.querySelector('.cta-title'),
        values.episode_topic,
        values.emphasis,
        {kind: 'orange', preferredBreak: true},
      );
    }
    refit();
  }

  // 換去背照。母頁面遞進來的是 data URL——預覽的 CSP 是
  // `img-src 'self' data:`，而 preview-assets 只服務 receipt 驗過的樣板快照，
  // 新選的那張還沒進快照，所以只能走 data:。
  const cutoutSelectors = {
    cutout: '#canvas.cover .guest',
    guest_cutout: '#canvas.quote-a .guest, #canvas.quote-b .guest-panel img',
  };

  function applyCutout(field, src) {
    const selector = cutoutSelectors[field];
    if (!selector) throw new Error(`不認得的素材欄位：${field}`);
    const node = document.querySelector(selector);
    if (!node) throw new Error('預覽找不到來賓去背照');
    return new Promise((resolve, reject) => {
      node.addEventListener('load', () => resolve(), {once: true});
      node.addEventListener('error', () => reject(new Error('去背照無法在預覽載入')), {once: true});
      node.src = src;
    }).then(() => {
      // 換圖會改變佈局：cover 的重疊診斷與拖曳控制點都吃 getBoundingClientRect。
      refit();
      const guest = document.querySelector('#canvas.cover .guest');
      if (guest) positionResizeHandle(guest);
    });
  }

  // 去背照的外框常常遠大於看得見的人——封面這張左邊與上面都是透明像素，
  // 外框左上角落在標題文字那一帶。控制點錨在外框角上，就變成「一個橘點浮在
  // 標題上」，看不出是那張照片的控制項（金句用的是 _trim 版，外框貼著人，
  // 所以一直沒問題）。這裡掃出不透明像素的邊界，把控制點錨到人身上。
  const opaqueInsetCache = new Map();

  function opaqueInset(img) {
    const key = img.currentSrc || img.src;
    if (!key) return {left: 0, right: 1, top: 0};
    if (opaqueInsetCache.has(key)) return opaqueInsetCache.get(key);
    let inset = {left: 0, right: 1, top: 0};
    try {
      if (img.naturalWidth && img.naturalHeight) {
        const size = 48;
        const probe = document.createElement('canvas');
        probe.width = size;
        probe.height = size;
        const ctx = probe.getContext('2d', {willReadFrequently: true});
        ctx.drawImage(img, 0, 0, size, size);
        const {data} = ctx.getImageData(0, 0, size, size);
        // 門檻拉到半透明以上：去背邊緣的羽化像素會讓邊界看起來比實際大一圈。
        let minX = size;
        let maxX = -1;
        let minY = size;
        for (let y = 0; y < size; y += 1) {
          for (let x = 0; x < size; x += 1) {
            if (data[(y * size + x) * 4 + 3] > 128) {
              if (x < minX) minX = x;
              if (x > maxX) maxX = x;
              if (y < minY) minY = y;
            }
          }
        }
        if (maxX >= minX && minY < size) {
          inset = {left: minX / size, right: (maxX + 1) / size, top: minY / size};
        }
      }
    } catch (error) {
      // 畫布被污染（非 data: 來源）就退回外框角落，不要讓控制點消失。
      inset = {left: 0, right: 1, top: 0};
    }
    opaqueInsetCache.set(key, inset);
    return inset;
  }

  function handleSize() {
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue('--editor-handle-size');
    return Math.max(24, parseFloat(raw) || 44);
  }

  function positionResizeHandle(guest) {
    const handle = document.querySelector('[data-carousel-resize-handle]');
    if (!handle) return;
    const rect = guest.getBoundingClientRect();
    const canvas = document.getElementById('canvas');
    const bounds = canvas ? canvas.getBoundingClientRect() : {left: 0, top: 0, width: 1080, height: 1080};
    // 控制點貼在**圖片外框**左上角，而去背照是靠右定位、寬度常常超過畫布：
    // 往右推到底時左緣會算出負數，控制點就跑到畫布外被 overflow:hidden 裁掉，
    // 於是「縮放」整個點不到。夾回畫布內，讓它永遠抓得到。
    const size = handleSize();
    const clamp = (value, max) => Math.max(0, Math.min(max, value));
    // 高度是往上長的（元素靠 `bottom` 定位），所以控制點抓上緣才對得上手感。
    // 水平放在人物中央——放外框左上角會落在標題文字上，看起來像個裝飾圓點。
    const inset = opaqueInset(guest);
    const anchorX = rect.left + rect.width * ((inset.left + inset.right) / 2);
    const anchorY = rect.top + rect.height * inset.top;
    handle.style.left = `${clamp(anchorX - bounds.left - size / 2, bounds.width - size)}px`;
    handle.style.top = `${clamp(anchorY - bounds.top - size / 2, bounds.height - size)}px`;
  }

  function applyLayout(values, {notify = false, refitNow = true} = {}) {
    const spec = guestSpec();
    if (!spec) {
      emit('preview-error', {
        message: '認不出這張卡的版型，無法套用去背照幾何',
        fingerprint: diagnosticsFingerprint,
      });
      return;
    }
    const bounded = boundedLayout(values, spec.bounds);
    // NaN 寫進 style 會被瀏覽器丟掉、什麼都不發生——那正是最難查的失敗樣態。
    // 寧可回報錯誤，也不要安靜地不動。
    if (Object.values(bounded).some((value) => !Number.isFinite(value))) {
      emit('preview-error', {
        message: '版面數值不完整，無法套用', fingerprint: diagnosticsFingerprint,
      });
      return;
    }
    layout = bounded;
    const guest = document.querySelector(spec.selector);
    if (!guest) {
      emit('preview-error', {
        message: '預覽找不到來賓去背照', fingerprint: diagnosticsFingerprint,
      });
      return;
    }
    // `!important` 而非普通 inline：算圖端注入的 layout override 是
    // `.cover .guest{right:…!important}`，而沒有標記的 inline style 打不贏
    // 樣式表的 !important。少了這個，拖曳會「數字有變、畫面不動」——
    // 而且只在**已經存過 override 的卡**上發生（r002 的封面有、金句沒有，
    // 這就是兩張卡表現不同的全部原因）。
    guest.style.setProperty('right', `${layout.guest_right_px}px`, 'important');
    guest.style.setProperty('bottom', `${layout.guest_bottom_px}px`, 'important');
    guest.style.setProperty('height', `${layout.guest_height_px}px`, 'important');
    // 標題字級只有封面有；金句的幾何不碰任何字級。
    if (Object.hasOwn(layout, 'title_font_size_px')) {
      const title = document.querySelector('.cover-title');
      if (!title) return;
      document.documentElement.style.setProperty(
        '--type-cover-title', `${layout.title_font_size_px}px`,
      );
      title.dataset.fitStart = String(layout.title_font_size_px);
    }
    positionResizeHandle(guest);
    if (notify) emit('layout-change', {layout});
    if (refitNow) refit();
  }

  function configureGuestInteraction() {
    const canvas = document.getElementById('canvas');
    const spec = guestSpec();
    if (!canvas || !spec) {
      emit('guest-editor-state', {bound: false, reason: '這張卡沒有可調整的去背照幾何'});
      return;
    }
    const guest = document.querySelector(spec.selector);
    if (!guest) {
      emit('guest-editor-state', {bound: false, reason: '預覽找不到來賓去背照'});
      return;
    }
    if (guest.dataset.editorReady === 'true') {
      emit('guest-editor-state', {bound: true, role: guestRole()});
      return;
    }
    guest.dataset.editorReady = 'true';
    const style = document.createElement('style');
    // 去背照原本沒有任何「可以拖」的提示，而畫面上又有兩個長得一樣的橘色方塊
    // （去背照的 z30、文字的 z40）。這裡給 hover/拖曳虛線框，並把去背照的控制點
    // 做成圓形，跟文字的方形控制點一眼分得開。
    // `.guest-label` 是 z-index:5，蓋在去背照上面，會把左下角的指標事件整塊吃掉——
    // 編輯時讓它不接事件，整張去背照才都抓得到（姓名與職稱本來就走文字欄位編輯）。
    style.textContent = '.guest[data-editor-ready="true"]{cursor:move;touch-action:none}.guest[data-editor-ready="true"]:hover,.guest[data-editor-ready="true"][data-dragging="true"]{outline:4px dashed var(--orange);outline-offset:6px}.cover .guest-label{pointer-events:none}.carousel-preview-resize{position:absolute;z-index:30;width:var(--editor-handle-size,44px);height:var(--editor-handle-size,44px);border:5px solid var(--ink);background:#fff;border-radius:50%;box-shadow:0 0 0 3px rgba(255,255,255,.9),0 4px 10px rgba(0,0,0,.35);cursor:nwse-resize;touch-action:none}.carousel-preview-resize::after{content:\"\";position:absolute;inset:22%;border-radius:50%;background:var(--orange)}';
    document.head.append(style);
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'carousel-preview-resize';
    handle.dataset.carouselResizeHandle = 'true';
    handle.setAttribute('aria-label', '調整來賓尺寸');
    canvas.append(handle);

    const begin = (event, mode) => {
      event.preventDefault();
      const target = event.currentTarget;
      target.setPointerCapture(event.pointerId);
      guest.dataset.dragging = 'true';
      const start = {x: event.clientX, y: event.clientY, values: {...layout}};
      const move = (next) => {
        const dx = next.clientX - start.x;
        const dy = next.clientY - start.y;
        if (mode === 'drag') {
          applyLayout({
            ...start.values,
            guest_right_px: start.values.guest_right_px - dx,
            guest_bottom_px: start.values.guest_bottom_px - dy,
          }, {refitNow: false});
        } else {
          applyLayout({
            ...start.values,
            guest_height_px: start.values.guest_height_px - dy,
          }, {refitNow: false});
        }
      };
      const cleanup = () => {
        delete guest.dataset.dragging;
        target.removeEventListener('pointermove', move);
        target.removeEventListener('pointerup', end);
        target.removeEventListener('pointercancel', cancel);
        target.removeEventListener('lostpointercapture', cancel);
      };
      const end = () => { cleanup(); applyLayout(layout, {notify: true}); };
      const cancel = () => { cleanup(); applyLayout(start.values); };
      target.addEventListener('pointermove', move);
      target.addEventListener('pointerup', end, {once: true});
      target.addEventListener('pointercancel', cancel, {once: true});
      target.addEventListener('lostpointercapture', cancel, {once: true});
    };
    guest.addEventListener('pointerdown', (event) => begin(event, 'drag'));
    handle.addEventListener('pointerdown', (event) => begin(event, 'resize'));
    positionResizeHandle(guest);
    emit('guest-editor-state', {bound: true, role: guestRole()});
  }

  function snap(value, step) { return Math.round(Number(value) / step) * step; }

  function currentPage() {
    const spec = window.__CAROUSEL_SPEC__;
    const index = Number(new URLSearchParams(window.location.search).get('page') || 0);
    return spec?.pages?.[index] || null;
  }

  function regionNodes() {
    return [...document.querySelectorAll('#canvas [data-fit-region]')];
  }

  function discoverTextLayouts() {
    const page = currentPage();
    if (!page) return;
    const layouts = regionNodes().map((node) => {
      const region = node.dataset.fitRegion.split('.').slice(1).join('.');
      const rect = node.getBoundingClientRect();
      return {
        page_id: page.page_id,
        role: page.role,
        region,
        values: {
          x_px: snap(rect.left, 4),
          y_px: snap(rect.top, 4),
          width_px: Math.max(80, snap(rect.width, 4)),
          font_start_px: Math.max(24, snap(parseFloat(getComputedStyle(node).fontSize), 2)),
          lines: null,
        },
      };
    });
    emit('text-layout-baseline', {layouts});
  }

  function boundedTextLayout(item) {
    const rect = textSafeRects[`${item.role}.${item.region}`] || [48, 48, 1032, 1032];
    const values = {...item.values};
    values.x_px = Math.max(rect[0], Math.min(rect[2] - 80, snap(values.x_px, 4)));
    values.y_px = Math.max(rect[1], Math.min(rect[3], snap(values.y_px, 4)));
    values.width_px = Math.max(80, Math.min(rect[2] - values.x_px, snap(values.width_px, 4)));
    values.font_start_px = Math.max(24, Math.min(160, snap(values.font_start_px, 2)));
    return {...item, values};
  }

  function positionTextHandles() {
    regionNodes().forEach((node) => {
      const region = node.dataset.fitRegion.split('.').slice(1).join('.');
      const handle = document.querySelector(`[data-text-resize-handle="${region}"]`);
      if (!handle) return;
      const rect = node.getBoundingClientRect();
      const halfHandle = Math.ceil(22 / visualScale);
      handle.style.left = `${rect.right - halfHandle}px`;
      handle.style.top = `${rect.top + Math.max(0, rect.height / 2 - halfHandle)}px`;
    });
  }

  function setSelectedTextRegion(region, {notify = true} = {}) {
    if (!regionNodes().some((node) => node.dataset.fitRegion.endsWith(`.${region}`))) return;
    selectedTextRegion = region;
    regionNodes().forEach((node) => {
      const candidate = node.dataset.fitRegion.split('.').slice(1).join('.');
      const selected = candidate === region;
      node.dataset.textRegionSelected = String(selected);
      const handle = document.querySelector(`[data-text-resize-handle="${candidate}"]`);
      if (handle) {
        handle.hidden = !selected;
        handle.disabled = !selected;
        handle.tabIndex = selected ? 0 : -1;
      }
    });
    if (notify) emit('text-region-select', {region});
  }

  function applyTextLayouts(layouts, {notifyRegion = null, refitNow = true} = {}) {
    textLayouts = layouts.map(boundedTextLayout);
    if (typeof window.applyEditorPatch !== 'function') {
      throw new Error('預覽缺少 canonical applyEditorPatch(layout)');
    }
    window.applyEditorPatch(textLayouts);
    positionTextHandles();
    if (notifyRegion) {
      const item = textLayouts.find((candidate) => candidate.region === notifyRegion);
      if (item) emit('text-layout-change', {region: notifyRegion, values: item.values});
    }
    if (refitNow) refit();
  }

  function configureTextInteractions() {
    const canvas = document.getElementById('canvas');
    const page = currentPage();
    if (!canvas || !page) return;
    if (!document.querySelector('style[data-text-layout-editor]')) {
      const style = document.createElement('style');
      style.dataset.textLayoutEditor = 'true';
      style.textContent = '[data-fit-region][data-text-editor-ready="true"]{cursor:move;touch-action:none;outline-offset:6px}[data-fit-region][data-text-region-selected="true"]{outline:4px solid var(--orange)}[data-fit-region][data-text-editor-ready="true"]:focus-visible{outline:4px solid var(--orange)}.carousel-preview-text-resize{position:absolute;z-index:40;width:var(--editor-handle-size,44px);height:var(--editor-handle-size,44px);border:4px solid var(--ink);background:var(--orange);cursor:ew-resize;touch-action:none}.carousel-preview-text-resize:focus-visible{outline:4px solid var(--white);outline-offset:2px}';
      document.head.append(style);
    }
    regionNodes().forEach((node) => {
      const region = node.dataset.fitRegion.split('.').slice(1).join('.');
      if (node.dataset.textEditorReady === 'true') return;
      node.dataset.textEditorReady = 'true';
      node.tabIndex = 0;
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'carousel-preview-text-resize';
      handle.dataset.textResizeHandle = region;
      handle.setAttribute('aria-label', `調整${region}文字寬度`);
      canvas.append(handle);
      const begin = (event, mode) => {
        event.preventDefault();
        setSelectedTextRegion(region);
        const source = textLayouts.find((item) => item.region === region);
        if (!source) return;
        const target = event.currentTarget;
        target.setPointerCapture(event.pointerId);
        const start = {x: event.clientX, y: event.clientY, item: structuredClone(source)};
        const move = (next) => {
          const dx = next.clientX - start.x;
          const dy = next.clientY - start.y;
          const values = {...start.item.values};
          if (mode === 'drag') {
            values.x_px += dx; values.y_px += dy;
          } else values.width_px += dx;
          applyTextLayouts(
            textLayouts.map((item) => item.region === region ? {...item, values} : item),
            {refitNow: false},
          );
        };
        const cleanup = () => {
          target.removeEventListener('pointermove', move);
          target.removeEventListener('pointerup', end);
          target.removeEventListener('pointercancel', cancel);
          target.removeEventListener('lostpointercapture', cancel);
        };
        const end = () => { cleanup(); applyTextLayouts(textLayouts, {notifyRegion: region}); };
        const cancel = () => {
          cleanup();
          applyTextLayouts(textLayouts.map((item) => item.region === region ? start.item : item));
        };
        target.addEventListener('pointermove', move);
        target.addEventListener('pointerup', end, {once: true});
        target.addEventListener('pointercancel', cancel, {once: true});
        target.addEventListener('lostpointercapture', cancel, {once: true});
      };
      node.addEventListener('pointerdown', (event) => begin(event, 'drag'));
      handle.addEventListener('pointerdown', (event) => begin(event, 'resize'));
      node.addEventListener('focus', () => setSelectedTextRegion(region));
      node.addEventListener('click', () => setSelectedTextRegion(region));
      node.addEventListener('keydown', (event) => {
        const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
        if (!directions[event.key]) return;
        event.preventDefault();
        const item = textLayouts.find((candidate) => candidate.region === region);
        if (!item) return;
        const distance = event.shiftKey ? 16 : 4;
        const [dx, dy] = directions[event.key];
        applyTextLayouts(textLayouts.map((candidate) => candidate.region === region
          ? {...candidate, values: {...candidate.values, x_px: candidate.values.x_px + dx * distance, y_px: candidate.values.y_px + dy * distance}}
          : candidate), {notifyRegion: region});
      });
      handle.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        setSelectedTextRegion(region);
        const distance = (event.shiftKey ? 16 : 4) * (event.key === 'ArrowLeft' ? -1 : 1);
        applyTextLayouts(textLayouts.map((candidate) => candidate.region === region
          ? {...candidate, values: {...candidate.values, width_px: candidate.values.width_px + distance}}
          : candidate), {notifyRegion: region});
      });
    });
    setSelectedTextRegion(selectedTextRegion || regionNodes()[0]?.dataset.fitRegion.split('.').slice(1).join('.'), {notify: false});
    positionTextHandles();
  }

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    emit('editor-escape', {region: selectedTextRegion});
  }, true);

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent || event.data?.channel !== channel) return;
    try {
      if (event.data.type === 'apply-copy') {
        applyCopy(event.data.role, event.data.values);
      } else if (event.data.type === 'apply-cutout') {
        // 換圖會改變 cover 的重疊量測，母頁面的「送出」按鈕又押在 fingerprint
        // 相符的診斷上。先換上新 fingerprint 再載圖，載完那次 refit 送出的
        // 診斷才會被母頁面採信——否則按鈕永遠停在 disabled。
        diagnosticsRequestId = event.data.requestId || null;
        diagnosticsFingerprint = event.data.fingerprint || null;
        applyCutout(event.data.field, event.data.src).catch((error) => {
          emit('preview-error', {message: error.message, fingerprint: diagnosticsFingerprint});
        });
      } else if (event.data.type === 'apply-layout') {
        applyLayout(event.data.values);
        configureGuestInteraction();
      } else if (event.data.type === 'apply-text-layouts') {
        textSafeRects = event.data.safeRects || {};
        diagnosticsRequestId = event.data.requestId || null;
        diagnosticsFingerprint = event.data.fingerprint || null;
        selectedTextRegion = event.data.selectedRegion || selectedTextRegion;
        applyTextLayouts(event.data.layouts || []);
        configureTextInteractions();
        if (selectedTextRegion) setSelectedTextRegion(selectedTextRegion, {notify: false});
      } else if (event.data.type === 'select-text-region') {
        setSelectedTextRegion(event.data.region, {notify: false});
      } else if (event.data.type === 'set-visual-scale') {
        visualScale = Math.max(0.1, Number(event.data.scale) || 1);
        document.documentElement.style.setProperty(
          '--editor-handle-size', `${Math.ceil(44 / visualScale)}px`,
        );
        positionTextHandles();
      } else if (event.data.type === 'set-presentation') {
        // 主畫面的卡片縮圖也用這份預覽來顯示草稿的樣子，但那裡不該出現拖曳圓點
        // 與文字選取外框——那是編輯器的控制項，不是卡片內容。
        document.documentElement.dataset.presentation = event.data.on ? 'true' : 'false';
      } else if (event.data.type === 'refit') {
        refit();
      }
    } catch (error) {
      emit('preview-error', {message: error.message, fingerprint: diagnosticsFingerprint});
    }
  });

  document.fonts.ready.then(() => { discoverTextLayouts(); discoverGuestLayout(); });
  // fonts.ready 若被延後或不觸發，上面那條就不會跑。綁定是這個編輯器的基本能力，
  // 不該押在單一時機上——立刻再試一次（重複呼叫是冪等的）。
  discoverGuestLayout();
  emit('ready');
})();
