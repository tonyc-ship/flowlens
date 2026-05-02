/**
 * FlowLens — Passive Site Observer
 *
 * Silently monitors user behavior on matched websites to learn:
 *   - Navigation patterns (how users move between pages)
 *   - Interaction patterns (what elements users click, how they scroll)
 *   - Page structure (DOM shape of different page types)
 *   - Content patterns (what text/media appears where)
 *
 * All data stays local in chrome.storage.local. Nothing is sent externally
 * unless the Python agent explicitly requests it.
 *
 * This module is PASSIVE — it never modifies the page or interferes with
 * the user's browsing. It only listens and records.
 */

(() => {
  'use strict';

  // ── Config ──────────────────────────────────────────────────────

  const MAX_EVENTS = 2000;        // Cap raw events to limit storage
  const MAX_PATTERNS = 500;       // Cap per-type pattern entries
  const FLUSH_INTERVAL = 10000;   // Write to storage every 10s
  const STRUCTURE_INTERVAL = 30000; // Snapshot page structure every 30s
  const SCROLL_DEBOUNCE = 1000;   // Debounce scroll events

  // ── State ───────────────────────────────────────────────────────

  const pendingEvents = [];       // Buffer before flush
  let pageEntryTime = Date.now();
  let lastScrollTime = 0;
  let lastUrl = location.href;
  let isInitialized = false;

  // ── Helpers ─────────────────────────────────────────────────────

  function detectPageType() {
    const url = location.href;
    if (/\/search_result|keyword=/.test(url)) return 'search_results';
    if (/\/explore\/[^/?#]+/.test(url)) return 'note_detail';
    if (/\/user\/profile\//.test(url)) return 'profile';
    if (/xiaohongshu\.com\/?$|\/explore\/?$/.test(url)) return 'homepage';

    // DOM-based fallback
    if (document.querySelector('.note-detail-mask, .note-overlay, #noteContainer')) {
      return 'note_detail';
    }
    return 'unknown';
  }

  function getSiteName() {
    const host = location.hostname;
    if (host.includes('xiaohongshu.com')) return 'xiaohongshu';
    if (host.includes('douyin.com')) return 'douyin';
    if (host.includes('taobao.com')) return 'taobao';
    return host.replace('www.', '').split('.')[0];
  }

  /** Build a compact CSS-like path for an element (no IDs to avoid PII). */
  function getCssPath(el, maxDepth = 5) {
    const parts = [];
    let current = el;
    for (let i = 0; i < maxDepth && current && current !== document.body; i++) {
      let seg = current.tagName.toLowerCase();
      // Include meaningful classes (skip hashes/generated ones)
      const classes = [...(current.classList || [])]
        .filter(c => c.length < 30 && !/^[a-f0-9]{6,}$|^css-/.test(c))
        .slice(0, 3);
      if (classes.length) seg += '.' + classes.join('.');
      parts.unshift(seg);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  /** Get semantic info about a clicked element. */
  function describeElement(el) {
    if (!el || !el.tagName) return null;
    const tag = el.tagName.toLowerCase();
    const classes = [...(el.classList || [])]
      .filter(c => c.length < 30 && !/^[a-f0-9]{6,}$|^css-/.test(c))
      .slice(0, 5);
    const role = el.getAttribute('role') || '';
    const ariaLabel = el.getAttribute('aria-label') || '';

    // Get text content (truncated, no PII)
    let textContent = (el.textContent || '').trim().slice(0, 80);
    // Avoid capturing user-typed input
    if (tag === 'input' || tag === 'textarea') textContent = '';

    const rect = el.getBoundingClientRect();
    const isVisible = rect.width > 0 && rect.height > 0;

    return {
      tag,
      classes,
      role,
      aria_label: ariaLabel,
      text_preview: textContent,
      css_path: getCssPath(el),
      is_visible: isVisible,
      rect: isVisible ? {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      } : null,
    };
  }

  /** Lightweight page type snapshot — just type + URL pattern, no full DOM tree.
   *  The agent can always read the live DOM; saving the tree wastes context. */
  function snapshotPageStructure() {
    // Identify scrollable containers on this page type (useful for scroll automation)
    const scrollables = [];
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 100) {
        const path = getCssPath(el, 3);
        if (!scrollables.some(s => s.path === path)) {
          scrollables.push({
            path,
            scroll_height: el.scrollHeight,
            client_height: el.clientHeight,
          });
        }
        if (scrollables.length >= 5) break;
      }
    }

    return {
      page_type: detectPageType(),
      url_pattern: location.pathname.replace(/[a-f0-9]{24}/g, '{id}').replace(/\d{10,}/g, '{num}'),
      scrollable_containers: scrollables,
    };
  }

  // ── Event Recording ─────────────────────────────────────────────

  function record(event) {
    event.ts = Date.now();
    event.site = getSiteName();
    event.url = location.href;
    event.page_type = detectPageType();
    pendingEvents.push(event);
  }

  // ── Observers ───────────────────────────────────────────────────

  function onClickCapture(e) {
    // Find the meaningful target (skip text nodes, go up to real element)
    let target = e.target;
    if (!target || !target.tagName) return;

    // Skip extension-injected elements
    if (target.closest?.('[data-flowlens]')) return;

    const desc = describeElement(target);
    if (!desc) return;

    // Also describe the nearest "semantic container" (card, button, link)
    const container = target.closest('section, article, a, button, [role="button"], [data-note-id]');
    const containerDesc = container && container !== target ? describeElement(container) : null;

    record({
      type: 'click',
      element: desc,
      container: containerDesc,
    });
  }

  function onScroll(e) {
    const now = Date.now();
    if (now - lastScrollTime < SCROLL_DEBOUNCE) return;
    lastScrollTime = now;

    const target = e?.target;
    const isContainer = target && target !== document && target !== document.documentElement;

    let scrollY, maxScroll, containerPath;
    if (isContainer && target.scrollHeight > target.clientHeight) {
      scrollY = target.scrollTop;
      maxScroll = target.scrollHeight - target.clientHeight;
      containerPath = getCssPath(target);
    } else {
      scrollY = window.scrollY;
      maxScroll = document.documentElement.scrollHeight - window.innerHeight;
      containerPath = 'window';
    }
    const scrollPct = maxScroll > 0 ? Math.round((scrollY / maxScroll) * 100) : 0;

    record({
      type: 'scroll',
      scroll_y: Math.round(scrollY),
      scroll_pct: scrollPct,
      direction: scrollY > (window._lastScrollY || 0) ? 'down' : 'up',
      container: containerPath,
    });
    window._lastScrollY = scrollY;
  }

  function onUrlChange() {
    const newUrl = location.href;
    if (newUrl === lastUrl) return;

    // Record page leave duration
    const duration = Date.now() - pageEntryTime;
    record({
      type: 'page_leave',
      from_url: lastUrl,
      duration_ms: duration,
      from_page_type: detectPageType(),
    });

    lastUrl = newUrl;
    pageEntryTime = Date.now();

    record({
      type: 'navigate',
      to_url: newUrl,
      to_page_type: detectPageType(),
    });
  }

  /** Watch for SPA-style modal overlays (XHS opens notes as overlays). */
  function watchOverlays() {
    const observer = new MutationObserver(() => {
      const overlay = document.querySelector(
        '.note-detail-mask, .note-overlay, #noteContainer, .note-detail-modal'
      );
      const isVisible = overlay && overlay.offsetHeight > 0;
      const wasVisible = window._overlayWasVisible || false;

      if (isVisible && !wasVisible) {
        record({ type: 'overlay_open' });
      } else if (!isVisible && wasVisible) {
        record({ type: 'overlay_close' });
      }
      window._overlayWasVisible = isVisible;
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: false });
  }

  /** Periodically snapshot page structure to learn DOM patterns. */
  function startStructureSnapshots() {
    // Take one immediately
    setTimeout(() => {
      record({
        type: 'page_structure',
        structure: snapshotPageStructure(),
      });
    }, 3000); // Wait for page to settle

    setInterval(() => {
      // Only snapshot if page type changed or URL changed
      const currentType = detectPageType();
      if (currentType !== window._lastSnapshotType) {
        record({
          type: 'page_structure',
          structure: snapshotPageStructure(),
        });
        window._lastSnapshotType = currentType;
      }
    }, STRUCTURE_INTERVAL);
  }

  // ── Storage ─────────────────────────────────────────────────────

  async function flushToStorage() {
    if (!pendingEvents.length) return;

    const batch = pendingEvents.splice(0);

    try {
      const result = await chrome.storage.local.get(['observer_events', 'observer_stats']);
      let events = result.observer_events || [];
      let stats = result.observer_stats || {
        total_events: 0,
        first_event: null,
        last_event: null,
        sessions: 0,
      };

      events.push(...batch);

      // Trim oldest events if over limit
      if (events.length > MAX_EVENTS) {
        events = events.slice(events.length - MAX_EVENTS);
      }

      stats.total_events += batch.length;
      if (!stats.first_event) stats.first_event = batch[0].ts;
      stats.last_event = batch[batch.length - 1].ts;

      await chrome.storage.local.set({
        observer_events: events,
        observer_stats: stats,
      });
    } catch (err) {
      // Storage full or other error — drop the batch
      console.warn('[Observer] Storage flush failed:', err.message);
    }
  }

  // ── Initialization ──────────────────────────────────────────────

  function init() {
    if (isInitialized) return;
    isInitialized = true;

    // Record session start
    record({
      type: 'session_start',
      referrer: document.referrer,
      user_agent_hint: navigator.userAgentData?.platform || navigator.platform,
    });

    // Increment session count
    chrome.storage.local.get(['observer_stats'], (result) => {
      const stats = result.observer_stats || { total_events: 0, sessions: 0 };
      stats.sessions = (stats.sessions || 0) + 1;
      chrome.storage.local.set({ observer_stats: stats });
    });

    // Attach listeners
    document.addEventListener('click', onClickCapture, true); // Capture phase
    window.addEventListener('scroll', onScroll, { passive: true });
    // Capture scroll events on inner containers (comments, note panel, feed)
    // Using capture phase so we see scrolls on any scrollable element
    document.addEventListener('scroll', onScroll, { passive: true, capture: true });

    // SPA navigation detection (XHS uses History API)
    const origPushState = history.pushState;
    history.pushState = function () {
      origPushState.apply(this, arguments);
      setTimeout(onUrlChange, 100);
    };
    const origReplaceState = history.replaceState;
    history.replaceState = function () {
      origReplaceState.apply(this, arguments);
      setTimeout(onUrlChange, 100);
    };
    window.addEventListener('popstate', () => setTimeout(onUrlChange, 100));

    // Overlay watcher
    watchOverlays();

    // Structure snapshots
    startStructureSnapshots();

    // Periodic flush to storage
    setInterval(flushToStorage, FLUSH_INTERVAL);

    // Flush on page unload
    window.addEventListener('beforeunload', () => {
      const duration = Date.now() - pageEntryTime;
      record({
        type: 'page_leave',
        from_url: location.href,
        duration_ms: duration,
        from_page_type: detectPageType(),
      });
      // Synchronous flush attempt
      flushToStorage();
    });

    console.log('[Observer] Passive site observer active on', getSiteName());
  }

  // Start when DOM is ready
  if (document.body) {
    init();
  } else {
    document.addEventListener('DOMContentLoaded', init);
  }
})();
