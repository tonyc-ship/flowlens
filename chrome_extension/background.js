/**
 * SocAI Agent — Background Service Worker
 *
 * WebSocket client connecting to the local SocAI agent runtime.
 * Routes commands between the agent and content scripts.
 *
 * Handles directly:
 *   - navigate: chrome.tabs.update
 *   - capture_screenshot: chrome.debugger / captureVisibleTab
 *   - get_tab_info: tab URL and state
 *
 * Forwards to content script:
 *   - All DOM extraction and action commands
 *
 * Watch mode:
 *   - Broadcasts agent commands / results / highlights to the in-page overlay
 */

const DEFAULT_PORT = 8765;
const CONTENT_SCRIPT_FILES = ['content.js', 'content_xhs.js'];
let ws = null;
let activeTabId = null;
let activeTabUrl = '';
let pinnedTabId = null;
let pinnedWindowId = null;
let pinnedTabUrl = '';
let contentReady = new Map(); // tabId -> true
let reconnectTimer = null;
let wsPort = DEFAULT_PORT;
const debuggerQueues = new Map();

// ── Watch Mode State ──────────────────────────────────────────
let watchMode = false;
let watchStartTime = Date.now();
let watchLog = [];
const WATCH_LOG_MAX = 0; // 0 means keep the full task history for the overlay.
const WATCH_AUTO_COMMANDS = new Set([
  'navigate',
  'go_back',
  'create_background_window',
  'create_watch_window',
  'close_window',
  'close_tab',
]);

function targetTabId() {
  return pinnedTabId || activeTabId;
}

function targetTabUrl() {
  return pinnedTabUrl || activeTabUrl || '';
}

function siteFromUrl(url) {
  try {
    const hostname = new URL(url).hostname;
    if (/(^|\.)xiaohongshu\.com$/i.test(hostname)) return 'xiaohongshu';
  } catch {}
  return '';
}

function rememberTabUrl(tabId, url = '', windowId = null) {
  if (!tabId) return;
  activeTabId = tabId;
  if (url) activeTabUrl = url;
  if (pinnedTabId === tabId) {
    if (url) pinnedTabUrl = url;
    if (windowId != null) pinnedWindowId = windowId;
  }
}

function rememberTab(tab) {
  if (!tab?.id) return;
  rememberTabUrl(tab.id, tab.url || '', tab.windowId);
}

function commandTabId(params = {}) {
  return params.tabId || targetTabId();
}

function pinTab(tabId, windowId = null, url = '') {
  pinnedTabId = tabId || null;
  pinnedWindowId = windowId || null;
  pinnedTabUrl = url || (tabId === activeTabId ? activeTabUrl : '') || '';
  if (tabId) activeTabId = tabId;
  if (url) activeTabUrl = url;
}

function releasePinnedTab() {
  pinnedTabId = null;
  pinnedWindowId = null;
  pinnedTabUrl = '';
}

function enqueueDebuggerTask(tabId, task) {
  const key = String(tabId || 'default');
  const previous = debuggerQueues.get(key) || Promise.resolve();
  const run = previous.then(() => task(), () => task());
  debuggerQueues.set(key, run.catch(() => {}));
  return run;
}

function withTimeout(promise, timeoutMs, label = 'debugger operation') {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs)),
  ]);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withTabDebugger(tabId, work, timeoutMs = 5000) {
  return enqueueDebuggerTask(tabId, async () => {
    const target = { tabId };
    await chrome.debugger.attach(target, '1.3');
    try {
      return await withTimeout(work(target), timeoutMs);
    } finally {
      try { await chrome.debugger.detach(target); } catch {}
    }
  });
}

async function setCaptureOverlayHidden(tabId, hidden) {
  if (!tabId) return;
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'socai_capture_overlay', hidden });
  } catch {}
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (shouldHide) => {
        const ids = ['socai-watch-overlay', 'socai-watch-root'];
        ids.forEach((id) => {
          const el = document.getElementById(id);
          if (!el) return;
          if (shouldHide) {
            if (el.dataset.socaiCaptureDisplay === undefined) {
              el.dataset.socaiCaptureDisplay = el.style.display || '';
            }
            el.style.display = 'none';
          } else if (el.dataset.socaiCaptureDisplay !== undefined) {
            el.style.display = el.dataset.socaiCaptureDisplay;
            delete el.dataset.socaiCaptureDisplay;
          } else {
            el.style.display = '';
          }
        });
      },
      args: [hidden],
    });
  } catch {}
}

// ── Watch Mode Helpers ────────────────────────────────────────

function watchElapsed() {
  return Math.round((Date.now() - watchStartTime) / 100) / 10;
}

function statusSnapshot() {
  return {
    connected: ws?.readyState === WebSocket.OPEN,
    port: wsPort,
    activeTabId,
    activeTabUrl,
    pinnedTabId,
    pinnedWindowId,
    pinnedTabUrl,
    targetUrl: targetTabUrl(),
    targetSite: siteFromUrl(targetTabUrl()),
    watchMode,
  };
}

function broadcastOverlay(message, explicitTabId = null) {
  const tabId = explicitTabId || targetTabId();
  if (!tabId) return;
  try {
    chrome.tabs.sendMessage(tabId, message);
  } catch {}
}

function broadcastStatus() {
  const data = statusSnapshot();
  broadcastOverlay({ type: 'watch_status', data });
}

function currentWatchState() {
  return {
    type: 'watch_state',
    status: statusSnapshot(),
    startTime: watchStartTime,
    entries: watchLog,
  };
}

function broadcastWatch(data) {
  if (!watchMode) return;
  if (data.timestamp === undefined) data.timestamp = watchElapsed();

  watchLog.push(data);
  if (WATCH_LOG_MAX > 0 && watchLog.length > WATCH_LOG_MAX) watchLog.shift();
  broadcastOverlay({ type: 'watch_event', data });
  broadcastStatus();
}

function shouldAutoBroadcastCommand(action) {
  return WATCH_AUTO_COMMANDS.has(String(action || ''));
}

function broadcastHighlight(mode, opts) {
  if (!watchMode) return;
  const tabId = targetTabId();
  if (!tabId) return;
  try {
    chrome.tabs.sendMessage(tabId, { type: 'watch_highlight', mode, ...opts });
  } catch {}
}

async function launchDeepLink(url) {
  if (!url) throw new Error('Missing deep link URL');
  try {
    await chrome.tabs.create({ url, active: false });
    return { ok: true, method: 'tabs.create' };
  } catch (err) {
    try {
      await chrome.windows.create({ url, focused: true, state: 'normal' });
      return { ok: true, method: 'windows.create' };
    } catch (innerErr) {
      throw new Error(innerErr.message || err.message || 'Failed to open deep link');
    }
  }
}

async function prepareWatchModeOnCurrentTab(params = {}) {
  let tab = null;
  if (params.tabId) {
    tab = await chrome.tabs.get(params.tabId);
  } else {
    [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  }
  if (!tab?.id) throw new Error('No active tab');

  rememberTab(tab);
  if (tab.windowId) {
    try {
      await chrome.windows.update(tab.windowId, { focused: true });
    } catch {}
  }
  if (params.url && tab.url !== params.url) {
    tab = await chrome.tabs.update(tab.id, { url: params.url });
    rememberTabUrl(tab.id, params.url, tab.windowId);
    await waitForContentScript(tab.id, params.wait || 5000).catch(() => {});
  }
  try {
    tab = await chrome.tabs.get(tab.id);
    rememberTab(tab);
  } catch {}
  if (params.lock !== false) {
    pinTab(tab.id, tab.windowId, tab.url || activeTabUrl);
  }

  watchMode = true;
  watchStartTime = Date.now();
  watchLog = [];
  broadcastStatus();

  broadcastWatch({
    kind: 'session',
    message: `Watch mode attached to current tab ${tab.id}`,
    timestamp: 0,
  });

  return {
    ok: true,
    windowId: tab.windowId,
    tabId: tab.id,
    locked: params.lock !== false,
    watchMode: true,
  };
}

async function createWatchTabInCurrentWindow(params = {}) {
  const win = await chrome.windows.create({
    url: params.url || 'https://www.xiaohongshu.com/explore',
    focused: true,
    state: 'normal',
    type: 'normal',
    width: params.width || 1280,
    height: params.height || 900,
  });
  const tab = win.tabs?.[0] || null;
  if (!tab?.id) throw new Error('Failed to create watch tab');
  await chrome.tabs.update(tab.id, { active: true });

  return await prepareWatchModeOnCurrentTab({
    tabId: tab.id,
    lock: params.lock !== false,
    wait: params.wait,
  });
}

// ── WebSocket Connection ───────────────────────────────────────

function connect(port) {
  wsPort = port || wsPort;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    console.log(ws.readyState === WebSocket.OPEN ? '[BG] Already connected' : '[BG] Connection already in progress');
    return;
  }

  clearTimeout(reconnectTimer);
  console.log(`[BG] Connecting to ws://localhost:${wsPort}...`);
  const socket = new WebSocket(`ws://localhost:${wsPort}`);
  ws = socket;

  socket.onopen = () => {
    if (ws !== socket) {
      try { socket.close(); } catch {}
      return;
    }
    console.log('[BG] Connected to agent');
    clearTimeout(reconnectTimer);
    broadcastStatus();

    // Send connected event with capabilities. Try to include active tab info
    // but always send the event even if no tabs are available (MV3 service
    // workers may not have a "current window" context).
    const sendConnected = (tabId, tabUrl) => {
      if (ws !== socket || ws.readyState !== WebSocket.OPEN) return;
      if (tabId) rememberTabUrl(tabId, tabUrl || '');
      const manifest = chrome.runtime.getManifest();
      ws.send(JSON.stringify({
        type: 'event',
        event: 'connected',
        data: {
          tabId: activeTabId,
          url: tabUrl || '',
          extension_version: manifest.version,
          capabilities: {
            watch_mode: true,
            create_watch_window: true,
            side_panel: false,
            protocol_version: 2,
            extension_version: manifest.version,
          },
        }
      }));
    };

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs && tabs[0]) {
        sendConnected(tabs[0].id, tabs[0].url);
      } else {
        // No active tab in current window — still send connected event
        // so the bridge doesn't hang waiting.
        chrome.tabs.query({ active: true }, (allActive) => {
          if (allActive && allActive[0]) {
            sendConnected(allActive[0].id, allActive[0].url);
          } else {
            sendConnected(null, '');
          }
        });
      }
    });
  };

  socket.onmessage = async (event) => {
    if (ws !== socket) return;
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      console.error('[BG] Invalid message:', event.data);
      return;
    }

    if (msg.type !== 'command') return;

    // ── Watch mode: broadcast incoming command ──
    const autoWatch = watchMode && shouldAutoBroadcastCommand(msg.action);
    if (autoWatch) {
      const kind = categorizeAction(msg.action);
      broadcastWatch({
        kind,
        action: msg.action,
        params: sanitizeParams(msg.params),
        message: describeCommand(msg.action, msg.params),
      });
    }

    const cmdStart = Date.now();
    let response;
    try {
      const result = await handleCommand(msg);
      response = { id: msg.id, type: 'response', data: result };

      // ── Watch mode: broadcast result ──
      if (autoWatch) {
        broadcastWatch({
          kind: 'result',
          action: msg.action,
          message: describeResult(msg.action, result),
          duration: (Date.now() - cmdStart) / 1000,
        });
      }
    } catch (err) {
      response = { id: msg.id, type: 'response', error: err.message };

      // ── Watch mode: broadcast error ──
      if (watchMode) {
        broadcastWatch({
          kind: 'error',
          action: msg.action,
          message: `${msg.action} failed: ${err.message}`,
        });
      }
    }

    try {
      if (ws === socket && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(response));
      } else {
        console.error('[BG] WebSocket not open, cannot send response');
      }
    } catch (sendErr) {
      console.error('[BG] Failed to send response:', sendErr);
    }
  };

  socket.onclose = () => {
    if (ws !== socket) return;
    console.log('[BG] Disconnected from agent');
    ws = null;
    broadcastStatus();
    // Auto-reconnect after 3s
    reconnectTimer = setTimeout(() => connect(wsPort), 3000);
  };

  socket.onerror = (err) => {
    if (ws !== socket) return;
    console.error('[BG] WebSocket error');
  };
}

function disconnect() {
  clearTimeout(reconnectTimer);
  if (ws) {
    ws.close();
    ws = null;
  }
  broadcastStatus();
}

// ── Watch Mode: Command Categorization ────────────────────────

function categorizeAction(action) {
  if (['click_at', 'click_card', 'click_note_by_id', 'click_note_link', 'click_search_tab'].includes(action)) return 'click';
  if (['extract_search_cards', 'extract_note_content', 'extract_comments', 'extract_profile_info', 'extract_profile_notes', 'extract_search_tabs', 'get_search_page_state', 'collect_carousel_images'].includes(action)) return 'extract';
  if (['navigate', 'go_back', 'scroll_page', 'scroll_note', 'press_key', 'type_text', 'find_chat_input', 'set_chat_input_text', 'click_chat_submit', 'mouse_move', 'run_js', 'submit_search_query'].includes(action)) return 'action';
  if (['create_background_window', 'create_watch_window', 'create_tab', 'close_window', 'close_tab', 'lock_active_tab', 'release_active_tab', 'set_active_tab', 'get_tab_info'].includes(action)) return 'action';
  if (['detect_state'].includes(action)) return 'info';
  return 'command';
}

function sanitizeParams(params) {
  if (!params) return {};
  const clean = { ...params };
  // Don't send large code blocks or binary data to watch panel
  if (clean.code && clean.code.length > 200) clean.code = clean.code.slice(0, 200) + '…';
  delete clean.screenshot;
  return clean;
}

function describeCommand(action, params) {
  if (!params) params = {};
  switch (action) {
    case 'navigate': return `Navigate → ${params.url || ''}`.slice(0, 120);
    case 'go_back': return 'Go back in tab history';
    case 'click_at': return `CDP click at (${params.x}, ${params.y})`;
    case 'click_card': return `Click card #${params.index}`;
    case 'click_note_by_id': return `Click note ${params.note_id || params.noteId || ''}`;
    case 'click_note_link': return `Click note link: ${params.url || ''}`.slice(0, 100);
    case 'click_search_tab': return `Switch tab → ${params.label || ''}`;
    case 'submit_search_query': return `Submit XHS search: ${(params.keyword || '').slice(0, 40)}`;
    case 'extract_search_cards': return 'Extract search cards from DOM';
    case 'extract_note_content': return 'Extract note content';
    case 'extract_comments': return `Extract comments (max: ${params.max_comments || 'all'})`;
    case 'extract_profile_info': return 'Extract profile info';
    case 'extract_profile_notes': return 'Extract profile notes';
    case 'collect_carousel_images': return 'Collecting carousel images';
    case 'detect_state': return 'Detect page state';
    case 'scroll_page': return `Scroll page ${params.pixels || 600}px`;
    case 'scroll_note': return `Scroll note ${params.pixels || 400}px`;
    case 'press_key': return `Press key: ${params.key || '?'}`;
    case 'type_text': return `Type text (${(params.text || '').length} chars)`;
    case 'find_chat_input': return 'Find chatbot input';
    case 'set_chat_input_text': return `Set chatbot input (${(params.text || '').length} chars)`;
    case 'click_chat_submit': return 'Click chatbot submit';
    case 'run_js': return 'Execute JavaScript';
    case 'get_tab_info': return 'Get tab info';
    case 'create_background_window': return 'Create background window';
    case 'create_watch_window': return 'Open watch overlay';
    default: return action;
  }
}

function describeResult(action, result) {
  if (!result) return 'OK';
  switch (action) {
    case 'navigate': return `Navigated to ${result.url || 'page'}`.slice(0, 100);
    case 'go_back': return `Back to ${result.url || 'previous page'}`.slice(0, 100);
    case 'detect_state': return `State: ${result.state || '?'}${result.antiBotState ? ' ⚠ ' + result.antiBotState : ''}`;
    case 'extract_search_cards': {
      const cards = result.cards || [];
      return `Found ${cards.length} card${cards.length !== 1 ? 's' : ''}`;
    }
    case 'extract_note_content': {
      const note = result.note || {};
      return note.title ? `"${note.title}"`.slice(0, 100) : 'Note extracted';
    }
    case 'extract_comments': {
      const comments = result.comments || [];
      return `${comments.length} comment${comments.length !== 1 ? 's' : ''}`;
    }
    case 'extract_profile_info': {
      const p = result.profile || {};
      return p.name ? `Profile: ${p.name}` : 'Profile extracted';
    }
    case 'extract_profile_notes': {
      const notes = result.notes || [];
      return `${notes.length} note${notes.length !== 1 ? 's' : ''}`;
    }
    case 'collect_carousel_images': {
      const urls = result.image_urls || [];
      return `${urls.length} image${urls.length !== 1 ? 's' : ''}`;
    }
    case 'click_at': return `Clicked at (${result.x}, ${result.y})`;
    case 'get_tab_info': return `${result.title || result.url || ''}`.slice(0, 100);
    case 'get_search_page_state': return `${result.card_count || 0} cards${result.has_no_results ? ' (no results)' : ''}`;
    default: return result.ok ? 'OK' : JSON.stringify(result).slice(0, 100);
  }
}

// ── Screenshot (CDP via chrome.debugger, fallback to captureVisibleTab) ──

async function captureScreenshot(params = {}) {
  const quality = params.quality || 70;
  const format = params.format || 'jpeg';
  const tabId = commandTabId(params);
  const shouldHideOverlay = params.includeOverlay !== true;

  if (tabId && shouldHideOverlay) {
    await setCaptureOverlayHidden(tabId, true);
    await sleep(80);
  }

  try {
    // Method 1: CDP via chrome.debugger (most reliable in MV3)
    if (tabId) {
      try {
        const debugResult = await withTabDebugger(tabId, async (target) => {
          const result = await chrome.debugger.sendCommand(
            target,
            'Page.captureScreenshot',
            { format, quality, optimizeForSpeed: true }
          );
          if (result && result.data) {
            const mimeType = format === 'png' ? 'image/png' : 'image/jpeg';
            return { screenshot: `data:${mimeType};base64,${result.data}` };
          }
          return null;
        });
        if (debugResult) return debugResult;
      } catch (attachErr) {
        console.error('[BG] debugger.attach failed:', attachErr.message);
        return { screenshot: '', error: `Debugger screenshot failed for tab ${tabId}: ${attachErr.message}` };
      }
    }

    // Method 2: captureVisibleTab fallback
    try {
      const tab = tabId ? await chrome.tabs.get(tabId) : null;
      const dataUrl = await chrome.tabs.captureVisibleTab(
        tab?.windowId || chrome.windows.WINDOW_ID_CURRENT,
        { format, quality }
      );
      return { screenshot: dataUrl };
    } catch (err) {
      console.error('[BG] captureVisibleTab fallback failed:', err.message);
      return { screenshot: '', error: `Screenshot failed: ${err.message}` };
    }
  } finally {
    if (tabId && shouldHideOverlay) {
      await setCaptureOverlayHidden(tabId, false);
    }
  }
}

async function executeDomScript(tabId, func, args = []) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func,
    args,
  });
  return result?.result || {};
}

async function findChatInput(tabId, selectors = []) {
  return await executeDomScript(tabId, (selectors) => {
    const heuristicSelector = [
      'textarea',
      'input[type="text"]',
      'input:not([type])',
      '[contenteditable="true"]',
      '[role="textbox"]',
      '[placeholder]',
      '[data-placeholder]',
      '[aria-label]',
      '.ProseMirror',
      '.ql-editor',
    ].join(',');

    const normalize = (value) => String(value || '').trim().toLowerCase();
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        style.opacity !== '0' &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const editableSelector = 'textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror, .ql-editor';
    const editableFor = (source) => {
      if (!source) return null;
      if (source.matches?.(editableSelector)) return source;
      return source.querySelector?.(editableSelector) || null;
    };
    const targetFor = (editable) => {
      let node = editable;
      let best = editable;
      while (node && node !== document.body) {
        if (isVisible(node)) {
          const rect = node.getBoundingClientRect();
          if (rect.width >= 220 && rect.height >= 28 && rect.height <= 280) {
            best = node;
          }
        }
        node = node.parentElement;
      }
      return best;
    };

    const seen = new Set();
    const candidates = [];
    const pushCandidate = (source, selector = '') => {
      const editable = editableFor(source);
      if (!editable || seen.has(editable) || !isVisible(editable)) return;
      seen.add(editable);

      const target = targetFor(editable);
      const rect = target.getBoundingClientRect();
      const inputRect = editable.getBoundingClientRect();
      const textHint = normalize(
        editable.getAttribute('placeholder') ||
        editable.getAttribute('data-placeholder') ||
        editable.getAttribute('aria-label') ||
        target.getAttribute?.('aria-label') ||
        target.getAttribute?.('data-placeholder') ||
        editable.innerText ||
        target.innerText
      );

      let score = 0;
      if (selector) score += 40;
      if (editable.matches('textarea')) score += 30;
      if (editable.matches('input')) score += 18;
      if (editable.isContentEditable || editable.getAttribute('contenteditable') === 'true') score += 26;
      if (normalize(editable.getAttribute('role')) === 'textbox') score += 18;
      if (String(editable.className || '').includes('ProseMirror')) score += 12;
      if (String(editable.className || '').includes('ql-editor')) score += 12;
      if (
        textHint.includes('ask') ||
        textHint.includes('message') ||
        textHint.includes('anything') ||
        textHint.includes('help') ||
        textHint.includes('today') ||
        textHint.includes('prompt')
      ) score += 18;
      if (rect.bottom > window.innerHeight - 320) score += 14;
      if (rect.width > 220) score += 10;
      if (rect.height >= 28 && rect.height <= 180) score += 6;

      candidates.push({
        found: true,
        selector,
        tag: editable.tagName,
        inputTag: editable.tagName,
        contentEditable: editable.isContentEditable || editable.getAttribute('contenteditable') === 'true',
        textHint: textHint.slice(0, 120),
        x: Math.round(rect.left + rect.width / 2),
        y: Math.round(rect.top + rect.height / 2),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        inputX: Math.round(inputRect.left + inputRect.width / 2),
        inputY: Math.round(inputRect.top + inputRect.height / 2),
        inputWidth: Math.round(inputRect.width),
        inputHeight: Math.round(inputRect.height),
        score,
      });
    };

    for (const selector of selectors || []) {
      try {
        document.querySelectorAll(selector).forEach((node) => pushCandidate(node, selector));
      } catch {}
    }
    document.querySelectorAll(heuristicSelector).forEach((node) => pushCandidate(node));

    candidates.sort((left, right) => right.score - left.score);
    return candidates[0] || { found: false };
  }, [selectors]);
}

async function setChatInputText(tabId, selectors = [], text = '') {
  return await executeDomScript(tabId, (selectors, text) => {
    const heuristicSelector = [
      'textarea',
      'input[type="text"]',
      'input:not([type])',
      '[contenteditable="true"]',
      '[role="textbox"]',
      '[placeholder]',
      '[data-placeholder]',
      '[aria-label]',
      '.ProseMirror',
      '.ql-editor',
    ].join(',');

    const normalize = (value) => String(value || '').trim().toLowerCase();
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        style.opacity !== '0' &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const editableSelector = 'textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror, .ql-editor';
    const textFor = (editable) => {
      if (!editable) return '';
      if (editable.matches?.('textarea, input')) return String(editable.value || '').replace(/\s+/g, ' ').trim();
      return String(editable.innerText || editable.textContent || '').replace(/\s+/g, ' ').trim();
    };
    const editableFor = (source) => {
      if (!source) return null;
      if (source.matches?.(editableSelector)) return source;
      return source.querySelector?.(editableSelector) || null;
    };
    const targetFor = (editable) => {
      let node = editable;
      let best = editable;
      while (node && node !== document.body) {
        if (isVisible(node)) {
          const rect = node.getBoundingClientRect();
          if (rect.width >= 220 && rect.height >= 28 && rect.height <= 280) {
            best = node;
          }
        }
        node = node.parentElement;
      }
      return best;
    };

    let best = null;
    let bestScore = -1;
    const seen = new Set();
    const consider = (source, selector = '') => {
      const editable = editableFor(source);
      if (!editable || seen.has(editable) || !isVisible(editable)) return;
      seen.add(editable);
      const target = targetFor(editable);
      const rect = target.getBoundingClientRect();
      const textHint = normalize(
        editable.getAttribute('placeholder') ||
        editable.getAttribute('data-placeholder') ||
        editable.getAttribute('aria-label') ||
        target.getAttribute?.('aria-label') ||
        target.getAttribute?.('data-placeholder') ||
        editable.innerText ||
        target.innerText
      );
      let score = 0;
      if (selector) score += 40;
      if (editable.matches('textarea')) score += 30;
      if (editable.matches('input')) score += 18;
      if (editable.isContentEditable || editable.getAttribute('contenteditable') === 'true') score += 26;
      if (normalize(editable.getAttribute('role')) === 'textbox') score += 18;
      if (textHint.includes('ask') || textHint.includes('message') || textHint.includes('help') || textHint.includes('today')) score += 18;
      if (rect.bottom > window.innerHeight - 320) score += 14;
      if (rect.width > 220) score += 10;
      if (score > bestScore) {
        best = { editable, target, selector };
        bestScore = score;
      }
    };

    for (const selector of selectors || []) {
      try {
        document.querySelectorAll(selector).forEach((node) => consider(node, selector));
      } catch {}
    }
    document.querySelectorAll(heuristicSelector).forEach((node) => consider(node));

    if (!best?.editable) return { ok: false, found: false };

    const editable = best.editable;
    const target = best.target || editable;
    target.focus?.();
    editable.focus?.();

    if (editable.matches('textarea, input')) {
      const proto = editable.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) setter.call(editable, text);
      else editable.value = text;
      editable.dispatchEvent(new Event('input', { bubbles: true }));
      editable.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      try {
        editable.textContent = '';
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(editable);
        range.collapse(false);
        selection?.removeAllRanges();
        selection?.addRange(range);
        let inserted = false;
        try {
          inserted = document.execCommand('insertText', false, text);
        } catch {}
        if (!inserted) {
          editable.textContent = text;
        }
        editable.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
      } catch (err) {
        return { ok: false, found: true, error: String(err) };
      }
    }

    const appliedText = textFor(editable);

    return {
      ok: true,
      found: true,
      selector: best.selector || '',
      tag: editable.tagName,
      contentEditable: editable.isContentEditable || editable.getAttribute('contenteditable') === 'true',
      textLength: text.length,
      appliedTextLength: appliedText.length,
      hasText: appliedText.length > 0,
    };
  }, [selectors, text]);
}

async function getChatInputState(tabId, selectors = []) {
  return await executeDomScript(tabId, (selectors) => {
    const heuristicSelector = [
      'textarea',
      'input[type="text"]',
      'input:not([type])',
      '[contenteditable="true"]',
      '[role="textbox"]',
      '[placeholder]',
      '[data-placeholder]',
      '[aria-label]',
      '.ProseMirror',
      '.ql-editor',
    ].join(',');

    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        style.opacity !== '0' &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const editableSelector = 'textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror, .ql-editor';
    const editableFor = (source) => {
      if (!source) return null;
      if (source.matches?.(editableSelector)) return source;
      return source.querySelector?.(editableSelector) || null;
    };
    const targetFor = (editable) => {
      let node = editable;
      let best = editable;
      while (node && node !== document.body) {
        if (isVisible(node)) {
          const rect = node.getBoundingClientRect();
          if (rect.width >= 220 && rect.height >= 28 && rect.height <= 280) {
            best = node;
          }
        }
        node = node.parentElement;
      }
      return best;
    };
    const textFor = (editable) => {
      if (!editable) return '';
      if (editable.matches?.('textarea, input')) {
        return normalize(editable.value);
      }
      return normalize(editable.innerText || editable.textContent);
    };

    let best = null;
    let bestScore = -1;
    const seen = new Set();
    const consider = (source, selector = '') => {
      const editable = editableFor(source);
      if (!editable || seen.has(editable) || !isVisible(editable)) return;
      seen.add(editable);
      const target = targetFor(editable);
      const rect = target.getBoundingClientRect();
      const textHint = normalize(
        editable.getAttribute('placeholder') ||
        editable.getAttribute('data-placeholder') ||
        editable.getAttribute('aria-label') ||
        target.getAttribute?.('aria-label') ||
        target.getAttribute?.('data-placeholder')
      ).toLowerCase();
      let score = 0;
      if (selector) score += 40;
      if (editable.matches('textarea')) score += 30;
      if (editable.matches('input')) score += 18;
      if (editable.isContentEditable || editable.getAttribute('contenteditable') === 'true') score += 26;
      if (textHint.includes('ask') || textHint.includes('message') || textHint.includes('help') || textHint.includes('today')) score += 18;
      if (rect.bottom > window.innerHeight - 320) score += 14;
      if (rect.width > 220) score += 10;
      if (score > bestScore) {
        best = { editable, target, selector };
        bestScore = score;
      }
    };

    for (const selector of selectors || []) {
      try {
        document.querySelectorAll(selector).forEach((node) => consider(node, selector));
      } catch {}
    }
    document.querySelectorAll(heuristicSelector).forEach((node) => consider(node));

    if (!best?.editable) return { found: false };

    const editable = best.editable;
    const target = best.target || editable;
    const rect = target.getBoundingClientRect();
    const inputRect = editable.getBoundingClientRect();
    const text = textFor(editable);
    return {
      found: true,
      selector: best.selector || '',
      tag: editable.tagName,
      contentEditable: editable.isContentEditable || editable.getAttribute('contenteditable') === 'true',
      text,
      textLength: text.length,
      empty: text.length === 0,
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      inputX: Math.round(inputRect.left + inputRect.width / 2),
      inputY: Math.round(inputRect.top + inputRect.height / 2),
      inputWidth: Math.round(inputRect.width),
      inputHeight: Math.round(inputRect.height),
      focused: document.activeElement === editable || editable.contains?.(document.activeElement),
    };
  }, [selectors]);
}

async function clickChatSubmit(tabId, selectors = [], anchor = null) {
  return await executeDomScript(tabId, async (selectors, anchor) => {
    const normalize = (value) => String(value || '').trim().toLowerCase();
    const isVisible = (el) => {
      if (!el) return false;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return (
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        style.opacity !== '0' &&
        rect.width > 0 &&
        rect.height > 0
      );
    };
    const editableSelector = 'textarea, input, [contenteditable="true"], [role="textbox"], .ProseMirror, .ql-editor';
    const editableFor = (source) => {
      if (!source) return null;
      if (source.matches?.(editableSelector)) return source;
      return source.querySelector?.(editableSelector) || null;
    };
    const targetFor = (editable) => {
      let node = editable;
      let best = editable;
      while (node && node !== document.body) {
        if (isVisible(node)) {
          const rect = node.getBoundingClientRect();
          if (rect.width >= 220 && rect.height >= 28 && rect.height <= 280) {
            best = node;
          }
        }
        node = node.parentElement;
      }
      return best;
    };
    const textFor = (editable) => {
      if (!editable) return '';
      if (editable.matches?.('textarea, input')) {
        return String(editable.value || '').replace(/\s+/g, ' ').trim();
      }
      return String(editable.innerText || editable.textContent || '').replace(/\s+/g, ' ').trim();
    };

    const heuristicSelector = [
      'textarea',
      'input[type="text"]',
      'input:not([type])',
      '[contenteditable="true"]',
      '[role="textbox"]',
      '.ProseMirror',
      '.ql-editor',
    ].join(',');

    let bestInput = null;
    let bestInputScore = -1;
    const seenInputs = new Set();
    const considerInput = (source, selector = '') => {
      const editable = editableFor(source);
      if (!editable || seenInputs.has(editable) || !isVisible(editable)) return;
      seenInputs.add(editable);
      const target = targetFor(editable);
      const rect = target.getBoundingClientRect();
      const textHint = normalize(
        editable.getAttribute('placeholder') ||
        editable.getAttribute('data-placeholder') ||
        editable.getAttribute('aria-label') ||
        target.getAttribute?.('aria-label') ||
        target.getAttribute?.('data-placeholder')
      );
      let score = 0;
      if (selector) score += 40;
      if (editable.matches('textarea')) score += 30;
      if (editable.matches('input')) score += 18;
      if (editable.isContentEditable || editable.getAttribute('contenteditable') === 'true') score += 26;
      if (textHint.includes('ask') || textHint.includes('message') || textHint.includes('help') || textHint.includes('today')) score += 18;
      if (rect.bottom > window.innerHeight - 320) score += 14;
      if (rect.width > 220) score += 10;
      if (score > bestInputScore) {
        bestInput = { editable, target, selector, rect };
        bestInputScore = score;
      }
    };

    for (const selector of selectors || []) {
      try {
        document.querySelectorAll(selector).forEach((node) => considerInput(node, selector));
      } catch {}
    }
    document.querySelectorAll(heuristicSelector).forEach((node) => considerInput(node));

    const candidates = [];
    const seen = new Set();
    const pushCandidate = (el, selector = '') => {
      if (!el || seen.has(el) || !isVisible(el) || el.disabled) return;
      seen.add(el);

      const rect = el.getBoundingClientRect();
      if (rect.width < 20 || rect.height < 20) return;

      const hint = normalize(
        el.getAttribute('aria-label') ||
        el.getAttribute('title') ||
        el.getAttribute('data-testid') ||
        el.textContent ||
        el.innerText ||
        el.className
      );
      const textOnly = normalize(el.textContent || el.innerText);
      const role = normalize(el.getAttribute('role'));
      const hasPopup = el.getAttribute('aria-haspopup');
      const inputRect = anchor && anchor.x && anchor.y
        ? {
            left: Number(anchor.x) - Number(anchor.width || 0) / 2,
            right: Number(anchor.x) + Number(anchor.width || 0) / 2,
            top: Number(anchor.y) - Number(anchor.height || 0) / 2,
            bottom: Number(anchor.y) + Number(anchor.height || 0) / 2,
            width: Number(anchor.width || 0),
            height: Number(anchor.height || 0),
          }
        : bestInput?.rect;
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      let score = 0;
      if (selector) score += 40;
      if (hint.includes('send') || hint.includes('submit') || hint.includes('up')) score += 30;
      if (rect.right > window.innerWidth - 140) score += 16;
      if (rect.bottom > window.innerHeight - 220) score += 12;
      if (rect.width <= 64 && rect.height <= 64) score += 14;
      if (rect.width > 96) score -= 22;
      if (textOnly.length > 6) score -= 18;
      if (hasPopup || role === 'combobox') score -= 40;
      if (hint.includes('model') || hint.includes('opus') || hint.includes('sonnet') || hint.includes('haiku')) score -= 40;
      if (el.closest('[role="menu"], [role="listbox"]')) score -= 30;
      if (el.closest('fieldset, form')) score += 8;
      if (inputRect) {
        const inputCenterY = inputRect.top + inputRect.height / 2;
        if (centerX >= inputRect.right - 120) score += 30;
        if (Math.abs(centerY - inputCenterY) <= Math.max(40, inputRect.height)) score += 16;
        if (rect.left < inputRect.left + inputRect.width * 0.4) score -= 12;
      }

      candidates.push({ el, rect, score, selector });
    };

    for (const selector of selectors || []) {
      try {
        document.querySelectorAll(selector).forEach((node) => pushCandidate(node, selector));
      } catch {}
    }
    document.querySelectorAll('button, [role="button"]').forEach((node) => pushCandidate(node));

    candidates.sort((left, right) => right.score - left.score);
    const best = candidates[0];
    if (!best?.el) return { ok: false, clicked: false };

    const beforeText = textFor(bestInput?.editable);
    best.el.focus?.();
    try {
      best.el.click();
    } catch {
      best.el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    }
    await new Promise((resolve) => setTimeout(resolve, 80));
    const afterText = textFor(bestInput?.editable);

    return {
      ok: true,
      clicked: true,
      selector: best.selector || '',
      hint: normalize(
        best.el.getAttribute('aria-label') ||
        best.el.getAttribute('title') ||
        best.el.getAttribute('data-testid') ||
        best.el.textContent ||
        best.el.innerText
      ).slice(0, 80),
      x: Math.round(best.rect.left + best.rect.width / 2),
      y: Math.round(best.rect.top + best.rect.height / 2),
      beforeTextLength: beforeText.length,
      afterTextLength: afterText.length,
      inputEmptied: beforeText.length > 0 && afterText.length === 0,
    };
  }, [selectors, anchor]);
}

// ── Command Handling ───────────────────────────────────────────

async function handleCommand(msg) {
  const { action, params = {} } = msg;

  switch (action) {
    // ── Background-handled commands ──

    case 'navigate': {
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      let tab = await chrome.tabs.update(tabId, { url: params.url });
      rememberTabUrl(tabId, params.url || tab?.url || '', tab?.windowId || null);
      // Wait for page load + content script injection
      await waitForContentScript(tabId, params.wait || 5000);
      try {
        tab = await chrome.tabs.get(tabId);
        rememberTab(tab);
      } catch {}
      return { ok: true, url: tab?.url || params.url };
    }

    case 'go_back': {
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      await chrome.scripting.executeScript({
        target: { tabId },
        world: 'MAIN',
        func: () => {
          history.back();
          return true;
        },
      });
      await new Promise(r => setTimeout(r, params.wait || 1500));
      await waitForContentScript(tabId, 3000);
      const tab = await chrome.tabs.get(tabId);
      rememberTab(tab);
      return { ok: true, url: tab.url || '', title: tab.title || '' };
    }

    case 'capture_screenshot': {
      return await captureScreenshot(params);
    }

    case 'get_tab_info': {
      const tabId = commandTabId(params);
      const tab = await chrome.tabs.get(tabId);
      rememberTab(tab);
      return { url: tab.url, title: tab.title, tabId };
    }

    case 'set_active_tab': {
      activeTabId = params.tabId;
      try {
        const tab = await chrome.tabs.get(activeTabId);
        rememberTab(tab);
      } catch {}
      return { ok: true, tabId: activeTabId };
    }

    case 'lock_active_tab': {
      const tabId = params.tabId || activeTabId;
      if (!tabId) throw new Error('No active tab to lock');
      const tab = await chrome.tabs.get(tabId);
      rememberTab(tab);
      pinTab(tab.id, tab.windowId, tab.url || activeTabUrl);
      return { ok: true, tabId: pinnedTabId, windowId: pinnedWindowId };
    }

    case 'release_active_tab': {
      releasePinnedTab();
      return { ok: true };
    }

    case 'create_tab': {
      const tab = await chrome.tabs.create({ url: params.url, active: true });
      rememberTabUrl(tab.id, tab.url || params.url || '', tab.windowId);
      await waitForContentScript(activeTabId, params.wait || 5000);
      try {
        rememberTab(await chrome.tabs.get(tab.id));
      } catch {}
      return { ok: true, tabId: tab.id };
    }

    case 'create_background_window': {
      // Create a new window in the background (same profile = same login state)
      // Does NOT wait for content script — caller should wait separately
      const createOpts = {
        url: params.url || 'about:blank',
        focused: params.focused === true,  // Don't steal focus by default
        state: params.minimized ? 'minimized' : 'normal',
        width: params.width || 1280,
        height: params.height || 900,
      };
      if (params.left != null) createOpts.left = params.left;
      if (params.top != null) createOpts.top = params.top;
      const win = await chrome.windows.create(createOpts);
      const tab = win.tabs[0];
      rememberTabUrl(tab.id, tab.url || params.url || '', win.id);
      if (params.lock !== false) {
        pinTab(tab.id, win.id, tab.url || params.url || activeTabUrl);
      }
      return { ok: true, windowId: win.id, tabId: tab.id, locked: params.lock !== false };
    }

    // ── Watch Mode: current window + in-page overlay ──

    case 'create_watch_window': {
      return await createWatchTabInCurrentWindow(params);
    }

    case 'enable_watch_mode': {
      return await prepareWatchModeOnCurrentTab({
        tabId: commandTabId(params),
        lock: true,
      });
    }

    case 'disable_watch_mode': {
      watchMode = false;
      broadcastStatus();
      return { ok: true };
    }

    case 'get_status': {
      return statusSnapshot();
    }

    case 'watch_log': {
      // Agent sends reasoning / action log entries to the watch panel
      if (watchMode) {
        broadcastWatch({
          kind: params.level || params.kind || 'info',
          phase: params.phase || '',
          message: params.message || '',
          detail: params.detail || '',
          observation: params.observation || '',
          reasoning: params.reasoning || '',
          decision: params.decision || '',
          evidence: params.evidence || '',
          action: params.action_name || '',
          duration: params.duration,
          x: params.x,
          y: params.y,
          target: params.target || '',
        });
      }
      return { ok: true };
    }

    case 'watch_highlight': {
      // Direct highlight command from Python agent
      if (watchMode) {
        broadcastHighlight(params.mode || 'coords', params);
      }
      return { ok: true };
    }

    case 'close_tab': {
      if (!params.tabId) throw new Error('close_tab requires tabId');
      if (activeTabId === params.tabId) {
        activeTabId = null;
        activeTabUrl = '';
      }
      if (pinnedTabId === params.tabId) {
        releasePinnedTab();
      }
      if (watchMode) {
        watchMode = false;
        broadcastStatus();
      }
      await chrome.tabs.remove(params.tabId);
      // Verify it's actually gone
      try {
        await chrome.tabs.get(params.tabId);
        throw new Error(`Tab ${params.tabId} still exists after remove`);
      } catch (e) {
        if (e.message?.includes('still exists')) throw e;
        // Expected: "No tab with id" means removal succeeded
      }
      return { ok: true, removedTabId: params.tabId };
    }

    case 'close_window': {
      if (params.windowId) {
        if (pinnedWindowId === params.windowId) {
          releasePinnedTab();
        }
        if (watchMode) {
          watchMode = false;
          broadcastStatus();
        }
        await chrome.windows.remove(params.windowId);
      }
      return { ok: true };
    }

    case 'reload_extension': {
      // Reload the extension to pick up code changes (content scripts, adapters, etc.)
      // Sends response first, then reloads after a short delay
      setTimeout(() => chrome.runtime.reload(), 200);
      return { ok: true, message: 'Reloading in 200ms — reconnect after' };
    }

    case 'run_js': {
      // Execute JS directly in the page context via chrome.scripting
      // Note: uses world: 'MAIN' to bypass extension CSP
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      const [result] = await chrome.scripting.executeScript({
        target: { tabId },
        world: 'MAIN',
        func: (code) => {
          try {
            return { value: new Function(code)() };
          } catch (e) {
            return { error: e.message };
          }
        },
        args: [params.code || ''],
      });
      return result.result || {};
    }

    case 'submit_search_query': {
      return await sendToContentScript(commandTabId(params), msg);
    }

    case 'find_chat_input': {
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      return await findChatInput(tabId, params.selectors || []);
    }

    case 'set_chat_input_text': {
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      return await setChatInputText(tabId, params.selectors || [], params.text || '');
    }

    case 'get_chat_input_state': {
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      return await getChatInputState(tabId, params.selectors || []);
    }

    case 'click_chat_submit': {
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      return await clickChatSubmit(tabId, params.selectors || [], params.anchor || null);
    }

    case 'click_at': {
      // CDP-based real mouse click — indistinguishable from human clicks.
      // Uses chrome.debugger to dispatch Input.dispatchMouseEvent.
      // params: { x, y } — viewport coordinates to click at
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      const x = params.x || 0;
      const y = params.y || 0;

      // Watch mode: highlight before click
      if (watchMode) {
        broadcastHighlight('coords', { x, y });
      }

      return await withTabDebugger(tabId, async (target) => {
        // mousePressed + mouseReleased = a complete click
        await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {
          type: 'mousePressed', x, y, button: 'left', clickCount: 1,
        });
        // Small human-like delay between press and release
        await new Promise(r => setTimeout(r, 50 + Math.random() * 80));
        await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {
          type: 'mouseReleased', x, y, button: 'left', clickCount: 1,
        });
        return { ok: true, x, y };
      });
    }

    case 'mouse_move': {
      // CDP-based mouse move for more realistic interaction
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      return await withTabDebugger(tabId, async (target) => {
        await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {
          type: 'mouseMoved', x: params.x || 0, y: params.y || 0,
        });
        return { ok: true };
      });
    }

    case 'press_key': {
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      const key = params.key || 'Escape';
      const code = params.code || key;
      const windowsVirtualKeyCode = params.windowsVirtualKeyCode || (key === 'Escape' ? 27 : 0);
      return await withTabDebugger(tabId, async (target) => {
        await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', {
          type: 'keyDown',
          key,
          code,
          windowsVirtualKeyCode,
          nativeVirtualKeyCode: windowsVirtualKeyCode,
        });
        await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', {
          type: 'keyUp',
          key,
          code,
          windowsVirtualKeyCode,
          nativeVirtualKeyCode: windowsVirtualKeyCode,
        });
        return { ok: true, key };
      });
    }

    case 'type_text': {
      // Insert text at current cursor position via CDP Input.insertText
      // Works with textareas, contenteditable, ProseMirror, etc.
      // Handles Unicode/CJK without IME simulation.
      const tabId = commandTabId(params);
      if (!tabId) throw new Error('No active tab');
      const text = params.text || '';
      return await withTabDebugger(tabId, async (target) => {
        await chrome.debugger.sendCommand(target, 'Input.insertText', { text });
        return { ok: true, length: text.length };
      });
    }

    // ── Observer data (Python agent can pull learned observations) ──

    case 'get_observer_data': {
      const keys = ['observer_events', 'observer_stats'];
      const result = await chrome.storage.local.get(keys);
      return {
        events: result.observer_events || [],
        stats: result.observer_stats || {},
      };
    }

    case 'clear_observer_data': {
      await chrome.storage.local.remove(['observer_events', 'observer_stats']);
      return { ok: true };
    }

    // ── Forwarded to content script ──

    default:
      return await sendToContentScript(commandTabId(params), msg);
  }
}

async function sendToContentScript(tabId, msg, retries = 3) {
  if (!tabId) throw new Error('No active tab');
  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, {
        type: 'command',
        action: msg.action,
        params: msg.params || {},
      });
      return response;
    } catch (err) {
      if (attempt < retries - 1) {
        console.log(`[BG] Content script not ready, retry ${attempt + 1}...`);
        await new Promise(r => setTimeout(r, 1500));
        await waitForContentScript(tabId, 3000);
        // Try re-injecting content script
        try {
          await chrome.scripting.executeScript({
            target: { tabId },
            files: CONTENT_SCRIPT_FILES,
          });
        } catch {}
        await new Promise(r => setTimeout(r, 500));
      } else {
        throw new Error(`Content script unreachable after ${retries} attempts: ${err.message}`);
      }
    }
  }
}

async function waitForContentScript(tabId, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      const resp = await chrome.tabs.sendMessage(tabId, {
        type: 'command', action: 'ping', params: {}
      });
      if (resp?.ok) return;
    } catch {}
    await new Promise(r => setTimeout(r, 500));
  }
  // Final attempt: inject content script manually
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: CONTENT_SCRIPT_FILES,
    });
    await new Promise(r => setTimeout(r, 500));
  } catch {}
}

// ── Keepalive & Tab Tracking ───────────────────────────────────

// Long-lived port from content script keeps service worker alive
const keepalivePorts = new Set();
chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'keepalive') {
    keepalivePorts.add(port);
    port.onDisconnect.addListener(() => keepalivePorts.delete(port));
    port.onMessage.addListener((msg) => {
      // Ping received — service worker stays alive
    });
    console.log(`[BG] Keepalive port connected (${keepalivePorts.size} active)`);

    // Auto-connect WebSocket when content script connects (if not already connected)
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connect(wsPort);
    }
    return;
  }
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  if (pinnedTabId && pinnedTabId !== tabId) return;
  activeTabId = tabId;
  chrome.tabs.get(tabId).then((tab) => {
    rememberTab(tab);
    broadcastStatus();
  }).catch(() => broadcastStatus());
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tabId !== activeTabId && tabId !== pinnedTabId) return;
  if (changeInfo.url || tab?.url) {
    rememberTabUrl(tabId, changeInfo.url || tab?.url || '', tab?.windowId || null);
  }
  if (changeInfo.url || changeInfo.status === 'complete') {
    broadcastStatus();
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  contentReady.delete(tabId);
  if (activeTabId === tabId) {
    activeTabId = null;
    activeTabUrl = '';
  }
  if (pinnedTabId === tabId) {
    releasePinnedTab();
  }
  broadcastStatus();
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Content script ready signal
  if (msg.type === 'content_ready' && sender.tab) {
    contentReady.set(sender.tab.id, true);
    if (sender.tab.id === activeTabId || sender.tab.id === pinnedTabId) {
      rememberTabUrl(sender.tab.id, msg.url || sender.tab.url || '', sender.tab.windowId);
    }
    console.log(`[BG] Content script ready on tab ${sender.tab.id}: ${msg.url}`);
    if (watchMode && sender.tab.id === targetTabId()) {
      try { chrome.tabs.sendMessage(sender.tab.id, currentWatchState()); } catch {}
      try { chrome.tabs.sendMessage(sender.tab.id, { type: 'watch_status', data: statusSnapshot() }); } catch {}
    }
    return;
  }

  // Popup status query
  if (msg.action === 'get_status') {
    sendResponse(statusSnapshot());
    return;
  }

  if (msg.action === 'launch_deep_link') {
    launchDeepLink(msg.url)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
});

// Auto-connect on startup and use alarms to keep service worker alive
connect(DEFAULT_PORT);

// Alarms keep the service worker from dying
chrome.alarms.create('keepalive', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepalive') {
    // Reconnect if needed
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connect(wsPort);
    }
  }
});

console.log('[BG] Background service worker started');
