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
        emit('preview-error', {message: '預覽缺少可信的版面檢查器'});
        return;
      }
      const diagnostics = window.__carouselRefit();
      emit('diagnostics', {diagnostics});
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

  function applyLayout(values, {notify = false} = {}) {
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
    refit();
  }

  function configureCoverInteraction() {
    const canvas = document.getElementById('canvas');
    const guest = canvas && document.querySelector('#canvas.cover .guest');
    if (!guest || guest.dataset.editorReady === 'true') return;
    guest.dataset.editorReady = 'true';
    const style = document.createElement('style');
    style.textContent = '.guest[data-editor-ready="true"]{cursor:move;touch-action:none}.carousel-preview-resize{position:absolute;z-index:30;width:32px;height:32px;border:4px solid #282525;background:#f57331;cursor:nwse-resize;touch-action:none}';
    document.head.append(style);
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'carousel-preview-resize';
    handle.dataset.carouselResizeHandle = 'true';
    handle.setAttribute('aria-label', '調整來賓尺寸');
    canvas.append(handle);

    const begin = (event, mode) => {
      event.preventDefault();
      const start = {x: event.clientX, y: event.clientY, values: {...layout}};
      const move = (next) => {
        const dx = next.clientX - start.x;
        const dy = next.clientY - start.y;
        if (mode === 'drag') {
          applyLayout({
            ...start.values,
            guest_right_px: start.values.guest_right_px - dx,
            guest_bottom_px: start.values.guest_bottom_px - dy,
          }, {notify: true});
        } else {
          applyLayout({
            ...start.values,
            guest_height_px: start.values.guest_height_px - dy,
          }, {notify: true});
        }
      };
      const end = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', end);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', end, {once: true});
    };
    guest.addEventListener('pointerdown', (event) => begin(event, 'drag'));
    handle.addEventListener('pointerdown', (event) => begin(event, 'resize'));
    positionResizeHandle(guest);
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent || event.data?.channel !== channel) return;
    try {
      if (event.data.type === 'apply-copy') {
        applyCopy(event.data.role, event.data.values);
      } else if (event.data.type === 'apply-layout') {
        applyLayout(event.data.values);
        configureCoverInteraction();
      } else if (event.data.type === 'refit') {
        refit();
      }
    } catch (error) {
      emit('preview-error', {message: error.message});
    }
  });

  emit('ready');
})();
