const $ = id => document.getElementById(id);

function updateUI(status) {
  const bar = $('statusBar');
  const connectBtn = $('connectBtn');
  const disconnectBtn = $('disconnectBtn');

  if (status.connected) {
    bar.className = 'status connected';
    bar.textContent = `Agent: connected (port ${status.port})`;
    connectBtn.style.display = 'none';
    disconnectBtn.style.display = '';
  } else {
    bar.className = 'status disconnected';
    bar.textContent = 'Agent: disconnected';
    connectBtn.style.display = '';
    disconnectBtn.style.display = 'none';
  }
}

// Get initial status
chrome.runtime.sendMessage({ action: 'get_status' }, updateUI);

$('connectBtn').addEventListener('click', () => {
  const port = parseInt($('port').value) || 8765;
  chrome.runtime.sendMessage({ action: 'connect', port }, () => {
    setTimeout(() => {
      chrome.runtime.sendMessage({ action: 'get_status' }, updateUI);
    }, 1000);
  });
});

$('disconnectBtn').addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'disconnect' }, () => {
    chrome.runtime.sendMessage({ action: 'get_status' }, updateUI);
  });
});

// ── Observer ──────────────────────────────────────────────────
$('observerBtn').addEventListener('click', () => {
  chrome.tabs.create({ url: chrome.runtime.getURL('observer_viewer.html') });
});

function updateObserverStats() {
  chrome.storage.local.get(['observer_stats'], (result) => {
    const s = result.observer_stats;
    if (s && s.total_events > 0) {
      $('observerStats').textContent =
        `${s.total_events} events recorded across ${s.sessions || 0} sessions`;
    }
  });
}
updateObserverStats();

// Poll status every 2s while popup is open
setInterval(() => {
  chrome.runtime.sendMessage({ action: 'get_status' }, updateUI);
  updateObserverStats();
}, 2000);
