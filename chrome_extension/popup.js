const $ = id => document.getElementById(id);

function updateUI(status) {
  const bar = $('statusBar');
  if (!bar) return;
  if (status && status.connected) {
    bar.className = 'status connected';
    bar.textContent = `Agent: connected (port ${status.port})`;
  } else {
    bar.className = 'status disconnected';
    bar.textContent = 'Agent: disconnected';
  }
}

function refresh() {
  chrome.runtime.sendMessage({ action: 'get_status' }, (status) => {
    if (chrome.runtime.lastError) return;
    updateUI(status);
  });
}

refresh();
setInterval(refresh, 2000);
