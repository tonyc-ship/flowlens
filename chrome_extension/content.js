/**
 * FlowLens Agent — Content Script (v2)
 *
 * Handles generic DOM actions, watch UI, and platform adapter dispatch.
 * Communicates with background.js which relays to the external Python agent.
 *
 * Responsibilities:
 * - In-page watch overlay and interaction highlights
 * - Generic DOM/media helpers
 * - Command dispatch to platform adapters
 */

// ── Watch Mode: Element Highlighting ──────────────────────────

function watchHighlightElement(el) {
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const overlay = document.createElement('div');
  overlay.className = 'flowlens-element-highlight';
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

  // Label showing what element was targeted
  const label = document.createElement('div');
  const tag = el.tagName.toLowerCase();
  const cls = el.className ? '.' + String(el.className).split(' ')[0] : '';
  label.textContent = tag + cls;
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
  overlay.appendChild(label);
  document.documentElement.appendChild(overlay);

  setTimeout(() => { overlay.style.opacity = '0'; }, 1400);
  setTimeout(() => { overlay.remove(); }, 2000);
}

// ── Watch Mode: In-Page Overlay ───────────────────────────────

let watchOverlayHost = null;
let watchOverlayShadow = null;
let watchOverlayFeed = null;
let watchOverlayDot = null;
let watchOverlayStatusText = null;
let watchOverlayEntries = [];
let watchOverlayStartTime = Date.now();
let watchOverlayTaskText = '';
let watchOverlayAutoScroll = true;

function ensureWatchOverlay() {
  if (watchOverlayHost) return watchOverlayHost;

  watchOverlayHost = document.createElement('div');
  watchOverlayHost.id = 'flowlens-watch-overlay';
  watchOverlayHost.style.cssText = [
    'position: fixed',
    'top: 88px',
    'right: 16px',
    'width: 320px',
    'max-height: min(52vh, 460px)',
    'z-index: 2147483645',
    'pointer-events: auto',
  ].join(';');
  document.documentElement.appendChild(watchOverlayHost);

  watchOverlayShadow = watchOverlayHost.attachShadow({ mode: 'open' });

  const style = document.createElement('style');
  style.textContent = `
    * { box-sizing: border-box; }
    .hud {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border: 1px solid rgba(99, 102, 241, 0.24);
      border-radius: 14px;
      background: rgba(14, 17, 24, 0.68);
      backdrop-filter: blur(10px);
      color: #e5e7eb;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.24);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
    }
    .header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.14);
      background: rgba(15, 23, 42, 0.48);
    }
    .title {
      font-size: 12px;
      font-weight: 700;
      color: #f8fafc;
      letter-spacing: 0.2px;
    }
    .subtitle {
      margin-top: 2px;
      font-size: 11px;
      color: rgba(203, 213, 225, 0.9);
      word-break: break-word;
    }
    .grow { flex: 1; min-width: 0; }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #64748b;
      box-shadow: none;
      flex-shrink: 0;
    }
    .dot.on {
      background: #34d399;
      box-shadow: 0 0 10px rgba(52, 211, 153, 0.75);
    }
    .toggle {
      border: 0;
      background: rgba(30, 41, 59, 0.58);
      color: #cbd5e1;
      border-radius: 999px;
      width: 26px;
      height: 26px;
      cursor: pointer;
      font-size: 13px;
      line-height: 1;
      flex-shrink: 0;
    }
    .feed {
      display: flex;
      flex-direction: column;
      gap: 8px;
      overflow-y: auto;
      padding: 10px 12px 12px;
      max-height: 360px;
      overscroll-behavior: contain;
    }
    .feed.hidden { display: none; }
    .feed::-webkit-scrollbar { width: 6px; }
    .feed::-webkit-scrollbar-thumb {
      background: rgba(100, 116, 139, 0.35);
      border-radius: 999px;
    }
    .entry {
      border-radius: 10px;
      padding: 8px 10px;
      border: 1px solid rgba(148, 163, 184, 0.08);
      background: rgba(15, 23, 42, 0.48);
    }
    .entry-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 5px;
    }
    .entry-kind {
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 2px 6px;
      border-radius: 999px;
      background: rgba(99, 102, 241, 0.22);
      color: #c7d2fe;
    }
    .entry-time {
      font-size: 10px;
      color: #64748b;
      margin-left: auto;
    }
    .entry-action {
      font-size: 11px;
      font-weight: 600;
      color: #f8fafc;
      margin-bottom: 3px;
      word-break: break-word;
    }
    .entry-message {
      font-size: 11px;
      line-height: 1.45;
      color: #cbd5e1;
      word-break: break-word;
    }
    .empty {
      padding: 16px 10px;
      text-align: center;
      color: #64748b;
      font-size: 11px;
    }
  `;
  watchOverlayShadow.appendChild(style);

  const panel = document.createElement('div');
  panel.className = 'hud';
  panel.innerHTML = `
    <div class="header">
      <span class="dot" id="cvWatchDot"></span>
      <div class="grow">
        <div class="title">FlowLens Agent Status</div>
        <div class="subtitle" id="cvWatchStatus">${escapeWatchHtml(watchLocaleText('Waiting for agent activity...', '等待 Agent 步骤...'))}</div>
      </div>
      <button class="toggle" id="cvWatchToggle" title="Collapse">−</button>
    </div>
    <div class="feed" id="cvWatchFeed">
      <div class="empty">${escapeWatchHtml(watchLocaleText('Waiting for agent activity...', '等待 Agent 步骤...'))}</div>
    </div>
  `;
  watchOverlayShadow.appendChild(panel);

  watchOverlayFeed = watchOverlayShadow.getElementById('cvWatchFeed');
  watchOverlayDot = watchOverlayShadow.getElementById('cvWatchDot');
  watchOverlayStatusText = watchOverlayShadow.getElementById('cvWatchStatus');

  const toggle = watchOverlayShadow.getElementById('cvWatchToggle');
  toggle.addEventListener('click', () => {
    const hidden = watchOverlayFeed.classList.toggle('hidden');
    toggle.textContent = hidden ? '+' : '−';
  });
  watchOverlayFeed.addEventListener('scroll', () => {
    watchOverlayAutoScroll = watchOverlayFeed.scrollHeight - watchOverlayFeed.scrollTop - watchOverlayFeed.clientHeight < 48;
  });

  return watchOverlayHost;
}

function hideWatchOverlay() {
  if (watchOverlayHost) {
    watchOverlayHost.style.display = 'none';
  }
}

function showWatchOverlay() {
  ensureWatchOverlay();
  watchOverlayHost.style.display = '';
}

function setWatchOverlayCaptureHidden(hidden) {
  const hosts = [
    watchOverlayHost,
    document.getElementById('flowlens-watch-overlay'),
    document.getElementById('flowlens-watch-root'),
  ].filter(Boolean);
  hosts.forEach((host) => {
    if (hidden) {
      if (host.dataset.flowlensCaptureDisplay === undefined) {
        host.dataset.flowlensCaptureDisplay = host.style.display || '';
      }
      host.style.display = 'none';
    } else if (host.dataset.flowlensCaptureDisplay !== undefined) {
      host.style.display = host.dataset.flowlensCaptureDisplay;
      delete host.dataset.flowlensCaptureDisplay;
    } else {
      host.style.display = '';
    }
  });
}

function watchOverlayTime(ts) {
  if (typeof ts !== 'number') return '';
  if (ts < 60) return `${ts.toFixed(1)}s`;
  return `${Math.floor(ts / 60)}m ${Math.floor(ts % 60)}s`;
}

function watchOverlaySummary(entry) {
  return String(
    entry?.decision ||
    entry?.message ||
    entry?.detail ||
    entry?.observation ||
    entry?.reasoning ||
    entry?.evidence ||
    ''
  ).trim();
}

function escapeWatchHtml(value) {
  const node = document.createElement('div');
  node.textContent = String(value || '');
  return node.innerHTML;
}

function isXhsWatchContext() {
  return /(^|\.)xiaohongshu\.com$/i.test(window.location.hostname);
}

function watchLocaleText(en, zh) {
  return isXhsWatchContext() ? zh : en;
}

const WATCH_KIND_LABELS_ZH = {
  think: '思考',
  command: '指令',
  action: '操作',
  result: '结果',
  click: '点击',
  extract: '提取',
  warning: '警告',
  error: '错误',
  info: '信息',
  session: '会话',
};

const WATCH_ACTION_LABELS_ZH = {
  start: '开始任务',
  turn: '执行轮次',
  thinking: '思考总结',
  tool: '调用工具',
  navigate: '打开页面',
  go_back: '返回上一页',
  click_at: '点击坐标',
  click_card: '打开笔记卡片',
  click_note_by_id: '打开指定笔记',
  click_note_link: '打开笔记链接',
  click_search_tab: '切换搜索分类',
  submit_search_query: '提交搜索关键词',
  extract_search_cards: '读取搜索结果',
  extract_note_content: '读取笔记内容',
  extract_comments: '读取评论',
  extract_profile_info: '读取作者主页',
  extract_profile_notes: '读取作者笔记',
  collect_carousel_images: '收集笔记图片',
  detect_state: '识别页面状态',
  get_search_page_state: '检查搜索状态',
  scroll_page: '滚动页面',
  scroll_note: '滚动笔记',
  press_key: '按键操作',
  type_text: '输入文字',
  run_js: '执行页面脚本',
  get_tab_info: '读取标签页信息',
  create_background_window: '创建任务窗口',
  create_watch_window: '打开状态栏',
  xhs_topic_scan: '小红书话题扫描',
  run_site_action: '小红书页面操作',
  extract_site_entity: '提取小红书页面信息',
};

function watchKindLabel(kind) {
  if (!isXhsWatchContext()) return String(kind || 'info').toUpperCase();
  return WATCH_KIND_LABELS_ZH[kind] || String(kind || '信息');
}

function watchActionLabel(entry) {
  const raw = String(entry.action || entry.phase || 'update');
  if (!isXhsWatchContext()) return raw;
  const lower = raw.toLowerCase();
  if (lower === 'turn') {
    const match = String(entry.message || '').match(/Turn\s+(\d+)\/(\d+)/i);
    return match ? `第 ${match[1]}/${match[2]} 轮` : '执行轮次';
  }
  if (entry.phase === 'tool' && entry.action) {
    return WATCH_ACTION_LABELS_ZH[lower] || `调用工具：${entry.action}`;
  }
  return WATCH_ACTION_LABELS_ZH[lower] || raw;
}

function taskTextFromEntry(entry) {
  const message = String(entry?.message || '').trim();
  if (!message) return '';
  if (entry?.phase === 'start' || /^Task started:/i.test(message)) {
    return message.replace(/^Task started:\s*/i, '').trim();
  }
  return '';
}

function updateWatchTaskFromEntry(entry) {
  const taskText = taskTextFromEntry(entry);
  if (taskText) {
    watchOverlayTaskText = watchLocaleText(`Task: ${taskText}`, `问题：${taskText}`);
    if (watchOverlayStatusText) {
      watchOverlayStatusText.textContent = watchOverlayTaskText;
    }
  }
}

function renderWatchOverlay() {
  ensureWatchOverlay();
  if (!watchOverlayEntries.length) {
    watchOverlayFeed.innerHTML = `<div class="empty">${escapeWatchHtml(watchLocaleText('Waiting for agent activity...', '等待 Agent 步骤...'))}</div>`;
    return;
  }

  const previousScrollTop = watchOverlayFeed.scrollTop;
  watchOverlayFeed.innerHTML = watchOverlayEntries.map((entry) => {
    const kind = String(entry.kind || 'info');
    const action = watchActionLabel(entry);
    const message = watchOverlaySummary(entry);
    return `
      <div class="entry">
        <div class="entry-head">
          <span class="entry-kind">${escapeWatchHtml(watchKindLabel(kind))}</span>
          <span class="entry-time">${watchOverlayTime(entry.timestamp)}</span>
        </div>
        <div class="entry-action">${escapeWatchHtml(action)}</div>
        <div class="entry-message">${escapeWatchHtml(message || watchLocaleText('No details yet.', '暂无详情。'))}</div>
      </div>
    `;
  }).join('');
  if (watchOverlayAutoScroll) {
    watchOverlayFeed.scrollTop = watchOverlayFeed.scrollHeight;
  } else {
    watchOverlayFeed.scrollTop = previousScrollTop;
  }
}

function updateWatchOverlayStatus(status) {
  ensureWatchOverlay();
  const active = !!status?.watchMode;
  watchOverlayDot.classList.toggle('on', active);
  watchOverlayStatusText.textContent = watchOverlayTaskText || (active
    ? watchLocaleText('Task running', '任务运行中')
    : watchLocaleText('Task idle', '任务空闲'));
  if (active) {
    showWatchOverlay();
  } else if (!watchOverlayEntries.length) {
    hideWatchOverlay();
  }
}

function setWatchOverlayState(payload) {
  watchOverlayStartTime = payload?.startTime || Date.now();
  watchOverlayEntries = Array.isArray(payload?.entries)
    ? payload.entries.slice()
    : [];
  const startEntry = watchOverlayEntries.find((entry) => taskTextFromEntry(entry));
  if (startEntry) updateWatchTaskFromEntry(startEntry);
  if (payload?.status) {
    updateWatchOverlayStatus(payload.status);
  } else {
    showWatchOverlay();
  }
  renderWatchOverlay();
}

function appendWatchOverlayEntry(entry) {
  if (!entry) return;
  if (entry.timestamp === undefined) {
    entry.timestamp = Math.max(0, (Date.now() - watchOverlayStartTime) / 1000);
  }
  updateWatchTaskFromEntry(entry);
  watchOverlayEntries.push(entry);
  showWatchOverlay();
  renderWatchOverlay();
}

// ── Utility ────────────────────────────────────────────────────

function $(selector, root = document) {
  return root.querySelector(selector);
}

function $$(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

function text(el) {
  return el ? el.textContent.trim() : '';
}

/** Try multiple selectors, return first non-empty text */
function firstText(selectors, root = document) {
  for (const sel of selectors) {
    const el = root.querySelector(sel);
    if (el && el.textContent.trim()) return el.textContent.trim();
  }
  return '';
}

function wait(ms) {
  return new Promise(r => setTimeout(r, ms));
}

const mediaRequestLog = [];

function recordMediaRequest(entry) {
  if (!entry || !entry.url) return;
  const normalized = {
    url: String(entry.url),
    source: entry.source || 'page_hook',
    ts: entry.ts || Date.now(),
    kind: inferVideoKind(entry.url),
  };
  mediaRequestLog.push(normalized);
  if (mediaRequestLog.length > 500) {
    mediaRequestLog.splice(0, mediaRequestLog.length - 500);
  }
}

function installMediaRequestHook() {
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (event.data?.type !== 'flowlens_media_request') return;
    recordMediaRequest(event.data.payload);
  });

  const script = document.createElement('script');
  script.dataset.flowlensMediaHook = 'true';
  script.textContent = `
    (() => {
      if (window.__flowlensMediaHookInstalled) return;
      window.__flowlensMediaHookInstalled = true;

      const emit = (payload) => {
        try {
          window.postMessage({
            type: 'flowlens_media_request',
            payload: { ...payload, ts: Date.now(), href: location.href }
          }, '*');
        } catch {}
      };

      const normalizeUrl = (value) => {
        try {
          return new URL(String(value), location.href).href;
        } catch {
          return String(value || '');
        }
      };

      const originalFetch = window.fetch;
      if (originalFetch) {
        window.fetch = function(input, init) {
          const raw = typeof input === 'string' ? input : input?.url;
          if (raw) emit({ url: normalizeUrl(raw), source: 'fetch' });
          return originalFetch.apply(this, arguments);
        };
      }

      const originalOpen = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(method, url) {
        if (url) emit({ url: normalizeUrl(url), source: 'xhr' });
        return originalOpen.apply(this, arguments);
      };

      const originalCreateObjectURL = URL.createObjectURL;
      if (originalCreateObjectURL) {
        URL.createObjectURL = function(obj) {
          const blobUrl = originalCreateObjectURL.call(this, obj);
          emit({
            url: blobUrl,
            source: 'createObjectURL',
            blob_type: obj?.type || '',
            blob_size: obj?.size || 0,
          });
          return blobUrl;
        };
      }
    })();
  `;
  (document.documentElement || document.head || document.body).appendChild(script);
  script.remove();
}

installMediaRequestHook();

function waitForSelector(selector, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const el = $(selector);
    if (el) return resolve(el);
    const observer = new MutationObserver(() => {
      const el = $(selector);
      if (el) { observer.disconnect(); resolve(el); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); resolve(null); }, timeout);
  });
}

function parseCount(raw) {
  const textValue = String(raw || '').trim().toLowerCase().replace(/,/g, '').replace(/\+/g, '');
  if (!textValue) return 0;

  const match = textValue.match(/(\d+(?:\.\d+)?)(万|w|k)?/i);
  if (!match) return 0;

  let value = parseFloat(match[1]);
  const unit = (match[2] || '').toLowerCase();
  if (unit === '万' || unit === 'w') value *= 10000;
  if (unit === 'k') value *= 1000;
  return Math.round(value);
}

function uniqueStrings(values) {
  const seen = new Set();
  const result = [];
  for (const value of values) {
    const key = String(value || '').trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(key);
  }
  return result;
}

function inferVideoKind(url) {
  const lower = String(url || '').toLowerCase();
  if (!lower) return 'unknown';
  if (lower.startsWith('blob:')) return 'blob';
  if (lower.includes('.mp4')) return 'mp4';
  if (lower.includes('.m3u8')) return 'm3u8';
  if (lower.includes('.mov')) return 'mov';
  if (lower.includes('.m4v')) return 'm4v';
  return 'unknown';
}

function scoreVideoCandidate(url) {
  const lower = String(url || '').toLowerCase();
  if (!lower) return -1;
  if (lower.startsWith('blob:')) return 0;
  if (lower.startsWith('https://') || lower.startsWith('http://')) {
    const kind = inferVideoKind(lower);
    if (kind === 'mp4') return 5;
    if (kind === 'm3u8') return 4;
    if (kind === 'mov' || kind === 'm4v') return 3;
    return 2;
  }
  return 1;
}

function collectVideoCandidates(videoEl) {
  const candidates = [];
  const seen = new Set();

  function push(url, source) {
    if (!url) return;
    let normalized = '';
    try {
      normalized = new URL(url, window.location.href).href;
    } catch {
      normalized = String(url);
    }
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    candidates.push({
      url: normalized,
      source,
      kind: inferVideoKind(normalized),
      score: scoreVideoCandidate(normalized),
    });
  }

  push(videoEl?.src, 'video.src');
  push(videoEl?.currentSrc, 'video.currentSrc');

  for (const sourceEl of $$('video source, source')) {
    push(sourceEl.src || sourceEl.currentSrc || sourceEl.getAttribute('src'), 'source');
  }

  const attrRoots = [
    videoEl,
    videoEl?.parentElement,
    videoEl?.closest('.player-container, .video-player, .xgplayer, [class*="video"]'),
    document.querySelector('.player-container'),
    document.querySelector('.xg-video-container'),
  ].filter(Boolean);

  for (const root of attrRoots) {
    for (const attr of ['src', 'data-src', 'data-url', 'data-playurl', 'data-play-url', 'data-m3u8', 'data-hls']) {
      push(root.getAttribute?.(attr), `attr:${attr}`);
    }
  }

  try {
    const perfEntries = performance.getEntriesByType('resource');
    for (const entry of perfEntries.slice(-200)) {
      const url = entry.name || '';
      if (/(\.mp4|\.m3u8|\.m4v|\.mov)(\?|$)/i.test(url) || /video|vod|hls|playurl|sns-video/i.test(url)) {
        push(url, 'performance');
      }
    }
  } catch {}

  for (const entry of mediaRequestLog.slice(-300)) {
    const url = entry.url || '';
    if (/(\.mp4|\.m3u8|\.m4v|\.mov)(\?|$)/i.test(url) || /video|vod|hls|playurl|sns-video/i.test(url)) {
      push(url, entry.source || 'page_hook');
    }
  }

  try {
    const fullScriptText = [...document.scripts].map(s => s.textContent || '').join('\n');
    const matches = fullScriptText.match(/https?:\/\/[^\s"'\\]+?(?:\.mp4|\.m3u8|\.m4v|\.mov)(?:\?[^\s"'\\]*)?/ig) || [];
    for (const match of matches) push(match, 'script');
  } catch {}

  candidates.sort((a, b) => b.score - a.score);
  return candidates;
}

// ── Platform Adapter Support ──────────────────────────────────

window.FlowLensCommon = {
  $,
  $$,
  text,
  firstText,
  wait,
  watchHighlightElement,
  parseCount,
  uniqueStrings,
  collectVideoCandidates,
};

function requireXhsMethod(method) {
  const adapter = window.FlowLensXhs || {};
  const fn = adapter[method];
  if (typeof fn !== 'function') {
    throw new Error(`XHS adapter method unavailable: ${method}`);
  }
  return fn.bind(adapter);
}

async function scrollPage(pixels = 600) {
  window.scrollBy({ top: pixels, behavior: 'smooth' });
  await wait(1000);
  return { ok: true };
}

// ── Message Handler ────────────────────────────────────────────

// Signal readiness and keep background service worker alive via long-lived port
let keepalivePort = null;
let keepalivePingInterval = null;
let keepaliveReconnectTimer = null;
function connectKeepalive() {
  if (keepaliveReconnectTimer) {
    clearTimeout(keepaliveReconnectTimer);
    keepaliveReconnectTimer = null;
  }
  keepalivePort = chrome.runtime.connect({ name: 'keepalive' });
  keepalivePort.onDisconnect.addListener(() => {
    // Reconnect after brief delay (service worker restarted)
    if (keepaliveReconnectTimer) return;
    keepaliveReconnectTimer = setTimeout(() => {
      keepaliveReconnectTimer = null;
      connectKeepalive();
    }, 1000);
  });
  // Ping every 20s to prevent port timeout
  if (keepalivePingInterval) {
    clearInterval(keepalivePingInterval);
  }
  keepalivePingInterval = setInterval(() => {
    try { keepalivePort.postMessage({ type: 'ping' }); } catch {}
  }, 20000);
}
connectKeepalive();

setTimeout(() => {
  chrome.runtime.sendMessage({ type: 'content_ready', url: window.location.href });
}, 0);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'flowlens_capture_overlay') {
    setWatchOverlayCaptureHidden(!!msg.hidden);
    try { sendResponse({ ok: true }); } catch {}
    return;
  }

  if (msg.type === 'watch_highlight') {
    if (msg.mode === 'coords') {
      const overlay = document.createElement('div');
      overlay.className = 'flowlens-element-highlight';
      overlay.style.cssText = [
        'position: fixed',
        `left: ${(msg.x || 0) - 14}px`,
        `top: ${(msg.y || 0) - 14}px`,
        'width: 28px',
        'height: 28px',
        'border-radius: 999px',
        'border: 2px solid #f97316',
        'background: rgba(249, 115, 22, 0.18)',
        'pointer-events: none',
        'z-index: 2147483646',
        'box-shadow: 0 0 0 8px rgba(249, 115, 22, 0.12)',
        'transition: opacity 0.6s ease-out',
      ].join(';');
      document.documentElement.appendChild(overlay);
      setTimeout(() => { overlay.style.opacity = '0'; }, 1400);
      setTimeout(() => { overlay.remove(); }, 2000);
    }
    return;
  }

  if (msg.type === 'watch_state') {
    setWatchOverlayState(msg);
    return;
  }

  if (msg.type === 'watch_event') {
    appendWatchOverlayEntry(msg.data || {});
    return;
  }

  if (msg.type === 'watch_status') {
    updateWatchOverlayStatus(msg.data || {});
    return;
  }

  if (msg.type !== 'command') return;

  (async () => {
    try {
      let result;
      switch (msg.action) {
        case 'ping':
          result = {
            ok: true,
            url: window.location.href,
            state: window.FlowLensXhs?.detectState?.() || 'unknown',
          };
          break;

        case 'detect_state': {
          const detectState = window.FlowLensXhs?.detectState;
          const detectAntiBotState = window.FlowLensXhs?.detectAntiBotState;
          const detectNoteType = window.FlowLensXhs?.detectNoteType;
          result = {
            state: detectState ? detectState() : 'unknown',
            antiBotState: detectAntiBotState ? detectAntiBotState() : '',
            url: window.location.href,
            noteType: detectNoteType ? detectNoteType() : '',
          };
          break;
        }

        case 'extract_search_cards':
          result = { cards: requireXhsMethod('extractSearchCards')() };
          break;

        case 'extract_search_tabs':
          result = { tabs: requireXhsMethod('extractSearchTabs')() };
          break;

        case 'get_search_page_state':
          result = requireXhsMethod('detectSearchPageState')();
          break;

        case 'click_search_tab':
          result = await requireXhsMethod('clickSearchTab')(msg.params?.label ?? '全部');
          break;

        case 'submit_search_query':
          result = await requireXhsMethod('submitSearchQuery')(msg.params?.keyword ?? '');
          break;

        case 'extract_note_content':
          result = await requireXhsMethod('extractNoteContentCommand')(msg.params || {});
          break;

        case 'collect_carousel_images':
          result = await requireXhsMethod('collectAllCarouselImages')(msg.params?.max_images ?? 20);
          break;

        case 'extract_comments':
          result = { comments: requireXhsMethod('extractComments')(msg.params || {}) };
          break;

        case 'click_card':
          result = await requireXhsMethod('clickNoteCard')(msg.params?.index ?? 0);
          break;

        case 'click_note_link':
          result = await requireXhsMethod('clickNoteByLink')(msg.params?.url ?? '');
          break;

        case 'click_note_by_id':
          result = await requireXhsMethod('clickNoteById')(msg.params?.note_id ?? '');
          break;

        case 'close_note':
          result = await requireXhsMethod('closeNoteDetail')();
          break;

        case 'scroll_note':
          result = await requireXhsMethod('scrollInNote')(msg.params?.pixels ?? 400);
          break;

        case 'scroll_page':
          result = await scrollPage(msg.params?.pixels ?? 600);
          break;

        case 'extract_profile_info':
          result = { profile: requireXhsMethod('extractProfileInfo')() };
          break;

        case 'extract_profile_notes':
          result = { notes: requireXhsMethod('extractProfileNotes')() };
          break;

        case 'run_js': {
          try {
            const fn = new Function(msg.params?.code || '');
            result = { value: fn() };
          } catch (e) {
            result = { error: e.message };
          }
          break;
        }

        case 'capture_visible_dom': {
          // Fallback: capture visible area via scrolling canvas
          try {
            const { innerWidth: w, innerHeight: h } = window;
            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            // Note: this won't render the actual page, it's a placeholder
            // Real content capture requires html2canvas or similar
            result = { error: 'DOM canvas capture not implemented — use CDP screenshot' };
          } catch (e) {
            result = { error: e.message };
          }
          break;
        }

        default:
          result = { error: `Unknown action: ${msg.action}` };
      }
      sendResponse(result);
    } catch (err) {
      sendResponse({ error: err.message });
    }
  })();
  return true;
});

console.log('[FlowLens] Content script loaded:', window.location.href);
