/**
 * XHS Research Agent — Background Service Worker (v2)
 *
 * WebSocket client connecting to the external Python agent.
 * Routes commands between the agent and content scripts.
 *
 * Handles directly:
 *   - navigate: chrome.tabs.update
 *   - capture_screenshot: chrome.tabs.captureVisibleTab
 *   - get_tab_info: tab URL and state
 *
 * Forwards to content script:
 *   - All DOM extraction and action commands
 *
 * Watch mode:
 *   - Broadcasts all agent commands/responses to the injected watch panel
 *   - Sends click highlights before CDP clicks
 *   - Re-injects watch panel on navigation
 */

const DEFAULT_PORT = 8765;
let ws = null;
let activeTabId = null;
let pinnedTabId = null;
let pinnedWindowId = null;
let contentReady = new Map(); // tabId -> true
let reconnectTimer = null;
let wsPort = DEFAULT_PORT;
let debuggerQueue = Promise.resolve();

// ── Watch Mode State ──────────────────────────────────────────
let watchMode = false;
let watchStartTime = Date.now();
let watchLog = [];           // Buffered entries for re-injection
const WATCH_LOG_MAX = 500;

function targetTabId() {
  return pinnedTabId || activeTabId;
}

function pinTab(tabId, windowId = null) {
  pinnedTabId = tabId || null;
  pinnedWindowId = windowId || null;
  if (tabId) activeTabId = tabId;
}

function releasePinnedTab() {
  pinnedTabId = null;
  pinnedWindowId = null;
}

function enqueueDebuggerTask(task) {
  const run = debuggerQueue.then(() => task(), () => task());
  debuggerQueue = run.catch(() => {});
  return run;
}

function withTimeout(promise, timeoutMs, label = 'debugger operation') {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs)),
  ]);
}

async function withTabDebugger(tabId, work, timeoutMs = 5000) {
  return enqueueDebuggerTask(async () => {
    const target = { tabId };
    await chrome.debugger.attach(target, '1.3');
    try {
      return await withTimeout(work(target), timeoutMs);
    } finally {
      try { await chrome.debugger.detach(target); } catch {}
    }
  });
}

// ── Watch Mode Helpers ────────────────────────────────────────

function watchElapsed() {
  return Math.round((Date.now() - watchStartTime) / 100) / 10;
}

function broadcastWatch(data) {
  if (!watchMode) return;
  // Add timestamp if missing
  if (data.timestamp === undefined) data.timestamp = watchElapsed();

  watchLog.push(data);
  if (watchLog.length > WATCH_LOG_MAX) watchLog.shift();

  const tabId = targetTabId();
  if (!tabId) return;
  try {
    chrome.tabs.sendMessage(tabId, { type: 'watch_event', data });
  } catch {}
}

function broadcastHighlight(mode, opts) {
  if (!watchMode) return;
  const tabId = targetTabId();
  if (!tabId) return;
  try {
    chrome.tabs.sendMessage(tabId, { type: 'watch_highlight', mode, ...opts });
  } catch {}
}

async function injectWatchPanel(tabId) {
  if (!tabId) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['watch_panel.js'],
    });
    // Send history after a short delay for panel to initialize
    setTimeout(() => {
      try {
        chrome.tabs.sendMessage(tabId, {
          type: 'watch_load_history',
          entries: watchLog,
          startTime: watchStartTime,
        });
      } catch {}
    }, 300);
  } catch (err) {
    console.log('[BG] Failed to inject watch panel:', err.message);
  }
}

// ── WebSocket Connection ───────────────────────────────────────

function connect(port) {
  wsPort = port || wsPort;
  if (ws && ws.readyState === WebSocket.OPEN) {
    console.log('[BG] Already connected');
    return;
  }

  console.log(`[BG] Connecting to ws://localhost:${wsPort}...`);
  ws = new WebSocket(`ws://localhost:${wsPort}`);

  ws.onopen = () => {
    console.log('[BG] Connected to agent');
    clearTimeout(reconnectTimer);
    // Send initial state (guard against race condition in MV3)
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        activeTabId = tabs[0].id;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'event',
            event: 'connected',
            data: { tabId: activeTabId, url: tabs[0].url }
          }));
        }
      }
    });
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      console.error('[BG] Invalid message:', event.data);
      return;
    }

    if (msg.type !== 'command') return;

    // ── Watch mode: broadcast incoming command ──
    if (watchMode && msg.action !== 'watch_log' && msg.action !== 'capture_screenshot') {
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
      if (watchMode && msg.action !== 'watch_log' && msg.action !== 'capture_screenshot') {
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
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(response));
      } else {
        console.error('[BG] WebSocket not open, cannot send response');
      }
    } catch (sendErr) {
      console.error('[BG] Failed to send response:', sendErr);
    }
  };

  ws.onclose = () => {
    console.log('[BG] Disconnected from agent');
    ws = null;
    // Auto-reconnect after 3s
    reconnectTimer = setTimeout(() => connect(wsPort), 3000);
  };

  ws.onerror = (err) => {
    console.error('[BG] WebSocket error');
  };
}

function disconnect() {
  clearTimeout(reconnectTimer);
  if (ws) {
    ws.close();
    ws = null;
  }
}

// ── Watch Mode: Command Categorization ────────────────────────

function categorizeAction(action) {
  if (['click_at', 'click_card', 'click_note_by_id', 'click_note_link', 'click_search_tab'].includes(action)) return 'click';
  if (['extract_search_cards', 'extract_note_content', 'extract_comments', 'extract_profile_info', 'extract_profile_notes', 'extract_search_tabs', 'get_search_page_state', 'collect_carousel_images'].includes(action)) return 'extract';
  if (['navigate', 'scroll_page', 'scroll_note', 'press_key', 'mouse_move', 'run_js'].includes(action)) return 'action';
  if (['create_background_window', 'create_watch_window', 'create_tab', 'close_window', 'lock_active_tab', 'release_active_tab', 'set_active_tab', 'get_tab_info'].includes(action)) return 'action';
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
    case 'click_at': return `CDP click at (${params.x}, ${params.y})`;
    case 'click_card': return `Click card #${params.index}`;
    case 'click_note_by_id': return `Click note ${params.note_id || params.noteId || ''}`;
    case 'click_note_link': return `Click note link: ${params.url || ''}`.slice(0, 100);
    case 'click_search_tab': return `Switch tab → ${params.label || ''}`;
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
    case 'run_js': return 'Execute JavaScript';
    case 'get_tab_info': return 'Get tab info';
    case 'create_background_window': return 'Create background window';
    case 'create_watch_window': return 'Create watch window';
    default: return action;
  }
}

function describeResult(action, result) {
  if (!result) return 'OK';
  switch (action) {
    case 'navigate': return `Navigated to ${result.url || 'page'}`.slice(0, 100);
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
  const tabId = targetTabId();

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
}

// ── Command Handling ───────────────────────────────────────────

async function handleCommand(msg) {
  const { action, params = {} } = msg;

  switch (action) {
    // ── Background-handled commands ──

    case 'navigate': {
      const tabId = targetTabId();
      if (!tabId) throw new Error('No active tab');
      await chrome.tabs.update(tabId, { url: params.url });
      // Wait for page load + content script injection
      await waitForContentScript(tabId, params.wait || 5000);
      // Re-inject watch panel after navigation
      if (watchMode) {
        await injectWatchPanel(tabId);
      }
      return { ok: true, url: params.url };
    }

    case 'capture_screenshot': {
      return await captureScreenshot(params);
    }

    case 'get_tab_info': {
      const tabId = targetTabId();
      const tab = await chrome.tabs.get(tabId);
      return { url: tab.url, title: tab.title, tabId };
    }

    case 'set_active_tab': {
      activeTabId = params.tabId;
      return { ok: true, tabId: activeTabId };
    }

    case 'lock_active_tab': {
      const tabId = params.tabId || activeTabId;
      if (!tabId) throw new Error('No active tab to lock');
      const tab = await chrome.tabs.get(tabId);
      pinTab(tab.id, tab.windowId);
      return { ok: true, tabId: pinnedTabId, windowId: pinnedWindowId };
    }

    case 'release_active_tab': {
      releasePinnedTab();
      return { ok: true };
    }

    case 'create_tab': {
      const tab = await chrome.tabs.create({ url: params.url, active: true });
      activeTabId = tab.id;
      await waitForContentScript(activeTabId, params.wait || 5000);
      return { ok: true, tabId: tab.id };
    }

    case 'create_background_window': {
      // Create a new window in the background (same profile = same login state)
      // Does NOT wait for content script — caller should wait separately
      const win = await chrome.windows.create({
        url: params.url || 'about:blank',
        focused: false,  // Don't steal focus from user
        state: params.minimized ? 'minimized' : 'normal',
        width: params.width || 1280,
        height: params.height || 900,
      });
      const tab = win.tabs[0];
      activeTabId = tab.id;
      if (params.lock !== false) {
        pinTab(tab.id, win.id);
      }
      return { ok: true, windowId: win.id, tabId: tab.id, locked: params.lock !== false };
    }

    // ── Watch Mode: Foreground window with activity sidebar ──

    case 'create_watch_window': {
      // Create a focused window (foreground) and enable watch mode
      const win = await chrome.windows.create({
        url: params.url || 'about:blank',
        focused: true,   // Foreground — user watches the agent
        state: 'normal',
        width: params.width || 1400,   // Wider to accommodate sidebar
        height: params.height || 900,
      });
      const tab = win.tabs[0];
      activeTabId = tab.id;
      if (params.lock !== false) {
        pinTab(tab.id, win.id);
      }

      // Enable watch mode
      watchMode = true;
      watchStartTime = Date.now();
      watchLog = [];

      // Inject watch panel after page loads
      await waitForContentScript(tab.id, params.wait || 5000).catch(() => {});
      await injectWatchPanel(tab.id);

      broadcastWatch({
        kind: 'session',
        message: `Watch window created — monitoring agent on tab ${tab.id}`,
        timestamp: 0,
      });

      return { ok: true, windowId: win.id, tabId: tab.id, locked: params.lock !== false, watchMode: true };
    }

    case 'enable_watch_mode': {
      watchMode = true;
      watchStartTime = Date.now();
      watchLog = [];
      const tabId = targetTabId();
      if (tabId) {
        await injectWatchPanel(tabId);
      }
      broadcastWatch({ kind: 'session', message: 'Watch mode enabled', timestamp: 0 });
      return { ok: true };
    }

    case 'disable_watch_mode': {
      watchMode = false;
      const tabId = targetTabId();
      if (tabId) {
        try {
          chrome.tabs.sendMessage(tabId, { type: 'watch_panel_hide' });
        } catch {}
      }
      return { ok: true };
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

    case 'close_window': {
      if (params.windowId) {
        if (pinnedWindowId === params.windowId) {
          releasePinnedTab();
        }
        // Disable watch mode if closing the watch window
        if (watchMode) {
          watchMode = false;
        }
        await chrome.windows.remove(params.windowId);
      }
      return { ok: true };
    }

    case 'reload_extension': {
      // Reload the extension to pick up code changes (content.js, etc.)
      // Sends response first, then reloads after a short delay
      setTimeout(() => chrome.runtime.reload(), 200);
      return { ok: true, message: 'Reloading in 200ms — reconnect after' };
    }

    case 'run_js': {
      // Execute JS directly in the page context via chrome.scripting
      // Note: uses world: 'MAIN' to bypass extension CSP
      const tabId = targetTabId();
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

    case 'click_at': {
      // CDP-based real mouse click — indistinguishable from human clicks.
      // Uses chrome.debugger to dispatch Input.dispatchMouseEvent.
      // params: { x, y } — viewport coordinates to click at
      const tabId = targetTabId();
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
      const tabId = targetTabId();
      if (!tabId) throw new Error('No active tab');
      return await withTabDebugger(tabId, async (target) => {
        await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {
          type: 'mouseMoved', x: params.x || 0, y: params.y || 0,
        });
        return { ok: true };
      });
    }

    case 'press_key': {
      const tabId = targetTabId();
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
      return await sendToContentScript(targetTabId(), msg);
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
            files: ['content.js'],
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
      files: ['content.js'],
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
  }
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  if (pinnedTabId && pinnedTabId !== tabId) return;
  activeTabId = tabId;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  contentReady.delete(tabId);
  if (activeTabId === tabId) activeTabId = null;
  if (pinnedTabId === tabId) {
    releasePinnedTab();
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Content script ready signal
  if (msg.type === 'content_ready' && sender.tab) {
    contentReady.set(sender.tab.id, true);
    console.log(`[BG] Content script ready on tab ${sender.tab.id}: ${msg.url}`);
    // Re-inject watch panel when content script loads on the automated tab
    if (watchMode && sender.tab.id === targetTabId()) {
      injectWatchPanel(sender.tab.id);
    }
    return;
  }

  // Watch panel signals
  if (msg.action === 'watch_panel_ready') {
    console.log('[BG] Watch panel ready');
    return;
  }
  if (msg.action === 'watch_panel_closed') {
    console.log('[BG] Watch panel closed by user');
    // Don't disable watch mode — just the panel UI was closed
    // User can re-open by sending enable_watch_mode
    return;
  }

  // Popup messages
  if (msg.action === 'connect') {
    connect(msg.port || DEFAULT_PORT);
    sendResponse({ ok: true });
    return;
  }

  if (msg.action === 'disconnect') {
    disconnect();
    sendResponse({ ok: true });
    return;
  }

  if (msg.action === 'get_status') {
    sendResponse({
      connected: ws?.readyState === WebSocket.OPEN,
      port: wsPort,
      activeTabId,
      pinnedTabId,
      pinnedWindowId,
      watchMode,
    });
    return;
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
