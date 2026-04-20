const feed = document.getElementById('feed');
const filtersEl = document.getElementById('filters');
const watchState = document.getElementById('watchState');

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
let taskText = '';
let autoScroll = true;
let currentStatus = {};

const FILTER_LABELS = {
  en: {
    all: 'All',
    think: 'Think',
    action: 'Action',
    extract: 'Extract',
    result: 'Result',
    warning: 'Warning',
    error: 'Error',
  },
  zh: {
    all: '全部',
    think: '思考',
    action: '操作',
    extract: '提取',
    result: '结果',
    warning: '警告',
    error: '错误',
  },
};

const KIND_LABELS_ZH = {
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

const ACTION_LABELS_ZH = {
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

const ACTION_LABELS_EN = {
  start: 'Task started',
  turn: 'Turn',
  thinking: 'Thinking',
  tool: 'Tool call',
  navigate: 'Navigate',
  go_back: 'Go back',
  click_at: 'Click coordinates',
  click_card: 'Click card',
  click_note_by_id: 'Open note',
  click_note_link: 'Open note link',
  click_search_tab: 'Switch search tab',
  submit_search_query: 'Submit search',
  extract_search_cards: 'Extract search cards',
  extract_note_content: 'Extract note content',
  extract_comments: 'Extract comments',
  extract_profile_info: 'Extract profile info',
  extract_profile_notes: 'Extract profile notes',
  collect_carousel_images: 'Collect carousel images',
  detect_state: 'Detect page state',
  get_search_page_state: 'Check search state',
  scroll_page: 'Scroll page',
  scroll_note: 'Scroll note',
  press_key: 'Press key',
  type_text: 'Type text',
  run_js: 'Run page script',
  get_tab_info: 'Get tab info',
  create_background_window: 'Create task window',
  create_watch_window: 'Open status panel',
  xhs_topic_scan: 'XHS topic scan',
  run_site_action: 'Site action',
  extract_site_entity: 'Extract site entity',
};

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

function siteFromUrl(url) {
  try {
    const hostname = new URL(url).hostname;
    if (/(^|\.)xiaohongshu\.com$/i.test(hostname)) return 'xiaohongshu';
  } catch {}
  return '';
}

function isXhsTaskText(value) {
  return /小红书|xiaohongshu|\bxhs\b/i.test(String(value || ''));
}

function isXhsContext() {
  return (
    currentStatus.targetSite === 'xiaohongshu' ||
    siteFromUrl(currentStatus.targetUrl || currentStatus.pinnedTabUrl || currentStatus.activeTabUrl) === 'xiaohongshu' ||
    isXhsTaskText(taskText)
  );
}

function languageCode() {
  return isXhsContext() ? 'zh' : 'en';
}

function localized(en, zh) {
  return languageCode() === 'zh' ? zh : en;
}

function updateFilterLabels() {
  const labels = FILTER_LABELS[languageCode()] || FILTER_LABELS.en;
  filtersEl.querySelectorAll('.filter-btn').forEach((button) => {
    button.textContent = labels[button.dataset.filter] || button.textContent;
  });
}

function emptyMessage() {
  return currentStatus.watchMode
    ? localized('Watch mode is active, waiting for agent steps.', '任务已连接，等待 Agent 步骤。')
    : localized('Start a FlowLens task to stream agent activity here.', '开始任务后，这里会实时显示 Agent 步骤。');
}

function setEmptyMessage() {
  feed.innerHTML = `<div class="empty">${escapeHtml(emptyMessage())}</div>`;
}

function taskTextFromEntry(entry) {
  const message = String(entry?.message || '').trim();
  if (!message) return '';
  if (entry?.phase === 'start' || /^Task started:/i.test(message)) {
    return message.replace(/^Task started:\s*/i, '').trim();
  }
  return '';
}

function updateTaskFromEntry(entry) {
  const nextTask = taskTextFromEntry(entry);
  if (nextTask) {
    const changed = taskText !== nextTask;
    taskText = nextTask;
    updateStatus(currentStatus);
    return changed;
  }
  return false;
}

function kindLabel(kind) {
  if (languageCode() !== 'zh') return String(kind || 'info').toUpperCase();
  return KIND_LABELS_ZH[kind] || String(kind || '信息');
}

function actionLabel(entry) {
  const raw = String(entry.action || entry.phase || 'update');
  const lower = raw.toLowerCase();
  if (languageCode() !== 'zh') {
    if (lower === 'turn') {
      const match = String(entry.message || '').match(/Turn\s+(\d+)\/(\d+)/i);
      return match ? `Turn ${match[1]}/${match[2]}` : 'Turn';
    }
    if (entry.phase === 'tool' && entry.action) {
      return ACTION_LABELS_EN[lower] || `Tool: ${entry.action}`;
    }
    return ACTION_LABELS_EN[lower] || raw;
  }
  if (lower === 'turn') {
    const match = String(entry.message || '').match(/Turn\s+(\d+)\/(\d+)/i);
    return match ? `第 ${match[1]}/${match[2]} 轮` : '执行轮次';
  }
  if (entry.phase === 'tool' && entry.action) {
    return ACTION_LABELS_ZH[lower] || `调用工具：${entry.action}`;
  }
  return ACTION_LABELS_ZH[lower] || raw;
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
  const action = actionLabel(entry);
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
      <span class="entry-kind" style="background:${colors.badge}">${escapeHtml(kindLabel(kind))}</span>
      <span class="entry-action">${escapeHtml(action)}</span>
    </div>
    <div class="entry-body">${escapeHtml(truncate(entry.message || '', 320))}</div>
    ${detailParts.length ? `<div class="entry-detail">${escapeHtml(truncate(detailParts.join('\n'), 1200))}</div>` : ''}
  `;
  return el;
}

function loadEntries(nextEntries) {
  entries = Array.isArray(nextEntries) ? nextEntries.slice() : [];
  const startEntry = entries.find((entry) => taskTextFromEntry(entry));
  if (startEntry) updateTaskFromEntry(startEntry);
  updateFilterLabels();
  const previousScrollTop = feed.scrollTop;
  feed.innerHTML = '';
  if (!entries.length) {
    setEmptyMessage();
    return;
  }
  entries.forEach((entry) => {
    feed.appendChild(renderEntry(entry));
  });
  applyFilter();
  if (autoScroll) {
    feed.scrollTop = feed.scrollHeight;
  } else {
    feed.scrollTop = previousScrollTop;
  }
}

function addEntry(entry) {
  removeEmpty();
  const wasXhs = isXhsContext();
  updateTaskFromEntry(entry);
  entries.push(entry);
  if (wasXhs !== isXhsContext()) {
    loadEntries(entries);
    return;
  }
  feed.appendChild(renderEntry(entry));
  applyFilter();
  if (autoScroll) {
    feed.scrollTop = feed.scrollHeight;
  }
}

function updateStatus(status = {}) {
  const wasXhs = isXhsContext();
  currentStatus = { ...currentStatus, ...status };
  const languageChanged = wasXhs !== isXhsContext();
  updateFilterLabels();
  watchState.textContent = taskText
    ? localized(`Task: ${taskText}`, `问题：${taskText}`)
    : (currentStatus.watchMode ? localized('Task running', '任务运行中') : localized('Task idle', '任务空闲'));
  const empty = feed.querySelector('.empty');
  if (empty) empty.textContent = emptyMessage();
  if (languageChanged && entries.length) {
    const previousScrollTop = feed.scrollTop;
    feed.innerHTML = '';
    entries.forEach((entry) => {
      feed.appendChild(renderEntry(entry));
    });
    applyFilter();
    feed.scrollTop = autoScroll ? feed.scrollHeight : previousScrollTop;
  }
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

feed.addEventListener('scroll', () => {
  autoScroll = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 48;
});

chrome.runtime.sendMessage({ action: 'get_status' }, (status) => {
  updateStatus(status || {});
});

connectPanelPort();
