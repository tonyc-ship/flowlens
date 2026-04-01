const feed = document.getElementById('feed');
const filtersEl = document.getElementById('filters');
const watchState = document.getElementById('watchState');
const targetState = document.getElementById('targetState');
const eventCount = document.getElementById('eventCount');

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

let entries = [];
let activeFilter = 'all';
let panelPort = null;

function escapeHtml(str) {
  const el = document.createElement('div');
  el.textContent = str || '';
  return el.innerHTML;
}

function truncate(str, len) {
  if (!str) return '';
  return str.length > len ? str.slice(0, len) + '…' : str;
}

function formatTime(ts) {
  if (typeof ts !== 'number') return '';
  return ts < 60 ? `${ts.toFixed(1)}s` : `${Math.floor(ts / 60)}m ${Math.floor(ts % 60)}s`;
}

function removeEmpty() {
  const empty = feed.querySelector('.empty');
  if (empty) empty.remove();
}

function updateCount() {
  eventCount.textContent = `${entries.length} event${entries.length === 1 ? '' : 's'}`;
}

function applyFilter() {
  feed.querySelectorAll('.entry').forEach((el) => {
    if (activeFilter === 'all' || el.dataset.kind === activeFilter) {
      el.classList.remove('hidden');
    } else {
      el.classList.add('hidden');
    }
  });
}

function renderEntry(entry) {
  const kind = entry.kind || 'info';
  const colors = ENTRY_COLORS[kind] || ENTRY_COLORS.info;
  const action = entry.action || entry.phase || '';
  const detailParts = [];
  if (entry.detail) detailParts.push(entry.detail);
  if (entry.observation) detailParts.push(`Observation: ${entry.observation}`);
  if (entry.reasoning) detailParts.push(`Reasoning: ${entry.reasoning}`);
  if (entry.decision) detailParts.push(`Decision: ${entry.decision}`);
  if (entry.evidence) detailParts.push(`Evidence: ${entry.evidence}`);
  if (entry.target) detailParts.push(`Target: ${entry.target}`);

  const el = document.createElement('div');
  el.className = 'entry';
  el.dataset.kind = kind;
  el.style.background = colors.bg;
  el.style.borderLeftColor = colors.border;
  el.innerHTML = `
    <div class="entry-header">
      <span class="entry-time">${escapeHtml(formatTime(entry.timestamp))}</span>
      <span class="entry-kind" style="background:${colors.badge}">${escapeHtml(kind.toUpperCase())}</span>
      <span class="entry-action">${escapeHtml(action)}</span>
    </div>
    <div class="entry-body">${escapeHtml(truncate(entry.message || '', 320))}</div>
    ${detailParts.length ? `<div class="entry-detail">${escapeHtml(truncate(detailParts.join('\n'), 1200))}</div>` : ''}
  `;
  return el;
}

function loadEntries(nextEntries) {
  entries = Array.isArray(nextEntries) ? nextEntries.slice() : [];
  feed.innerHTML = '';
  if (!entries.length) {
    feed.innerHTML = '<div class="empty">Watch mode is active, but no activity has arrived yet.</div>';
    updateCount();
    return;
  }
  entries.forEach((entry) => {
    feed.appendChild(renderEntry(entry));
  });
  updateCount();
  applyFilter();
  feed.scrollTop = feed.scrollHeight;
}

function addEntry(entry) {
  removeEmpty();
  entries.push(entry);
  if (entries.length > 500) entries = entries.slice(-500);
  feed.appendChild(renderEntry(entry));
  updateCount();
  applyFilter();
  feed.scrollTop = feed.scrollHeight;
}

function updateStatus(status = {}) {
  watchState.textContent = status.watchMode ? 'Watch active' : 'Watch idle';
  targetState.textContent = status.pinnedTabId || status.activeTabId
    ? `Target tab ${status.pinnedTabId || status.activeTabId}`
    : 'No target tab';
}

function handlePanelMessage(msg) {
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'watch_state') {
    updateStatus(msg.status || {});
    loadEntries(msg.entries || []);
    return;
  }
  if (msg.type === 'status') {
    updateStatus(msg.data || {});
    return;
  }
  if (msg.type === 'watch_event') {
    addEntry(msg.data || {});
  }
}

function connectPanelPort() {
  try {
    panelPort = chrome.runtime.connect({ name: 'sidepanel' });
    panelPort.onMessage.addListener(handlePanelMessage);
    chrome.windows.getCurrent().then((currentWindow) => {
      try {
        panelPort?.postMessage({ type: 'panel_context', windowId: currentWindow?.id || null });
      } catch {}
    }).catch(() => {});
    panelPort.onDisconnect.addListener(() => {
      panelPort = null;
      setTimeout(connectPanelPort, 1000);
    });
  } catch (err) {
    setTimeout(connectPanelPort, 1000);
  }
}

filtersEl.addEventListener('click', (event) => {
  const btn = event.target.closest('.filter-btn');
  if (!btn) return;
  activeFilter = btn.dataset.filter;
  filtersEl.querySelectorAll('.filter-btn').forEach((el) => el.classList.remove('active'));
  btn.classList.add('active');
  applyFilter();
});

chrome.runtime.sendMessage({ action: 'get_status' }, (status) => {
  updateStatus(status || {});
});

connectPanelPort();
