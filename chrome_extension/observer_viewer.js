/**
 * ClawVision Observer Viewer
 *
 * Reads observer data from chrome.storage.local and renders it
 * as timeline, click patterns, navigation flows, and page structures.
 */

(() => {
  'use strict';

  let allEvents = [];
  let stats = {};
  let siteFilter = '';
  let pageFilter = '';

  // ── Data Loading ────────────────────────────────────────────────

  async function loadData() {
    const result = await chrome.storage.local.get(['observer_events', 'observer_stats']);
    allEvents = result.observer_events || [];
    stats = result.observer_stats || {};
    return { events: allEvents, stats };
  }

  function filteredEvents() {
    return allEvents.filter(e => {
      if (siteFilter && e.site !== siteFilter) return false;
      if (pageFilter && e.page_type !== pageFilter) return false;
      return true;
    });
  }

  // ── Stats Bar ───────────────────────────────────────────────────

  function renderStats() {
    const events = filteredEvents();
    const clicks = events.filter(e => e.type === 'click').length;
    const navigations = events.filter(e => e.type === 'navigate').length;
    const structures = events.filter(e => e.type === 'page_structure').length;
    const sites = new Set(events.map(e => e.site).filter(Boolean));

    const timeRange = events.length
      ? formatDuration(events[events.length - 1].ts - events[0].ts)
      : '0s';

    document.getElementById('statsBar').innerHTML = `
      <div class="stat"><div class="label">Total Events</div><div class="value green">${events.length}</div></div>
      <div class="stat"><div class="label">Clicks</div><div class="value blue">${clicks}</div></div>
      <div class="stat"><div class="label">Navigations</div><div class="value yellow">${navigations}</div></div>
      <div class="stat"><div class="label">Structures</div><div class="value purple">${structures}</div></div>
      <div class="stat"><div class="label">Sites</div><div class="value">${sites.size}</div></div>
      <div class="stat"><div class="label">Sessions</div><div class="value">${stats.sessions || 0}</div></div>
      <div class="stat"><div class="label">Time Span</div><div class="value">${timeRange}</div></div>
    `;
  }

  // ── Filters ─────────────────────────────────────────────────────

  function populateFilters() {
    const sites = [...new Set(allEvents.map(e => e.site).filter(Boolean))].sort();
    const pages = [...new Set(allEvents.map(e => e.page_type).filter(Boolean))].sort();

    const siteSelect = document.getElementById('siteFilter');
    siteSelect.innerHTML = '<option value="">All sites</option>' +
      sites.map(s => `<option value="${s}">${s}</option>`).join('');

    const pageSelect = document.getElementById('pageFilter');
    pageSelect.innerHTML = '<option value="">All pages</option>' +
      pages.map(p => `<option value="${p}">${p}</option>`).join('');
  }

  // ── Timeline Panel ──────────────────────────────────────────────

  function renderTimeline() {
    const events = filteredEvents();
    if (!events.length) {
      document.getElementById('timeline').innerHTML = '<div class="empty">No events recorded yet. Browse some pages and come back.</div>';
      return;
    }

    // Show most recent 200 events
    const recent = events.slice(-200).reverse();
    const rows = recent.map(e => {
      const time = new Date(e.ts).toLocaleTimeString();
      const typeTag = `<span class="tag ${e.type}">${e.type}</span>`;
      let detail = '';

      switch (e.type) {
        case 'click':
          detail = `<span class="mono">${esc(e.element?.css_path || '')}</span>`;
          if (e.element?.text_preview) {
            detail += ` <span style="color:var(--text2)">"${esc(e.element.text_preview.slice(0, 40))}"</span>`;
          }
          break;
        case 'navigate':
          detail = `<span class="mono">${esc(shortenUrl(e.to_url || ''))}</span>`;
          break;
        case 'page_leave':
          detail = `${formatDuration(e.duration_ms)} on <span class="mono">${esc(e.from_page_type || '')}</span>`;
          break;
        case 'scroll':
          detail = `${e.scroll_pct}% ${e.direction || ''}`;
          break;
        case 'overlay_open':
        case 'overlay_close':
          detail = e.type === 'overlay_open' ? 'Modal opened' : 'Modal closed';
          break;
        case 'page_structure':
          detail = `<span class="mono">${esc(e.structure?.page_type || '')}</span> snapshot`;
          break;
        case 'session_start':
          detail = `New session`;
          break;
        default:
          detail = JSON.stringify(e).slice(0, 80);
      }

      return `<tr><td style="white-space:nowrap">${time}</td><td>${typeTag}</td><td style="width:100px">${esc(e.page_type || '')}</td><td>${detail}</td></tr>`;
    }).join('');

    document.getElementById('timeline').innerHTML = `
      <table>
        <thead><tr><th>Time</th><th>Type</th><th>Page</th><th>Detail</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  // ── Click Patterns Panel ────────────────────────────────────────

  function renderClickPatterns() {
    const clicks = filteredEvents().filter(e => e.type === 'click' && e.element);

    if (!clicks.length) {
      document.getElementById('clicks').innerHTML = '<div class="empty">No clicks recorded yet.</div>';
      return;
    }

    // Aggregate by css_path
    const pathCounts = {};
    for (const c of clicks) {
      const path = c.element.css_path || '(unknown)';
      if (!pathCounts[path]) {
        pathCounts[path] = { count: 0, examples: [], tag: c.element.tag, classes: c.element.classes };
      }
      pathCounts[path].count++;
      if (pathCounts[path].examples.length < 3 && c.element.text_preview) {
        const text = c.element.text_preview.slice(0, 50);
        if (!pathCounts[path].examples.includes(text)) {
          pathCounts[path].examples.push(text);
        }
      }
    }

    // Sort by frequency
    const sorted = Object.entries(pathCounts)
      .sort((a, b) => b[1].count - a[1].count)
      .slice(0, 50);

    const cards = sorted.map(([path, info]) => `
      <div class="pattern-card">
        <h4><span class="count">${info.count}x</span> &lt;${info.tag}&gt;${info.classes.length ? '.' + info.classes.join('.') : ''}</h4>
        <div class="path">${esc(path)}</div>
        ${info.examples.length ? `<div class="examples">Examples: ${info.examples.map(e => '"' + esc(e) + '"').join(', ')}</div>` : ''}
      </div>
    `).join('');

    document.getElementById('clicks').innerHTML = cards;
  }

  // ── Navigation Flows Panel ──────────────────────────────────────

  function renderNavigationFlows() {
    const navEvents = filteredEvents().filter(e =>
      e.type === 'navigate' || e.type === 'page_leave' || e.type === 'overlay_open' || e.type === 'overlay_close'
    );

    if (!navEvents.length) {
      document.getElementById('navigation').innerHTML = '<div class="empty">No navigation events yet.</div>';
      return;
    }

    // Build transition pairs: page_type_A -> page_type_B
    const transitions = {};
    for (let i = 0; i < navEvents.length - 1; i++) {
      const from = navEvents[i];
      const to = navEvents[i + 1];
      let fromType = from.page_type || from.from_page_type || '';
      let toType = to.page_type || to.to_page_type || '';
      if (from.type === 'overlay_open') toType = 'overlay';
      if (from.type === 'overlay_close') fromType = 'overlay';
      if (!fromType || !toType || fromType === toType) continue;

      const key = `${fromType} -> ${toType}`;
      transitions[key] = (transitions[key] || 0) + 1;
    }

    const sorted = Object.entries(transitions).sort((a, b) => b[1] - a[1]);

    // Also compute time-on-page by page type
    const pageLeaves = filteredEvents().filter(e => e.type === 'page_leave' && e.duration_ms > 0);
    const timeByPage = {};
    for (const e of pageLeaves) {
      const pt = e.from_page_type || 'unknown';
      if (!timeByPage[pt]) timeByPage[pt] = { total: 0, count: 0 };
      timeByPage[pt].total += e.duration_ms;
      timeByPage[pt].count++;
    }

    let html = '<h3 style="margin-bottom:12px;font-size:14px">Navigation Transitions</h3>';
    html += sorted.map(([key, count]) => {
      const [from, to] = key.split(' -> ');
      return `<div class="pattern-card">
        <span class="tag navigate">${esc(from)}</span>
        <span class="flow-arrow">&rarr;</span>
        <span class="tag click">${esc(to)}</span>
        <span class="count" style="float:right">${count}x</span>
      </div>`;
    }).join('');

    const pageTimeEntries = Object.entries(timeByPage).sort((a, b) => b[1].count - a[1].count);
    if (pageTimeEntries.length) {
      html += '<h3 style="margin:20px 0 12px;font-size:14px">Average Time on Page</h3>';
      html += pageTimeEntries.map(([pt, data]) => {
        const avg = Math.round(data.total / data.count);
        return `<div class="pattern-card">
          <span class="tag page_structure">${esc(pt)}</span>
          avg <strong>${formatDuration(avg)}</strong> (${data.count} visits)
        </div>`;
      }).join('');
    }

    document.getElementById('navigation').innerHTML = html;
  }

  // ── Page Structure Panel ────────────────────────────────────────

  function renderStructures() {
    const structs = filteredEvents().filter(e => e.type === 'page_structure' && e.structure);

    if (!structs.length) {
      document.getElementById('structure').innerHTML = '<div class="empty">No page structures captured yet.</div>';
      return;
    }

    // Deduplicate by page_type — keep the most recent for each
    const byType = {};
    for (const s of structs) {
      const pt = s.structure.page_type;
      byType[pt] = s; // last one wins
    }

    const cards = Object.entries(byType).map(([pageType, event]) => {
      const tree = renderTree(event.structure.tree, 0);
      const urlPattern = event.structure.url_pattern || '';
      return `
        <div class="pattern-card">
          <h4><span class="tag page_structure">${esc(pageType)}</span> ${esc(urlPattern)}</h4>
          <div style="margin-top:8px;max-height:300px;overflow:auto">
            <div class="tree">${tree}</div>
          </div>
        </div>`;
    }).join('');

    document.getElementById('structure').innerHTML = cards;
  }

  function renderTree(node, depth) {
    if (!node) return '';
    const indent = '  '.repeat(depth);
    let line = `${indent}<span class="tag-name">&lt;${node.tag}&gt;</span>`;
    if (node.classes?.length) {
      line += `<span class="class-name">.${node.classes.join('.')}</span>`;
    }
    if (node.role) line += ` <span style="color:var(--text2)">[${node.role}]</span>`;
    if (node.total_children) {
      line += ` <span class="count-badge">(${node.total_children} children)</span>`;
    }
    line += '\n';

    if (node.children) {
      for (const child of node.children) {
        line += renderTree(child, depth + 1);
      }
    }
    return line;
  }

  // ── Utilities ───────────────────────────────────────────────────

  function esc(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatDuration(ms) {
    if (!ms || ms < 0) return '0s';
    if (ms < 1000) return ms + 'ms';
    if (ms < 60000) return Math.round(ms / 1000) + 's';
    if (ms < 3600000) return Math.round(ms / 60000) + 'm';
    const hours = Math.floor(ms / 3600000);
    const mins = Math.round((ms % 3600000) / 60000);
    return hours + 'h ' + mins + 'm';
  }

  function shortenUrl(url) {
    try {
      const u = new URL(url);
      return u.pathname + u.search.slice(0, 30);
    } catch {
      return url.slice(0, 60);
    }
  }

  // ── Tab Switching ───────────────────────────────────────────────

  function setupTabs() {
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        tabs.forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.panel).classList.add('active');
        renderActivePanel();
      });
    });
  }

  function renderActivePanel() {
    const active = document.querySelector('.tab.active')?.dataset.panel;
    switch (active) {
      case 'timeline': renderTimeline(); break;
      case 'clicks': renderClickPatterns(); break;
      case 'navigation': renderNavigationFlows(); break;
      case 'structure': renderStructures(); break;
    }
  }

  function renderAll() {
    renderStats();
    renderActivePanel();
  }

  // ── Actions ─────────────────────────────────────────────────────

  function setupActions() {
    document.getElementById('exportBtn').addEventListener('click', async () => {
      const { events, stats } = await loadData();
      const blob = new Blob([JSON.stringify({ events, stats }, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `clawvision_observer_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });

    document.getElementById('clearBtn').addEventListener('click', async () => {
      if (!confirm('Clear all observer data? This cannot be undone.')) return;
      await chrome.storage.local.remove(['observer_events', 'observer_stats']);
      allEvents = [];
      stats = {};
      renderAll();
    });

    document.getElementById('siteFilter').addEventListener('change', (e) => {
      siteFilter = e.target.value;
      renderAll();
    });

    document.getElementById('pageFilter').addEventListener('change', (e) => {
      pageFilter = e.target.value;
      renderAll();
    });
  }

  // ── Init ────────────────────────────────────────────────────────

  async function init() {
    setupTabs();
    setupActions();
    await loadData();
    populateFilters();
    renderAll();

    // Auto-refresh every 5s
    setInterval(async () => {
      await loadData();
      renderAll();
    }, 5000);
  }

  init();
})();
