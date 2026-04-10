/**
 * XHS Research Agent — Content Script (v2)
 *
 * Runs inside xiaohongshu.com pages. Handles DOM extraction and browser actions.
 * Communicates with background.js which relays to the external Python agent.
 *
 * Improvements over v1:
 * - Better video note handling (separate selectors)
 * - Comment deduplication built-in
 * - More robust card extraction with multiple fallback selectors
 * - Scroll within note detail panel (not page scroll)
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

const WATCH_OVERLAY_MAX_ENTRIES = 10;
let watchOverlayHost = null;
let watchOverlayShadow = null;
let watchOverlayFeed = null;
let watchOverlayDot = null;
let watchOverlayStatusText = null;
let watchOverlayCount = null;
let watchOverlayEntries = [];
let watchOverlayStartTime = Date.now();

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
      background: rgba(14, 17, 24, 0.92);
      backdrop-filter: blur(16px);
      color: #e5e7eb;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
    }
    .header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.14);
      background: linear-gradient(180deg, rgba(30, 41, 59, 0.65), rgba(15, 23, 42, 0.35));
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
      color: #94a3b8;
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
    .count {
      font-size: 10px;
      color: #64748b;
      flex-shrink: 0;
    }
    .toggle {
      border: 0;
      background: rgba(30, 41, 59, 0.8);
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
      background: rgba(15, 23, 42, 0.6);
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
        <div class="title">FlowLens Live</div>
        <div class="subtitle" id="cvWatchStatus">Waiting for agent activity...</div>
      </div>
      <span class="count" id="cvWatchCount">0 events</span>
      <button class="toggle" id="cvWatchToggle" title="Collapse">−</button>
    </div>
    <div class="feed" id="cvWatchFeed">
      <div class="empty">Waiting for agent activity...</div>
    </div>
  `;
  watchOverlayShadow.appendChild(panel);

  watchOverlayFeed = watchOverlayShadow.getElementById('cvWatchFeed');
  watchOverlayDot = watchOverlayShadow.getElementById('cvWatchDot');
  watchOverlayStatusText = watchOverlayShadow.getElementById('cvWatchStatus');
  watchOverlayCount = watchOverlayShadow.getElementById('cvWatchCount');

  const toggle = watchOverlayShadow.getElementById('cvWatchToggle');
  toggle.addEventListener('click', () => {
    const hidden = watchOverlayFeed.classList.toggle('hidden');
    toggle.textContent = hidden ? '+' : '−';
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

function renderWatchOverlay() {
  ensureWatchOverlay();
  watchOverlayCount.textContent = `${watchOverlayEntries.length} events`;
  if (!watchOverlayEntries.length) {
    watchOverlayFeed.innerHTML = '<div class="empty">Waiting for agent activity...</div>';
    return;
  }

  watchOverlayFeed.innerHTML = watchOverlayEntries.map((entry) => {
    const kind = String(entry.kind || 'info').toUpperCase();
    const action = String(entry.action || entry.phase || 'update');
    const message = watchOverlaySummary(entry);
    return `
      <div class="entry">
        <div class="entry-head">
          <span class="entry-kind">${kind}</span>
          <span class="entry-time">${watchOverlayTime(entry.timestamp)}</span>
        </div>
        <div class="entry-action">${action}</div>
        <div class="entry-message">${message || 'No details yet.'}</div>
      </div>
    `;
  }).join('');
  watchOverlayFeed.scrollTop = watchOverlayFeed.scrollHeight;
}

function updateWatchOverlayStatus(status) {
  ensureWatchOverlay();
  const active = !!status?.watchMode;
  watchOverlayDot.classList.toggle('on', active);
  watchOverlayStatusText.textContent = active
    ? `Watching tab ${status?.pinnedTabId || status?.activeTabId || ''}`.trim()
    : 'Watch mode idle';
  if (active) {
    showWatchOverlay();
  } else if (!watchOverlayEntries.length) {
    hideWatchOverlay();
  }
}

function setWatchOverlayState(payload) {
  watchOverlayStartTime = payload?.startTime || Date.now();
  watchOverlayEntries = Array.isArray(payload?.entries)
    ? payload.entries.slice(-WATCH_OVERLAY_MAX_ENTRIES)
    : [];
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
  watchOverlayEntries.push(entry);
  if (watchOverlayEntries.length > WATCH_OVERLAY_MAX_ENTRIES) {
    watchOverlayEntries = watchOverlayEntries.slice(-WATCH_OVERLAY_MAX_ENTRIES);
  }
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

function extractNoteIdFromUrl(url) {
  const value = String(url || '');
  const match = value.match(/\/(?:explore|search_result|discovery)\/([^/?#]+)/i);
  return match ? match[1] : '';
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

// ── State Detection ────────────────────────────────────────────

function detectAntiBotState() {
  const url = window.location.href;
  const pageText = document.body ? document.body.innerText : '';

  if (url.includes('/404') || url.includes('error_code=')) return 'error_page';
  if (/请扫码在手机上查看|扫码在手机上查看|在手机上查看/.test(pageText)) return 'mobile_only_gate';
  if (/security verification|安全验证|请完成验证|请进行验证|滑块验证|验证码|拖动滑块/.test(pageText)) {
    return 'security_verification';
  }
  return '';
}

function detectState() {
  const url = window.location.href;
  const antiBotState = detectAntiBotState();

  if (antiBotState) return antiBotState;

  // Check for note detail overlay first (can appear on any page)
  const overlay = document.querySelector(
    '.note-detail-mask, .note-overlay, #noteContainer, .note-detail-modal'
  );
  if (overlay && overlay.offsetHeight > 0) return 'note_detail';

  const searchInput = findVisibleSearchInput();
  const searchTabs = extractSearchTabs();
  const hasSearchTabs = ['全部', '图文', '视频', '用户'].every((label) =>
    searchTabs.some((tab) => tab.label === label)
  );
  if (searchInput && hasSearchTabs) return 'search_results';

  // URL-based detection (only match actual explore URLs, not redirect params)
  if (extractNoteIdFromUrl(url)) return 'note_detail';
  if (url.includes('/search_result') || url.includes('keyword=')) return 'search_results';
  if (url.includes('/user/profile/')) return 'profile_page';
  if (url.match(/xiaohongshu\.com\/?$/) || url.endsWith('/explore')) return 'homepage';
  return 'unknown';
}

function detectNoteType() {
  if (document.querySelector('video, .player-container, .video-player, .xg-video-container')) {
    return 'video';
  }
  return 'image';
}

// ── Search Card Extraction ─────────────────────────────────────

function extractSearchCards() {
  // Try multiple container selectors
  let cards = $$('section.note-item');
  if (!cards.length) cards = $$('[data-note-id]');
  if (!cards.length) cards = $$('.feeds-page .note-item');

  return cards.map((card, i) => {
    const titleEl = card.querySelector('.title, .note-title, a.title span');
    const authorEl = card.querySelector('.author-wrapper .name, .author .name, .nick-name');
    const likesEl = card.querySelector('.like-wrapper .count, .engagement .like .count, .count');
    const imgEl = card.querySelector('.cover img, .note-cover img, img');
    const linkEl = card.querySelector('a[href*="/explore/"], a[href*="/search_result/"]')
                   || card.closest('a')
                   || card.querySelector('a');

    const link = linkEl ? linkEl.href : '';
    const noteId = card.dataset?.noteId
      || extractNoteIdFromUrl(link)
      || '';
    const hasVideo = !!card.querySelector(
      '.play-icon, .video-icon, svg[class*="video"], .duration'
    );

    return {
      position: i,
      title: text(titleEl),
      author: text(authorEl),
      likes: text(likesEl),
      cover_url: imgEl ? (imgEl.src || imgEl.dataset?.src || '') : '',
      link,
      note_id: noteId,
      type: hasVideo ? 'video' : 'image',
    };
  }).filter(c => c.title || c.link);
}

function extractSearchTabs() {
  const labels = ['全部', '图文', '视频', '用户'];
  const candidates = $$('button, a, div, span').filter((el) => {
    const label = text(el);
    if (!labels.includes(label)) return false;
    if (!(el instanceof HTMLElement)) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 24 && rect.height > 18;
  });

  const seen = new Set();
  const tabs = [];
  for (const el of candidates) {
    const label = text(el);
    if (seen.has(label)) continue;
    seen.add(label);
    const node = el;
    const ariaSelected = node.getAttribute('aria-selected') === 'true';
    const className = node.className || '';
    const isActive =
      ariaSelected
      || /\bactive\b|current|selected/.test(className)
      || node.getAttribute('data-active') === 'true';
    tabs.push({
      label,
      active: isActive,
    });
  }
  return tabs;
}

function detectSearchPageState() {
  const cards = extractSearchCards();
  const tabs = extractSearchTabs();
  const activeTab = tabs.find(tab => tab.active)?.label || '';
  const pageState = detectState();
  const input = findVisibleSearchInput();
  const noResultText = firstText([
    '.empty-result-page',
    '.empty-page',
    '.no-result',
    '.empty',
    '[class*="empty"]',
  ]);
  const pageText = document.body ? document.body.innerText : '';
  const hasNoResults =
    /没有找到相关内容|换个词试试|暂无相关内容|暂无结果/.test(noResultText || pageText);
  const skeletonCount = $$(
    '[class*="skeleton"], [class*="Skeleton"], [class*="loading"], [class*="shimmer"]'
  ).length;
  let urlKeyword = '';
  try {
    const current = new URL(window.location.href);
    urlKeyword = decodeURIComponent(current.searchParams.get('keyword') || '').trim();
  } catch {}

  return {
    page_state: pageState,
    card_count: cards.length,
    tabs,
    active_filter: activeTab,
    input_keyword: input && typeof input.value === 'string' ? input.value.trim() : '',
    url_keyword: urlKeyword,
    has_no_results: hasNoResults,
    loading: !cards.length && !hasNoResults,
    skeleton_count: skeletonCount,
  };
}

async function clickSearchTab(label) {
  const labels = ['全部', '图文', '视频', '用户'];
  if (!labels.includes(label)) {
    return { ok: false, error: `Unsupported search tab: ${label}` };
  }

  const candidates = $$('button, a, div, span').filter((el) => text(el) === label);
  for (const el of candidates) {
    if (!(el instanceof HTMLElement)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 24 || rect.height < 18) continue;
    watchHighlightElement(el);
    el.click();
    await wait(1500);
    return {
      ok: true,
      label,
      state: detectSearchPageState(),
    };
  }
  return { ok: false, error: `Search tab not found: ${label}` };
}

function findVisibleSearchInput() {
  const candidates = $$(
    'input#search-input, input[type="search"], input[placeholder*="搜索"], input[placeholder*="探索"], .search-input input, .search-container input'
  ).filter((el) => {
    if (!(el instanceof HTMLElement)) return false;
    const rect = el.getBoundingClientRect();
    return rect.width >= 120 && rect.height >= 24 && rect.bottom > 0 && rect.right > 0;
  });
  return candidates[0] || null;
}

function setNativeInputValue(input, value) {
  const proto = input instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
  if (descriptor && descriptor.set) descriptor.set.call(input, value);
  else input.value = value;
}

async function submitSearchQuery(keyword) {
  function hasSearchTransition(targetKeyword) {
    const state = detectSearchPageState();
    const normalizedKeyword = String(targetKeyword || '').trim().toLowerCase();
    const visibleKeyword = String(state.input_keyword || '').trim().toLowerCase();
    const urlKeyword = String(state.url_keyword || '').trim().toLowerCase();
    const visibleMatches = !normalizedKeyword || visibleKeyword === normalizedKeyword;
    const urlMatches = !normalizedKeyword || !urlKeyword || urlKeyword === normalizedKeyword;
    const isSearchResults = state.page_state === 'search_results';
    const hasSearchSurface = state.tabs.length > 0 || state.has_no_results || state.card_count > 0;
    return {
      ok: isSearchResults && visibleMatches && urlMatches && hasSearchSurface && !state.loading,
      state,
      url: window.location.href,
    };
  }

  async function waitForSearchTransition(targetKeyword, timeoutMs = 4000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const result = hasSearchTransition(targetKeyword);
      if (result.ok) return result;
      await wait(120);
    }
    return hasSearchTransition(targetKeyword);
  }

  function dispatchEnter(target) {
    const enterPayload = {
      key: 'Enter',
      code: 'Enter',
      keyCode: 13,
      which: 13,
      charCode: 13,
      bubbles: true,
      cancelable: true,
      composed: true,
    };
    target.dispatchEvent(new KeyboardEvent('keydown', enterPayload));
    target.dispatchEvent(new KeyboardEvent('keypress', enterPayload));
    target.dispatchEvent(new KeyboardEvent('keyup', enterPayload));
  }

  function triggerClick(target) {
    if (!target) return;
    const rect = target.getBoundingClientRect();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      clientX: Math.round(rect.left + rect.width / 2),
      clientY: Math.round(rect.top + rect.height / 2),
    };
    try { target.dispatchEvent(new PointerEvent('pointerdown', eventInit)); } catch {}
    try { target.dispatchEvent(new MouseEvent('mousedown', eventInit)); } catch {}
    try { target.dispatchEvent(new PointerEvent('pointerup', eventInit)); } catch {}
    try { target.dispatchEvent(new MouseEvent('mouseup', eventInit)); } catch {}
    try { target.dispatchEvent(new MouseEvent('click', eventInit)); } catch {}
    if (typeof target.click === 'function') target.click();
  }

  const input = findVisibleSearchInput();
  if (!input) {
    return { ok: false, error: 'Search input not found' };
  }

  input.scrollIntoView({ behavior: 'instant', block: 'center' });
  await wait(200);
  input.focus();
  watchHighlightElement(input);

  if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) {
    setNativeInputValue(input, keyword);
  } else if (input.isContentEditable) {
    input.textContent = keyword;
  } else {
    return { ok: false, error: 'Unsupported search input element' };
  }

  input.dispatchEvent(new InputEvent('input', {
    bubbles: true,
    inputType: 'insertText',
    data: keyword,
  }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  await wait(120);

  const root = input.closest('form, header, .search-input, .search-container, .search-bar, .search-box') || document;
  const inputRect = input.getBoundingClientRect();
  const inputCenterY = inputRect.top + inputRect.height / 2;
  const rawSubmitCandidates = [
    ...root.querySelectorAll('button, [role="button"], a, div, span, svg, .search-icon, .search-btn, .icon-search'),
    ...document.querySelectorAll('button, [role="button"], a, div, span, svg, .search-icon, .search-btn, .icon-search'),
  ];
  const submitCandidates = [...new Set(rawSubmitCandidates)]
    .filter((el) => el instanceof HTMLElement || el instanceof SVGElement)
    .map((el) => {
      const clickable = el.closest?.('button, [role="button"], a, div, span') || el;
      const rect = clickable.getBoundingClientRect();
      const meta = [
        clickable.getAttribute?.('aria-label') || '',
        clickable.getAttribute?.('title') || '',
        clickable.className || '',
        el.getAttribute?.('aria-label') || '',
        el.getAttribute?.('title') || '',
        el.className || '',
      ].join(' ').toLowerCase();
      let score = 0;
      if (/search|搜索|find|query/.test(meta)) score += 100;
      if (/clear|close|cancel|remove|delete|清除|关闭|取消/.test(meta)) score -= 120;
      const centerY = rect.top + rect.height / 2;
      score -= Math.abs(rect.left - inputRect.right);
      score -= Math.abs(centerY - inputCenterY) * 0.6;
      if (rect.left >= inputRect.right - 8) score += 18;
      if (rect.left < inputRect.left - 24) score -= 60;
      if (root.contains(clickable)) score += 18;
      if (rect.left >= inputRect.left && rect.right <= inputRect.right) score -= 20;
      return { el: clickable, rect, score, meta };
    })
    .filter(({ rect, score }) => rect.width >= 12 && rect.height >= 12 && rect.right >= inputRect.left && rect.left <= inputRect.right + 180 && score > -140)
    .sort((a, b) => b.score - a.score);

  const target = submitCandidates[0]?.el || null;
  if (target) {
    watchHighlightElement(target);
    triggerClick(target);
    const clicked = await waitForSearchTransition(keyword, 1800);
    if (clicked.ok) {
      return {
        ok: true,
        keyword,
        strategy: 'click_search_target',
        state: clicked.state.page_state,
        searchState: clicked.state,
        url: clicked.url,
      };
    }
  }

  if (input.form && typeof input.form.requestSubmit === 'function') {
    input.form.requestSubmit();
    const submitted = await waitForSearchTransition(keyword, 1800);
    if (submitted.ok) {
      return {
        ok: true,
        keyword,
        strategy: 'form_request_submit',
        state: submitted.state.page_state,
        searchState: submitted.state,
        url: submitted.url,
      };
    }
  }

  dispatchEnter(input);
  dispatchEnter(document);
  const entered = await waitForSearchTransition(keyword, 2200);
  return {
    ok: entered.ok,
    keyword,
    strategy: entered.ok ? 'synthetic_enter' : 'submit_failed',
    error: entered.ok ? '' : 'Search submit did not transition to search_results',
    state: entered.state.page_state,
    searchState: entered.state,
    url: entered.url,
  };
}

// ── Note Content Extraction ────────────────────────────────────

function getVisibleNoteOverlay() {
  const overlay = document.querySelector('.note-detail-mask, .note-overlay, .note-detail-modal, #noteContainer');
  if (overlay && overlay.offsetHeight > 0) return overlay;
  return null;
}

async function waitForVisibleNoteOverlay(timeoutMs = 1200) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const overlay = getVisibleNoteOverlay();
    if (overlay) return overlay;
    await wait(120);
  }
  return getVisibleNoteOverlay();
}

async function waitForNoteContent(timeout = 8000) {
  /** Wait for text or media elements to appear in DOM (XHS loads async). */
  const textSelectors = [
    '#detail-title', '.note-content .title', '.note-scroller .title',
    '#detail-desc', '.note-content .desc', '.note-scroller .desc',
  ];
  const mediaSelectors = [
    'video',
    '.player-container',
    '.video-player',
    '.xg-video-container',
    '.carousel-image img',
    '.slide img',
    '.swiper-slide img',
  ];
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const sel of textSelectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) return el;
    }
    for (const sel of mediaSelectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    await wait(500);
  }
  return null;
}

function extractNoteContent() {
  const note = {};
  note.type = detectNoteType();
  note.url = window.location.href;
  note.note_id =
    extractNoteIdFromUrl(window.location.href)
    || document.querySelector('[data-note-id]')?.dataset?.noteId
    || '';

  // Title — multiple fallbacks for image-text vs video notes
  note.title = firstText([
    '#detail-title',
    '.note-content .title',
    '.note-scroller .title',
    '.note-detail .title',
    'h1',
  ]);

  // Author
  note.author = firstText([
    '.author-container .username',
    '.author-wrapper .username',
    '.info .username',
    '.user-name',
  ]);

  // Content — different containers for different note types
  note.content = firstText([
    '#detail-desc .note-text',
    '#detail-desc',
    '.note-content .desc',
    '.note-scroller .desc',
    '.note-scroller .content',
    '.note-text .content',
    '.note-detail .desc',
  ]);

  // If content has nested spans/elements, get the full text
  if (!note.content) {
    const descEl = document.querySelector('#detail-desc, .note-content .desc');
    if (descEl) note.content = descEl.innerText.trim();
  }

  // Date
  note.date = firstText([
    '.note-content .date',
    '.bottom-container .date',
    '.note-scroller .date',
    '.date',
  ]);

  // Engagement metrics
  note.likes = firstText([
    '.like-wrapper .count',
    '.engage-bar .like .count',
    '[data-type="like"] .count',
    '.engage-bar-style .like-wrapper .count',
  ]);

  note.favorites = firstText([
    '.collect-wrapper .count',
    '.engage-bar .collect .count',
    '[data-type="collect"] .count',
  ]);

  note.comments_count = firstText([
    '.chat-wrapper .count',
    '.engage-bar .chat .count',
    '[data-type="chat"] .count',
  ]);

  note.shares = firstText([
    '.share-wrapper .count',
    '.engage-bar .share .count',
  ]);

  // Author profile link
  const authorLink = document.querySelector(
    '.author-container a[href*="/user/"], .info a[href*="/user/profile/"]'
  );
  note.author_url = authorLink ? authorLink.href : '';
  note.ip_location = firstText([
    '.note-content .ip-location',
    '.publish-info .ip-location',
    '.ip-location',
    '.note-ip-location',
  ]);
  note.location = firstText([
    '.note-content .location',
    '.publish-info .location',
    '.location-info',
    '.note-location',
  ]);

  // Hashtags
  note.hashtags = $$('.hash-tag a, a[href*="/page/topics/"], .note-content .tag, #detail-desc a.tag')
    .map(el => text(el))
    .filter(Boolean);

  // Images — different for image-text vs video notes
  if (note.type === 'image') {
    const imgs = $$(
      '.carousel-image img, .slide img, .swiper-slide img, ' +
      '.note-slider img, .note-detail img.note-image'
    );
    note.image_urls = imgs.map(img => img.src || img.dataset?.src || '').filter(Boolean);

    // Image indicator (e.g. "3/7")
    const indicator = document.querySelector(
      '.indicator, .carousel-indicator, .slide-indicator, .image-index'
    );
    if (indicator) {
      const m = text(indicator).match(/(\d+)\s*[/／]\s*(\d+)/);
      if (m) note.total_images = parseInt(m[2]);
    }
  } else {
    // Video note — get video poster/thumbnail
    const video = document.querySelector('video');
    const videoCandidates = collectVideoCandidates(video);
    const preferred = videoCandidates.find(c => c.kind !== 'blob') || videoCandidates[0] || null;

    if (video && video.poster) note.image_urls = [video.poster];
    else note.image_urls = [];
    note.poster_url = video ? (video.poster || '') : '';
    note.video_url = preferred ? preferred.url : (video ? (video.src || video.currentSrc || '') : '');
    note.video_url_candidates = videoCandidates;
    note.duration_s = video && Number.isFinite(video.duration) ? Math.round(video.duration * 10) / 10 : null;
  }

  note.image_count = note.total_images || note.image_urls.length || 1;

  return note;
}

// ── Comment Extraction (with dedup) ────────────────────────────

function extractComments(options = {}) {
  const rootSelectors = [
    '.comment-item',
    '.parent-comment',
    '.comment-inner',
    '.comments-container .comment-item-inner',
    '.comment-wrapper',
  ].join(', ');
  const childSelectors = [
    '.reply-item',
    '.sub-comment-item',
    '.child-comment-item',
    '.reply-comment-item',
  ].join(', ');

  function parseComment(item, includeChildren = true) {
    const username = firstText(['.name', '.user-name', '.nickname', '.author-name'], item);
    const commentText = firstText(
      ['.content', '.comment-text', '.note-text', '.desc', '[class*="content"]'],
      item,
    );
    const likes = firstText(
      ['.like .count', '.like-wrapper .count', '.interact-wrapper .count', '[class*="like"] .count'],
      item,
    );
    const time = firstText(
      ['.time', '.date', '.create-time', '.comment-time', '[class*="time"]'],
      item,
    );
    const badgeText = firstText(
      ['.author-tag', '.tag.author', '.reply-tag', '.user-tag', '[class*="author-tag"]'],
      item,
    );
    const topText = firstText(['.top-tag', '.pinned-tag', '[class*="top-tag"]'], item);
    const isAuthorReply = /作者|博主|楼主/.test(badgeText);
    const isPinned = /置顶/.test(topText);

    const childComments = [];
    if (includeChildren) {
      const childNodes = $$(childSelectors, item).filter(sub => !sub.parentElement?.closest(childSelectors));
      for (const child of childNodes) {
        const parsed = parseComment(child, false);
        if (parsed.text) childComments.push(parsed);
      }
    }

    const likeCount = parseCount(likes);
    const replyCount = childComments.length;
    const heatScore = likeCount + replyCount * 3 + (isAuthorReply ? 5 : 0) + (isPinned ? 10 : 0);

    return {
      username,
      text: commentText,
      likes,
      like_count: likeCount,
      time,
      is_author_reply: isAuthorReply,
      is_pinned: isPinned,
      badge: badgeText,
      reply_count: replyCount,
      heat_score: heatScore,
      sub_comments: childComments,
    };
  }

  const items = $$(rootSelectors).filter(item => !item.parentElement?.closest(rootSelectors));
  const seen = new Set();
  let comments = [];

  for (const item of items) {
    const parsed = parseComment(item, true);
    if (!parsed.text) continue;

    const key = `${parsed.username}:${parsed.text.slice(0, 30)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    comments.push(parsed);
  }

  if (options.prefer_hot !== false) {
    comments.sort((a, b) => (b.heat_score || 0) - (a.heat_score || 0));
  }

  if (options.max_comments) {
    comments = comments.slice(0, options.max_comments);
  }

  return comments;
}

// ── Profile Page Extraction ───────────────────────────────────

function extractProfileInfo() {
  const profile = {};

  // Username
  profile.name = firstText([
    '.user-nickname', '.user-name', '.info .nickname',
    'h1.user-name', '.name-detail .name',
  ]);

  // XHS ID — from .user-content or pattern match
  const idContainer = document.querySelector('.user-content, .user-redId, .xhs-id');
  if (idContainer) {
    const m = idContainer.textContent.match(/小红书号[：:]\s*(\S+)/);
    if (m) profile.xhs_id = m[1];
  }
  if (!profile.xhs_id) {
    // Broader search
    const allText = document.querySelector('.user-info, .basic-info');
    if (allText) {
      const m = allText.textContent.match(/小红书号[：:]\s*(\S+)/);
      if (m) profile.xhs_id = m[1];
    }
  }

  // Bio
  profile.bio = firstText([
    '.user-desc', '.bio', '.desc-text', '.info .desc',
  ]);

  // Avatar
  const avatarEl = document.querySelector(
    '.user-avatar img, .avatar-wrapper img, .info-part img, .avatar img'
  );
  profile.avatar_url = avatarEl ? (avatarEl.src || '') : '';

  // Stats: use .data-info structure (count + shows pairs)
  const statContainers = $$('.data-info > div, .user-interactions > div, .data-count > div');
  for (const container of statContainers) {
    const countEl = container.querySelector('.count');
    const labelEl = container.querySelector('.shows, .label');
    if (!countEl || !labelEl) continue;
    const value = text(countEl);
    const label = text(labelEl);
    if (label.includes('关注')) profile.following = value;
    else if (label.includes('粉丝')) profile.followers = value;
    else if (label.includes('赞') || label.includes('收藏')) profile.total_likes = value;
  }

  // Fallback: get all .count elements within .data-info
  if (!profile.followers) {
    const counts = $$('.data-info .count, .user-interactions .count');
    if (counts.length >= 3) {
      profile.following = text(counts[0]);
      profile.followers = text(counts[1]);
      profile.total_likes = text(counts[2]);
    }
  }

  // Verification
  const verifyEl = document.querySelector('.verify-icon, .badge-icon, .verified');
  profile.verified = !!verifyEl;
  profile.verify_text = firstText(['.verify-name', '.badge-name', '.verified-text']);

  // IP location
  profile.location = firstText(['.ip-container', '.user-IP', '.ip-text']);

  // Tags / labels
  profile.tags = $$('.user-tag, .tag-item, .info-tag').map(el => text(el)).filter(Boolean);

  return profile;
}

function extractProfileNotes() {
  // Profile page note grid
  let cards = $$('section.note-item');
  if (!cards.length) cards = $$('[data-note-id]');
  if (!cards.length) cards = $$('.feeds-page .note-item, .feeds-container .note-item');

  return cards.map((card, i) => {
    const titleEl = card.querySelector('.title, .note-title, a.title span');
    const likesEl = card.querySelector('.like-wrapper .count, .count');
    const imgEl = card.querySelector('.cover img, .note-cover img, img');
    const linkEl = card.querySelector('a[href*="/explore/"], a[href*="/discovery/"]')
                   || card.closest('a')
                   || card.querySelector('a');

    const link = linkEl ? linkEl.href : '';
    const noteId = card.dataset?.noteId
      || extractNoteIdFromUrl(link)
      || '';

    // Detect video indicator
    const hasVideo = !!card.querySelector(
      '.play-icon, .video-icon, svg[class*="video"], .duration'
    );

    return {
      position: i,
      title: text(titleEl),
      likes: text(likesEl),
      cover_url: imgEl ? (imgEl.src || imgEl.dataset?.src || '') : '',
      link,
      note_id: noteId,
      type: hasVideo ? 'video' : 'image',
    };
  }).filter(c => c.link || c.title);
}

// ── Carousel Image Collection ─────────────────────────────────

async function collectAllCarouselImages(maxImages = 20) {
  /**
   * Flip through all carousel images using arrow keys, collecting every
   * unique image URL. XHS lazy-loads carousel images — only the current
   * slide and ±1 neighbors have real `src` attributes.
   *
   * Strategy: use multiple selector strategies to find carousel images,
   * from specific (known XHS classes) to broad (any img in note overlay).
   *
   * Returns { ok, image_urls: string[], total: number, debug: object }
   */

  // Find the note overlay container to scope image search
  // This prevents picking up search result thumbnails behind the modal
  const noteOverlay = document.querySelector(
    '.note-detail-mask, .note-overlay, #noteContainer, .note-detail-modal'
  );
  const searchRoot = noteOverlay || document;

  // Image selectors scoped to the note overlay
  const selectorStrategies = [
    '.carousel-image img, .slide img, .swiper-slide img',
    '.note-slider img, .note-image',
    '.media-container img, .note-scroller img',
    'img',  // Broadest: any img within the scoped container
  ];

  const seenUrls = new Set();
  const orderedUrls = [];
  let matchedStrategy = '';

  function collectVisible() {
    for (const sel of selectorStrategies) {
      const imgs = [...searchRoot.querySelectorAll(sel)];
      for (const img of imgs) {
        const src = img.src || img.dataset?.src || '';
        // Filter: must be from XHS CDN, reasonably sized, not data URI
        if (src && !seenUrls.has(src) && !src.startsWith('data:') &&
            src.includes('xhscdn.com') && img.naturalWidth > 100) {
          seenUrls.add(src);
          orderedUrls.push(src);
          if (!matchedStrategy) matchedStrategy = sel;
        }
      }
      // Stop at first strategy that finds images
      if (orderedUrls.length > 0) break;
    }
  }

  collectVisible();

  // Read total from indicator (e.g. "2/7") — try multiple selectors
  const indicatorSelectors = [
    '.indicator', '.carousel-indicator', '.slide-indicator', '.image-index',
    // XHS specific
    '.note-scroller .index', '.media-container .index',
    '[class*="indicator"]', '[class*="index"]',
  ];
  let total = orderedUrls.length;
  for (const sel of indicatorSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      const m = text(el).match(/(\d+)\s*[/／]\s*(\d+)/);
      if (m) { total = parseInt(m[2]); break; }
    }
  }

  // Build debug info for diagnosing selector issues
  const debug = {
    found: orderedUrls.length,
    total,
    matchedStrategy,
    allImgCount: $$('img').length,
    xhsImgCount: $$('img').filter(i => (i.src || '').includes('xhscdn.com')).length,
  };

  // If only 1 image or we already have all, return early
  if (total <= 1 || orderedUrls.length >= total) {
    return { ok: true, image_urls: orderedUrls, total, debug };
  }

  // Find the carousel container to dispatch arrow key events
  // Try multiple container selectors
  const containerSelectors = [
    '.carousel', '.swiper', '.note-slider', '.slide-list',
    '.note-scroller', '.media-container', '.note-detail',
    '.note-detail-mask', '#noteContainer', '.note-overlay',
    '[class*="carousel"]', '[class*="slider"]', '[class*="swiper"]',
  ];
  let carousel = null;
  for (const sel of containerSelectors) {
    carousel = document.querySelector(sel);
    if (carousel) { debug.carouselContainer = sel; break; }
  }
  if (!carousel) {
    carousel = document.body;
    debug.carouselContainer = 'document.body (fallback)';
  }

  // Navigate forward through all slides
  let staleCount = 0;
  for (let i = 0; i < maxImages; i++) {
    const prevCount = seenUrls.size;

    // Dispatch ArrowRight key on the carousel container
    carousel.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'ArrowRight', code: 'ArrowRight', keyCode: 39,
      bubbles: true, cancelable: true,
    }));
    // Also dispatch on document in case carousel doesn't capture it
    document.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'ArrowRight', code: 'ArrowRight', keyCode: 39,
      bubbles: true, cancelable: true,
    }));

    await wait(400); // Wait for slide transition + lazy load
    collectVisible();

    if (seenUrls.size === prevCount) {
      staleCount++;
      if (staleCount >= 3) break; // No new images after 3 attempts
    } else {
      staleCount = 0;
    }

    if (orderedUrls.length >= total) break;
  }

  debug.finalCount = orderedUrls.length;
  return { ok: true, image_urls: orderedUrls, total, debug };
}

// ── Actions ────────────────────────────────────────────────────

async function clickNoteCard(index) {
  const cards = $$('section.note-item, [data-note-id]');
  if (index >= cards.length) return { ok: false, error: `Card index ${index} out of range (${cards.length} cards)` };

  const card = cards[index];

  // Click the card's cover image or container — NOT the <a> tag directly.
  // XHS's React handler intercepts clicks on the card to open a modal overlay.
  // Clicking the <a> tag directly causes full navigation which XHS blocks (404).
  const clickTarget = card.querySelector('.cover, .cover-ld, img, .note-cover')
                      || card;
  watchHighlightElement(clickTarget);
  clickTarget.click();
  const overlay = await waitForVisibleNoteOverlay(1200);

  // Check if modal opened
  if (overlay && overlay.offsetHeight > 0) {
    return { ok: true, method: 'overlay' };
  }

  // If no overlay, try clicking the card itself
  card.click();
  const retryOverlay = await waitForVisibleNoteOverlay(1200);
  if (retryOverlay && retryOverlay.offsetHeight > 0) {
    return { ok: true, method: 'card_click' };
  }
  return { ok: false, error: 'Note overlay did not open after clicking card' };
}

async function clickNoteByLink(url) {
  if (!url) {
    return { ok: false, error: 'Missing note URL' };
  }

  const links = $$('a[href]').filter((link) => link.href === url || link.href.includes(url));
  if (links.length > 0) {
    const card = links[0].closest('section.note-item, [data-note-id]');
    if (card) {
      card.scrollIntoView({ behavior: 'instant', block: 'center' });
      await wait(500);
      const clickTarget = card.querySelector('.cover, .cover-ld, img, .note-cover') || card;
      watchHighlightElement(clickTarget);
      clickTarget.click();
      let overlay = await waitForVisibleNoteOverlay(1200);

      if (overlay && overlay.offsetHeight > 0) {
        return { ok: true, method: 'overlay' };
      }
      card.click();
      overlay = await waitForVisibleNoteOverlay(1200);
      if (overlay && overlay.offsetHeight > 0) {
        return { ok: true, method: 'card_click' };
      }
      return { ok: false, error: `Overlay did not open for url: ${url}` };
    }

    watchHighlightElement(links[0]);
    links[0].click();
    const overlay = await waitForVisibleNoteOverlay(1200);
    if (overlay && overlay.offsetHeight > 0) {
      return { ok: true, method: 'link_click' };
    }
    return { ok: false, error: `Overlay did not open for url: ${url}` };
  }
  return { ok: false, error: `No clickable card found for url: ${url}` };
}

async function clickNoteById(noteId) {
  // Find card containing this note ID in its link and click its cover image
  // This opens the XHS modal overlay without triggering anti-bot
  const cards = $$('section.note-item, [data-note-id]');
  for (const card of cards) {
    const link = card.querySelector('a[href]');
    const cardNoteId = card.dataset?.noteId || card.getAttribute('data-note-id') || '';
    if (cardNoteId === noteId || (link && link.href.includes(noteId))) {
      // Scroll card into view first
      card.scrollIntoView({ behavior: 'instant', block: 'center' });
      await wait(500);
      // Click cover image (not <a> tag) to trigger React modal
      const clickTarget = card.querySelector('.cover, .cover-ld, img, .note-cover') || card;
      watchHighlightElement(clickTarget);
      clickTarget.click();
      let overlay = await waitForVisibleNoteOverlay(1200);

      // Check if modal opened
      if (overlay && overlay.offsetHeight > 0) {
        return { ok: true, method: 'overlay' };
      }
      // Retry: click card itself
      card.click();
      overlay = await waitForVisibleNoteOverlay(1200);
      if (overlay && overlay.offsetHeight > 0) {
        return { ok: true, method: 'card_click' };
      }
      return { ok: false, error: `Overlay did not open for note_id: ${noteId}` };
    }
  }
  return { ok: false, error: `No card found with note_id: ${noteId}` };
}

async function closeNoteDetail() {
  const overlaySelectors = '.note-detail-mask, .note-overlay, .note-detail-modal, #noteContainer';

  // First try Escape, which matches human keyboard behavior and is usually stable.
  document.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'Escape', keyCode: 27, code: 'Escape', bubbles: true
  }));
  await wait(800);
  let overlay = document.querySelector(overlaySelectors);
  if (!overlay || overlay.offsetHeight === 0) {
    return { ok: true, method: 'escape' };
  }

  // Try close button — multiple selectors for different XHS layouts
  const closeSelectors = [
    '.close-circle',
    '.note-detail-mask .close',
    '[aria-label="close"]',
    '.close-btn',
    '.note-close',
    'button.close',
    '.reds-note-detail .close',
    // SVG close icon
    '.note-detail-mask svg',
  ];
  for (const sel of closeSelectors) {
    const btn = document.querySelector(sel);
    if (btn) {
      btn.click();
      await wait(1000);
      // Check if overlay is gone
      overlay = document.querySelector(overlaySelectors);
      if (!overlay || overlay.offsetHeight === 0) {
        return { ok: true, method: 'button', selector: sel };
      }
    }
  }
  return { ok: false, error: 'Unable to close note overlay via escape or close button' };
}

async function scrollInNote(pixels = 400) {
  function isScrollable(el) {
    if (!(el instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(el);
    const overflowY = style.overflowY || style.overflow || '';
    const canOverflow = ['auto', 'scroll', 'overlay'].includes(overflowY);
    return el.scrollHeight > el.clientHeight + 24 && canOverflow;
  }

  const overlay = document.querySelector(
    '.note-detail-mask, .note-overlay, .note-detail-modal, .note-detail, #noteContainer'
  );
  const candidates = [
    ...$$(
      [
        '.note-scroller',
        '.note-content',
        '.note-detail .content',
        '.scroll-container',
        '.note-detail',
        '#noteContainer',
        '.note-detail-mask [class*="scroll"]',
        '.note-detail-mask [class*="content"]',
      ].join(', ')
    ),
    overlay,
  ].filter(Boolean);

  const unique = [];
  const seen = new Set();
  for (const node of candidates) {
    if (!(node instanceof HTMLElement)) continue;
    if (seen.has(node)) continue;
    seen.add(node);
    unique.push(node);
  }

  unique.sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
  const scrollContainer = unique.find(isScrollable) || null;
  if (scrollContainer) {
    const before = scrollContainer.scrollTop;
    scrollContainer.scrollBy({ top: pixels, behavior: 'smooth' });
    await wait(900);
    const after = scrollContainer.scrollTop;
    return {
      ok: after !== before,
      scrolled: after !== before,
      delta: after - before,
      container: scrollContainer.className || scrollContainer.id || scrollContainer.tagName,
      error: after !== before ? '' : 'Note scroll container did not move',
    };
  }

  const beforeWindow = window.scrollY;
  window.scrollBy({ top: pixels, behavior: 'smooth' });
  await wait(900);
  const afterWindow = window.scrollY;
  return {
    ok: afterWindow !== beforeWindow,
    scrolled: afterWindow !== beforeWindow,
    delta: afterWindow - beforeWindow,
    container: 'window',
    error: afterWindow !== beforeWindow ? '' : 'Window scroll did not move',
  };
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

chrome.runtime.sendMessage({ type: 'content_ready', url: window.location.href });

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
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
          result = { ok: true, url: window.location.href, state: detectState() };
          break;

        case 'detect_state':
          result = {
            state: detectState(),
            antiBotState: detectAntiBotState(),
            url: window.location.href,
            noteType: detectNoteType(),
          };
          break;

        case 'extract_search_cards':
          result = { cards: extractSearchCards() };
          break;

        case 'extract_search_tabs':
          result = { tabs: extractSearchTabs() };
          break;

        case 'get_search_page_state':
          result = detectSearchPageState();
          break;

        case 'click_search_tab':
          result = await clickSearchTab(msg.params?.label ?? '全部');
          break;

        case 'submit_search_query':
          result = await submitSearchQuery(msg.params?.keyword ?? '');
          break;

        case 'extract_note_content': {
          // Detect whether a note-detail modal OR a direct note page is visible.
          const overlaySelectors = '.note-detail-mask, .note-overlay, .note-detail-modal, #noteContainer';
          const overlay = document.querySelector(overlaySelectors);
          const overlayVisible = !!(overlay && overlay.offsetHeight > 0);
          const isDirectNotePage = !!extractNoteIdFromUrl(window.location.href);
          if (!overlayVisible && !isDirectNotePage) {
            result = {
              error: 'no_note_modal_open',
              message: 'extract_note_content called but no note detail modal is open. Use extract_page_data with command=click_card (or click_note_by_id) to open a note first, or close_note if a stuck modal needs to be dismissed.',
              url: window.location.href,
            };
            break;
          }
          await waitForNoteContent(msg.params?.timeout || 8000);
          const note = extractNoteContent();
          // Flag suspected-stale: same note_id as the previous extract. This
          // usually means close_note failed and the agent is re-reading the
          // same stuck modal. Surface it so the agent can course-correct.
          const prev = window.__flowlensLastNoteId || '';
          if (note.note_id && prev && note.note_id === prev) {
            note._stale_warning = `This looks like the same note as the previous extract (note_id=${note.note_id}). The note modal may not have closed between clicks — use extract_page_data command=close_note, verify the modal is gone, then re-open the target card.`;
          }
          if (note.note_id) window.__flowlensLastNoteId = note.note_id;
          result = { note };
          break;
        }

        case 'collect_carousel_images':
          result = await collectAllCarouselImages(msg.params?.max_images ?? 20);
          break;

        case 'extract_comments':
          result = { comments: extractComments(msg.params || {}) };
          break;

        case 'click_card':
          result = await clickNoteCard(msg.params?.index ?? 0);
          break;

        case 'click_note_link':
          result = await clickNoteByLink(msg.params?.url ?? '');
          break;

        case 'click_note_by_id':
          result = await clickNoteById(msg.params?.note_id ?? '');
          break;

        case 'close_note':
          result = await closeNoteDetail();
          break;

        case 'scroll_note':
          result = await scrollInNote(msg.params?.pixels ?? 400);
          break;

        case 'scroll_page':
          result = await scrollPage(msg.params?.pixels ?? 600);
          break;

        case 'extract_profile_info':
          result = { profile: extractProfileInfo() };
          break;

        case 'extract_profile_notes':
          result = { notes: extractProfileNotes() };
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

console.log('[XHS Agent] Content script loaded:', window.location.href);
