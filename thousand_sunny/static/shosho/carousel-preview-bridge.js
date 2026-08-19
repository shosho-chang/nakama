(() => {
  'use strict';

  const channel = 'nakama-carousel-editor-v1';
  const layoutBounds = {
    guest_right_px: [-540, 240],
    guest_bottom_px: [-400, 240],
    guest_height_px: [480, 1400],
    title_font_size_px: [72, 160],
  };
  let layout = null;
  let textLayouts = [];
  let textSafeRects = {};
  let diagnosticsRequestId = null;
  let diagnosticsFingerprint = null;
  let selectedTextRegion = null;
  let visualScale = 1;

  function emit(type, payload = {}) {
    window.parent.postMessage({channel, type, ...payload}, '*');
  }

  function boundedLayout(values) {
    return Object.fromEntries(Object.entries(layoutBounds).map(([name, [minimum, maximum]]) => {
      const value = Number(values[name]);
      return [name, Math.round(Math.max(minimum, Math.min(maximum, value)))];
    }));
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

  function positionResizeHandle(guest) {
    const handle = document.querySelector('[data-carousel-resize-handle]');
    if (!handle) return;
    const rect = guest.getBoundingClientRect();
    handle.style.left = `${rect.left - 14}px`;
    handle.style.top = `${rect.top - 14}px`;
  }

  function applyLayout(values, {notify = false, refitNow = true} = {}) {
    layout = boundedLayout(values);
    const canvas = document.getElementById('canvas');
    const guest = canvas && canvas.querySelector('.guest');
    const title = canvas && canvas.querySelector('.cover-title');
    if (!guest || !title) return;
    guest.style.right = `${layout.guest_right_px}px`;
    guest.style.bottom = `${layout.guest_bottom_px}px`;
    guest.style.height = `${layout.guest_height_px}px`;
    document.documentElement.style.setProperty(
      '--type-cover-title', `${layout.title_font_size_px}px`,
    );
    title.dataset.fitStart = String(layout.title_font_size_px);
    positionResizeHandle(guest);
    if (notify) emit('layout-change', {layout});
    if (refitNow) refit();
  }

  function configureCoverInteraction() {
    const canvas = document.getElementById('canvas');
    const guest = canvas && document.querySelector('#canvas.cover .guest');
    if (!guest || guest.dataset.editorReady === 'true') return;
    guest.dataset.editorReady = 'true';
    const style = document.createElement('style');
    style.textContent = '.guest[data-editor-ready="true"]{cursor:move;touch-action:none}.carousel-preview-resize{position:absolute;z-index:30;width:var(--editor-handle-size,44px);height:var(--editor-handle-size,44px);border:4px solid var(--ink);background:var(--orange);cursor:nwse-resize;touch-action:none}';
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
      } else if (event.data.type === 'apply-layout') {
        applyLayout(event.data.values);
        configureCoverInteraction();
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
      } else if (event.data.type === 'refit') {
        refit();
      }
    } catch (error) {
      emit('preview-error', {message: error.message, fingerprint: diagnosticsFingerprint});
    }
  });

  document.fonts.ready.then(() => discoverTextLayouts());
  emit('ready');
})();
