/**
 * SocAI Watch Mode — Real-time Agent Activity Sidebar
 *
 * Injected into the page by background.js when watch mode is enabled.
 * Uses Shadow DOM for complete isolation from the host page.
 *
 * Displays:
 *   - Agent thinking / reasoning (phase, observation, decision)
 *   - Actions (navigate, click, extract, scroll)
 *   - Results and errors
 *   - Warnings (anti-bot, timeouts)
 *   - Click highlights on the page
 */

(function () {
  'use strict';

  // Prevent double injection, but make a previously hidden panel visible again.
  const existingRoot = document.getElementById('socai-watch-root');
  if (existingRoot) {
    existingRoot.style.display = '';
    try {
      chrome.runtime.sendMessage({ action: 'watch_panel_ready' });
    } catch {}
    return;
  }

  // ── Constants ──────────────────────────────────────────────────

  const PANEL_WIDTH = 380;
  const HIGHLIGHT_DURATION = 2000;
  const MAX_ENTRIES = 500;

  const ENTRY_COLORS = {
    think:   { badge: '#a78bfa', bg: '#1e1b3a', border: '#7c3aed' },
    command: { badge: '#60a5fa', bg: '#1a2332', border: '#3b82f6' },
    action:  { badge: '#60a5fa', bg: '#1a2332', border: '#3b82f6' },
    result:  { badge: '#34d399', bg: '#1a2e24', border: '#10b981' },
    click:   { badge: '#f97316', bg: '#2a1f14', border: '#f97316' },
    extract: { badge: '#06b6d4', bg: '#162a30', border: '#06b6d4' },
    warning: { badge: '#fbbf24', bg: '#2a2414', border: '#f59e0b' },
    error:   { badge: '#f87171', bg: '#2a1a1a', border: '#ef4444' },
    info:    { badge: '#94a3b8', bg: '#1e2028', border: '#64748b' },
    session: { badge: '#c084fc', bg: '#1e1b3a', border: '#a855f7' },
  };

  const ENTRY_ICONS = {
    think:   '🧠',
    command: '▶',
    action:  '▶',
    result:  '✓',
    click:   '🖱',
    extract: '📋',
    warning: '⚠',
    error:   '✕',
    info:    'ℹ',
    session: '●',
  };

  // ── Create Host Element ────────────────────────────────────────

  const host = document.createElement('div');
  host.id = 'socai-watch-root';
  host.dataset.socai = 'watch-panel';
  host.style.cssText = [
    'position: fixed',
    'top: 0',
    'right: 0',
    'bottom: 0',
    `width: ${PANEL_WIDTH}px`,
    'z-index: 2147483647',
    'pointer-events: auto',
    'font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif',
  ].join(';');
  document.documentElement.appendChild(host);

  const shadow = host.attachShadow({ mode: 'closed' });

  // ── Styles ─────────────────────────────────────────────────────

  const style = document.createElement('style');
  style.textContent = `
    :host {
      all: initial;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    .panel {
      display: flex;
      flex-direction: column;
      height: 100vh;
      background: #0f1117;
      color: #e2e8f0;
      font-size: 12px;
      line-height: 1.5;
      border-left: 1px solid #2d3348;
      overflow: hidden;
    }

    /* ── Header ── */
    .header {
      flex-shrink: 0;
      padding: 12px 14px;
      background: linear-gradient(135deg, #1a1d2e 0%, #151825 100%);
      border-bottom: 1px solid #2d3348;
    }

    .header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .title {
      font-size: 13px;
      font-weight: 700;
      color: #f8fafc;
      letter-spacing: 0.5px;
    }

    .title-icon {
      color: #ff2442;
      margin-right: 6px;
    }

    .close-btn {
      background: none;
      border: none;
      color: #64748b;
      cursor: pointer;
      font-size: 16px;
      padding: 2px 6px;
      border-radius: 4px;
      line-height: 1;
    }
    .close-btn:hover { background: #1e2235; color: #94a3b8; }

    .status-row {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-top: 8px;
      font-size: 11px;
      color: #94a3b8;
    }

    .status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 4px;
    }
    .status-dot.on  { background: #34d399; box-shadow: 0 0 6px #34d399; }
    .status-dot.off { background: #f87171; }

    .counter {
      margin-left: auto;
      color: #64748b;
    }

    /* ── Filter bar ── */
    .filters {
      flex-shrink: 0;
      display: flex;
      gap: 4px;
      padding: 6px 14px;
      background: #13151f;
      border-bottom: 1px solid #1e2235;
      flex-wrap: wrap;
    }

    .filter-btn {
      background: #1e2235;
      border: 1px solid #2d3348;
      color: #94a3b8;
      font-size: 10px;
      padding: 2px 8px;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .filter-btn:hover { border-color: #475569; color: #e2e8f0; }
    .filter-btn.active { background: #2d3348; border-color: #475569; color: #f8fafc; }

    /* ── Activity Feed ── */
    .feed {
      flex: 1;
      overflow-y: auto;
      overflow-x: hidden;
      padding: 8px 10px;
      scroll-behavior: smooth;
    }

    .feed::-webkit-scrollbar { width: 5px; }
    .feed::-webkit-scrollbar-track { background: transparent; }
    .feed::-webkit-scrollbar-thumb { background: #2d3348; border-radius: 3px; }
    .feed::-webkit-scrollbar-thumb:hover { background: #475569; }

    .entry {
      margin-bottom: 6px;
      border-radius: 6px;
      border-left: 3px solid;
      padding: 8px 10px;
      animation: slideIn 0.2s ease-out;
      transition: opacity 0.2s;
    }

    .entry.hidden { display: none; }

    @keyframes slideIn {
      from { opacity: 0; transform: translateX(20px); }
      to   { opacity: 1; transform: translateX(0); }
    }

    .entry-header {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 4px;
    }

    .entry-time {
      font-size: 10px;
      color: #64748b;
      font-variant-numeric: tabular-nums;
      min-width: 42px;
    }

    .entry-badge {
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 1px 6px;
      border-radius: 3px;
      color: #0f1117;
    }

    .entry-icon {
      font-size: 12px;
      flex-shrink: 0;
    }

    .entry-action {
      font-size: 11px;
      color: #94a3b8;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }

    .entry-body {
      font-size: 11px;
      color: #cbd5e1;
      line-height: 1.6;
    }

    .entry-body .label {
      color: #64748b;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      margin-top: 4px;
      display: block;
    }

    .entry-body .value {
      color: #e2e8f0;
      display: block;
      padding-left: 0;
      word-break: break-word;
    }

    .entry-detail {
      margin-top: 4px;
      padding: 6px 8px;
      background: rgba(0,0,0,0.25);
      border-radius: 4px;
      font-size: 10px;
      color: #94a3b8;
      max-height: 0;
      overflow: hidden;
      transition: max-height 0.25s ease;
      cursor: pointer;
    }
    .entry-detail.open { max-height: 600px; }

    .entry-toggle {
      font-size: 10px;
      color: #475569;
      cursor: pointer;
      user-select: none;
      margin-top: 2px;
    }
    .entry-toggle:hover { color: #94a3b8; }

    .entry-coords {
      display: inline-block;
      background: #1e2235;
      padding: 1px 6px;
      border-radius: 3px;
      font-family: "SF Mono", "Fira Code", monospace;
      font-size: 10px;
      color: #f97316;
    }

    /* ── Empty state ── */
    .empty {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 200px;
      color: #475569;
      font-size: 12px;
      text-align: center;
    }
    .empty-icon { font-size: 32px; margin-bottom: 8px; opacity: 0.5; }

    /* ── Resize handle ── */
    .resize-handle {
      position: absolute;
      left: -3px;
      top: 0;
      bottom: 0;
      width: 6px;
      cursor: col-resize;
      z-index: 10;
    }
    .resize-handle:hover { background: rgba(99, 102, 241, 0.3); }
  `;
  shadow.appendChild(style);

  // ── Panel HTML ─────────────────────────────────────────────────

  const panel = document.createElement('div');
  panel.className = 'panel';
  panel.innerHTML = `
    <div class="header">
      <div class="header-top">
        <span class="title"><span class="title-icon">⊙</span>Watch Mode</span>
        <button class="close-btn" title="Close watch panel">✕</button>
      </div>
      <div class="status-row">
        <span><span class="status-dot on" id="wStatusDot"></span><span id="wStatusText">Active</span></span>
        <span id="wTimer">0.0s</span>
        <span class="counter"><span id="wCount">0</span> events</span>
      </div>
    </div>
    <div class="filters" id="wFilters">
      <button class="filter-btn active" data-filter="all">All</button>
      <button class="filter-btn" data-filter="think">Think</button>
      <button class="filter-btn" data-filter="action">Action</button>
      <button class="filter-btn" data-filter="click">Click</button>
      <button class="filter-btn" data-filter="extract">Extract</button>
      <button class="filter-btn" data-filter="result">Result</button>
      <button class="filter-btn" data-filter="warning">Warning</button>
    </div>
    <div class="feed" id="wFeed">
      <div class="empty">
        <div class="empty-icon">👁</div>
        Waiting for agent activity...
      </div>
    </div>
    <div class="resize-handle" id="wResize"></div>
  `;
  shadow.appendChild(panel);

  // ── State ──────────────────────────────────────────────────────

  let entries = [];
  let activeFilter = 'all';
  let autoScroll = true;
  let startTime = Date.now();
  let timerInterval = null;

  // ── DOM refs ───────────────────────────────────────────────────

  const feed = shadow.getElementById('wFeed');
  const countEl = shadow.getElementById('wCount');
  const timerEl = shadow.getElementById('wTimer');
  const filtersEl = shadow.getElementById('wFilters');
  const closeBtn = shadow.querySelector('.close-btn');
  const resizeHandle = shadow.getElementById('wResize');

  // ── Timer ──────────────────────────────────────────────────────

  function updateTimer() {
    const elapsed = (Date.now() - startTime) / 1000;
    timerEl.textContent = elapsed < 60
      ? `${elapsed.toFixed(1)}s`
      : `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s`;
  }
  timerInterval = setInterval(updateTimer, 200);

  // ── Filters ────────────────────────────────────────────────────

  filtersEl.addEventListener('click', (e) => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    activeFilter = btn.dataset.filter;
    filtersEl.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applyFilter();
  });

  function applyFilter() {
    feed.querySelectorAll('.entry').forEach(el => {
      if (activeFilter === 'all' || el.dataset.kind === activeFilter) {
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
    });
  }

  // ── Auto-scroll logic ─────────────────────────────────────────

  feed.addEventListener('scroll', () => {
    const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
    autoScroll = atBottom;
  });

  function scrollToBottom() {
    if (autoScroll) {
      feed.scrollTop = feed.scrollHeight;
    }
  }

  // ── Close button ───────────────────────────────────────────────

  closeBtn.addEventListener('click', () => {
    host.style.display = 'none';
    clearInterval(timerInterval);
    // Notify background that watch panel was closed by user
    try {
      chrome.runtime.sendMessage({ action: 'watch_panel_closed' });
    } catch {}
  });

  // ── Resize handle ──────────────────────────────────────────────

  let resizing = false;
  resizeHandle.addEventListener('mousedown', (e) => {
    resizing = true;
    e.preventDefault();
    const onMove = (me) => {
      const newWidth = Math.max(280, Math.min(600, window.innerWidth - me.clientX));
      host.style.width = newWidth + 'px';
    };
    const onUp = () => {
      resizing = false;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });

  // ── Entry Rendering ────────────────────────────────────────────

  function formatTime(ts) {
    if (typeof ts === 'number') {
      return ts < 60 ? `${ts.toFixed(1)}s` : `${Math.floor(ts / 60)}m${Math.floor(ts % 60)}s`;
    }
    return ((Date.now() - startTime) / 1000).toFixed(1) + 's';
  }

  function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
  }

  function truncate(str, len) {
    if (!str) return '';
    return str.length > len ? str.slice(0, len) + '…' : str;
  }

  function createEntryElement(entry) {
    const kind = entry.kind || 'info';
    const colors = ENTRY_COLORS[kind] || ENTRY_COLORS.info;
    const icon = ENTRY_ICONS[kind] || 'ℹ';

    const el = document.createElement('div');
    el.className = 'entry';
    el.dataset.kind = kind;
    el.style.borderLeftColor = colors.border;
    el.style.background = colors.bg;

    if (activeFilter !== 'all' && activeFilter !== kind) {
      el.classList.add('hidden');
    }

    let bodyHtml = '';
    let detailHtml = '';
    let actionText = entry.action || entry.phase || '';

    switch (kind) {
      case 'think':
        actionText = entry.phase || 'thinking';
        bodyHtml = `<span class="value">${escapeHtml(truncate(entry.decision || entry.message, 200))}</span>`;
        if (entry.observation || entry.reasoning || entry.evidence) {
          detailHtml = '';
          if (entry.observation) detailHtml += `<span class="label">Observation</span><span class="value">${escapeHtml(entry.observation)}</span>`;
          if (entry.reasoning) detailHtml += `<span class="label">Reasoning</span><span class="value">${escapeHtml(entry.reasoning)}</span>`;
          if (entry.evidence) detailHtml += `<span class="label">Evidence</span><span class="value">${escapeHtml(entry.evidence)}</span>`;
        }
        break;

      case 'click':
        actionText = 'click';
        bodyHtml = '';
        if (entry.x !== undefined && entry.y !== undefined) {
          bodyHtml += `<span class="entry-coords">(${entry.x}, ${entry.y})</span> `;
        }
        if (entry.target) bodyHtml += escapeHtml(truncate(entry.target, 120));
        if (entry.message) bodyHtml += escapeHtml(truncate(entry.message, 120));
        break;

      case 'command':
      case 'action':
        actionText = entry.action || '';
        bodyHtml = entry.detail || entry.message
          ? `<span class="value">${escapeHtml(truncate(entry.detail || entry.message, 200))}</span>`
          : '';
        if (entry.params && Object.keys(entry.params).length > 0) {
          const paramStr = JSON.stringify(entry.params, null, 1);
          if (paramStr.length > 4) {
            detailHtml = `<span class="label">Params</span><span class="value" style="font-family: monospace; font-size: 10px;">${escapeHtml(truncate(paramStr, 500))}</span>`;
          }
        }
        break;

      case 'result':
        actionText = entry.action || 'result';
        bodyHtml = entry.message || entry.detail
          ? `<span class="value">${escapeHtml(truncate(entry.message || entry.detail, 200))}</span>`
          : '';
        if (entry.duration !== undefined) {
          bodyHtml += ` <span style="color:#64748b;font-size:10px">(${entry.duration.toFixed(2)}s)</span>`;
        }
        break;

      case 'warning':
      case 'error':
        bodyHtml = `<span class="value">${escapeHtml(truncate(entry.message || entry.detail, 300))}</span>`;
        break;

      case 'extract':
        actionText = entry.action || 'extract';
        bodyHtml = entry.message
          ? `<span class="value">${escapeHtml(truncate(entry.message, 200))}</span>`
          : '';
        break;

      case 'session':
        bodyHtml = `<span class="value">${escapeHtml(entry.message || 'Session event')}</span>`;
        break;

      default:
        bodyHtml = `<span class="value">${escapeHtml(truncate(entry.message || entry.detail || JSON.stringify(entry), 200))}</span>`;
    }

    let html = `
      <div class="entry-header">
        <span class="entry-time">${formatTime(entry.timestamp)}</span>
        <span class="entry-icon">${icon}</span>
        <span class="entry-badge" style="background:${colors.badge}">${kind.toUpperCase()}</span>
        <span class="entry-action">${escapeHtml(actionText)}</span>
      </div>
    `;

    if (bodyHtml) {
      html += `<div class="entry-body">${bodyHtml}</div>`;
    }

    if (detailHtml) {
      html += `<div class="entry-toggle">▸ details</div>`;
      html += `<div class="entry-detail">${detailHtml}</div>`;
    }

    el.innerHTML = html;

    // Toggle detail expansion
    const toggle = el.querySelector('.entry-toggle');
    const detail = el.querySelector('.entry-detail');
    if (toggle && detail) {
      toggle.addEventListener('click', () => {
        const isOpen = detail.classList.toggle('open');
        toggle.textContent = isOpen ? '▾ details' : '▸ details';
      });
    }

    return el;
  }

  // ── Add Entry ──────────────────────────────────────────────────

  function addEntry(entry) {
    // Remove empty state on first entry
    const empty = feed.querySelector('.empty');
    if (empty) empty.remove();

    entries.push(entry);
    if (entries.length > MAX_ENTRIES) {
      entries.shift();
      const first = feed.querySelector('.entry');
      if (first) first.remove();
    }

    const el = createEntryElement(entry);
    feed.appendChild(el);
    countEl.textContent = entries.length;

    // Defer scroll to next frame for smooth animation
    requestAnimationFrame(scrollToBottom);
  }

  // ── Bulk Load (for re-injection after navigation) ──────────────

  function loadEntries(allEntries) {
    feed.innerHTML = '';
    entries = [];
    for (const entry of allEntries) {
      entries.push(entry);
      feed.appendChild(createEntryElement(entry));
    }
    countEl.textContent = entries.length;
    requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
  }

  // ── Highlight Rendering (on the host page, outside shadow) ─────

  function showClickHighlight(x, y) {
    const marker = document.createElement('div');
    marker.className = 'socai-click-marker';
    marker.style.cssText = [
      'position: fixed',
      `left: ${x}px`,
      `top: ${y}px`,
      'width: 0',
      'height: 0',
      'border-radius: 50%',
      'background: radial-gradient(circle, rgba(255,36,66,0.6) 0%, rgba(255,36,66,0) 70%)',
      'pointer-events: none',
      'z-index: 2147483646',
      'transform: translate(-50%, -50%)',
      'transition: width 0.3s ease-out, height 0.3s ease-out, opacity 0.8s ease-out',
      'opacity: 1',
    ].join(';');
    document.documentElement.appendChild(marker);

    // Expand
    requestAnimationFrame(() => {
      marker.style.width = '50px';
      marker.style.height = '50px';
    });

    // Also show a small persistent ring
    const ring = document.createElement('div');
    ring.className = 'socai-click-ring';
    ring.style.cssText = [
      'position: fixed',
      `left: ${x}px`,
      `top: ${y}px`,
      'width: 16px',
      'height: 16px',
      'border-radius: 50%',
      'border: 2px solid #ff2442',
      'pointer-events: none',
      'z-index: 2147483646',
      'transform: translate(-50%, -50%)',
      'box-shadow: 0 0 8px rgba(255,36,66,0.5)',
      'transition: opacity 0.5s ease-out 1.2s',
      'opacity: 1',
    ].join(';');
    document.documentElement.appendChild(ring);

    // Fade and remove
    setTimeout(() => {
      marker.style.opacity = '0';
      ring.style.opacity = '0';
    }, HIGHLIGHT_DURATION * 0.6);

    setTimeout(() => {
      marker.remove();
      ring.remove();
    }, HIGHLIGHT_DURATION);
  }

  function showElementHighlight(selector) {
    try {
      const el = document.querySelector(selector);
      if (!el) return;
      highlightElement(el);
    } catch {}
  }

  function highlightElement(el) {
    if (!el) return;
    const rect = el.getBoundingClientRect();

    const overlay = document.createElement('div');
    overlay.className = 'socai-element-highlight';
    overlay.style.cssText = [
      'position: fixed',
      `left: ${rect.left - 3}px`,
      `top: ${rect.top - 3}px`,
      `width: ${rect.width + 6}px`,
      `height: ${rect.height + 6}px`,
      'border: 2px solid #ff2442',
      'border-radius: 4px',
      'background: rgba(255, 36, 66, 0.08)',
      'pointer-events: none',
      'z-index: 2147483646',
      'box-shadow: 0 0 12px rgba(255, 36, 66, 0.3), inset 0 0 12px rgba(255, 36, 66, 0.05)',
      'transition: opacity 0.6s ease-out',
      'opacity: 1',
    ].join(';');

    // Label
    const label = document.createElement('div');
    label.style.cssText = [
      'position: absolute',
      'top: -20px',
      'left: 0',
      'background: #ff2442',
      'color: white',
      'font-size: 10px',
      'font-family: -apple-system, sans-serif',
      'padding: 1px 6px',
      'border-radius: 3px',
      'white-space: nowrap',
      'pointer-events: none',
    ].join(';');
    label.textContent = el.tagName.toLowerCase() + (el.className ? '.' + el.className.split(' ')[0] : '');
    overlay.appendChild(label);

    document.documentElement.appendChild(overlay);

    setTimeout(() => { overlay.style.opacity = '0'; }, HIGHLIGHT_DURATION * 0.7);
    setTimeout(() => { overlay.remove(); }, HIGHLIGHT_DURATION);
  }

  // ── Message Listener ───────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === 'watch_event') {
      addEntry(msg.data);
      return;
    }

    if (msg.type === 'watch_highlight') {
      if (msg.mode === 'coords') {
        showClickHighlight(msg.x, msg.y);
      } else if (msg.mode === 'selector') {
        showElementHighlight(msg.selector);
      } else if (msg.mode === 'element_rect') {
        // Direct rect-based highlight
        const overlay = document.createElement('div');
        overlay.className = 'socai-element-highlight';
        overlay.style.cssText = [
          'position: fixed',
          `left: ${(msg.rect?.left || 0) - 3}px`,
          `top: ${(msg.rect?.top || 0) - 3}px`,
          `width: ${(msg.rect?.width || 0) + 6}px`,
          `height: ${(msg.rect?.height || 0) + 6}px`,
          'border: 2px solid #ff2442',
          'border-radius: 4px',
          'background: rgba(255, 36, 66, 0.08)',
          'pointer-events: none',
          'z-index: 2147483646',
          'box-shadow: 0 0 12px rgba(255, 36, 66, 0.3)',
          'transition: opacity 0.6s ease-out',
          'opacity: 1',
        ].join(';');
        document.documentElement.appendChild(overlay);
        setTimeout(() => { overlay.style.opacity = '0'; }, HIGHLIGHT_DURATION * 0.7);
        setTimeout(() => { overlay.remove(); }, HIGHLIGHT_DURATION);
      }
      return;
    }

    if (msg.type === 'watch_load_history') {
      if (msg.startTime) startTime = msg.startTime;
      loadEntries(msg.entries || []);
      return;
    }

    if (msg.type === 'watch_panel_show') {
      host.style.display = '';
      return;
    }

    if (msg.type === 'watch_panel_hide') {
      host.style.display = 'none';
      return;
    }
  });

  // ── Initial session entry ──────────────────────────────────────

  addEntry({
    kind: 'session',
    message: 'Watch mode active — monitoring agent activity',
    timestamp: 0,
  });

  // ── Notify background that panel is ready ──────────────────────

  try {
    chrome.runtime.sendMessage({ action: 'watch_panel_ready' });
  } catch {}

})();
