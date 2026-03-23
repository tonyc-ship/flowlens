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
 */

const DEFAULT_PORT = 8765;
let ws = null;
let activeTabId = null;
let contentReady = new Map(); // tabId -> true
let reconnectTimer = null;
let wsPort = DEFAULT_PORT;

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

    let response;
    try {
      const result = await handleCommand(msg);
      response = { id: msg.id, type: 'response', data: result };
    } catch (err) {
      response = { id: msg.id, type: 'response', error: err.message };
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

// ── Screenshot (CDP via chrome.debugger, fallback to captureVisibleTab) ──

async function captureScreenshot(params = {}) {
  const quality = params.quality || 70;
  const format = params.format || 'jpeg';

  // Method 1: CDP via chrome.debugger (most reliable in MV3)
  if (activeTabId) {
    try {
      const target = { tabId: activeTabId };
      await chrome.debugger.attach(target, '1.3');
      try {
        const result = await chrome.debugger.sendCommand(
          target,
          'Page.captureScreenshot',
          { format, quality, optimizeForSpeed: true }
        );
        await chrome.debugger.detach(target);
        if (result && result.data) {
          const mimeType = format === 'png' ? 'image/png' : 'image/jpeg';
          return { screenshot: `data:${mimeType};base64,${result.data}` };
        }
      } catch (cmdErr) {
        try { await chrome.debugger.detach(target); } catch {}
        console.error('[BG] CDP screenshot failed:', cmdErr.message);
      }
    } catch (attachErr) {
      console.error('[BG] debugger.attach failed:', attachErr.message);
    }
  }

  // Method 2: captureVisibleTab fallback
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab(
      chrome.windows.WINDOW_ID_CURRENT,
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
      if (!activeTabId) throw new Error('No active tab');
      await chrome.tabs.update(activeTabId, { url: params.url });
      // Wait for page load + content script injection
      await waitForContentScript(activeTabId, params.wait || 5000);
      return { ok: true, url: params.url };
    }

    case 'capture_screenshot': {
      return await captureScreenshot(params);
    }

    case 'get_tab_info': {
      const tab = await chrome.tabs.get(activeTabId);
      return { url: tab.url, title: tab.title, tabId: activeTabId };
    }

    case 'set_active_tab': {
      activeTabId = params.tabId;
      return { ok: true, tabId: activeTabId };
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
      return { ok: true, windowId: win.id, tabId: tab.id };
    }

    case 'close_window': {
      if (params.windowId) {
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
      if (!activeTabId) throw new Error('No active tab');
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: activeTabId },
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
      if (!activeTabId) throw new Error('No active tab');
      const x = params.x || 0;
      const y = params.y || 0;
      const target = { tabId: activeTabId };
      await chrome.debugger.attach(target, '1.3');
      try {
        // mousePressed + mouseReleased = a complete click
        await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {
          type: 'mousePressed', x, y, button: 'left', clickCount: 1,
        });
        // Small human-like delay between press and release
        await new Promise(r => setTimeout(r, 50 + Math.random() * 80));
        await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {
          type: 'mouseReleased', x, y, button: 'left', clickCount: 1,
        });
        await chrome.debugger.detach(target);
        return { ok: true, x, y };
      } catch (err) {
        try { await chrome.debugger.detach(target); } catch {}
        throw err;
      }
    }

    case 'mouse_move': {
      // CDP-based mouse move for more realistic interaction
      if (!activeTabId) throw new Error('No active tab');
      const target = { tabId: activeTabId };
      await chrome.debugger.attach(target, '1.3');
      try {
        await chrome.debugger.sendCommand(target, 'Input.dispatchMouseEvent', {
          type: 'mouseMoved', x: params.x || 0, y: params.y || 0,
        });
        await chrome.debugger.detach(target);
        return { ok: true };
      } catch (err) {
        try { await chrome.debugger.detach(target); } catch {}
        throw err;
      }
    }

    // ── Forwarded to content script ──

    default:
      return await sendToContentScript(activeTabId, msg);
  }
}

async function sendToContentScript(tabId, msg, retries = 3) {
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
  activeTabId = tabId;
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Content script ready signal
  if (msg.type === 'content_ready' && sender.tab) {
    contentReady.set(sender.tab.id, true);
    console.log(`[BG] Content script ready on tab ${sender.tab.id}: ${msg.url}`);
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
