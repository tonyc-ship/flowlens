/**
 * Socai XHS Platform Adapter
 *
 * Site-specific DOM extraction and actions for xiaohongshu.com. The generic
 * content script owns the extension bridge and calls this adapter by method.
 */

(() => {
  if (!/(^|\.)xiaohongshu\.com$/i.test(window.location.hostname)) {
    return;
  }

  const common = window.SocaiCommon;
  if (!common) {
    console.warn('[Socai XHS] Common content helpers are unavailable');
    return;
  }

  const {
    $,
    $$,
    text,
    firstText,
    wait,
    watchHighlightElement,
    parseCount,
    collectVideoCandidates,
  } = common;

  function extractNoteIdFromUrl(url) {
    const value = String(url || '');
    const match = value.match(/\/(?:explore|search_result|discovery)\/([^/?#]+)/i);
    return match ? match[1] : '';
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
    let keywordMatches = true;
    if (normalizedKeyword) {
      keywordMatches = visibleKeyword
        ? visibleKeyword === normalizedKeyword
        : (!urlKeyword || urlKeyword === normalizedKeyword);
    }
    const isSearchResults = state.page_state === 'search_results';
    const hasSearchSurface = state.tabs.length > 0 || state.has_no_results || state.card_count > 0;
    return {
      ok: isSearchResults && keywordMatches && hasSearchSurface && !state.loading,
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

function isVisibleElement(el) {
  if (!el) return false;
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return (
    rect.width > 0 &&
    rect.height > 0 &&
    style.display !== 'none' &&
    style.visibility !== 'hidden' &&
    style.opacity !== '0'
  );
}

function getNoteExtractionRoot() {
  const overlay = getVisibleNoteOverlay();
  if (overlay) return overlay;
  const candidates = [
    '#noteContainer',
    '.note-detail-mask',
    '.note-detail-modal',
    '.note-detail',
    '.note-scroller',
    '.note-content',
  ];
  for (const selector of candidates) {
    const el = document.querySelector(selector);
    if (isVisibleElement(el)) return el;
  }
  return document;
}

function isInCommentArea(el) {
  return !!el?.closest?.(
    '.comments-container, .comment-list, .comment-item, .comment-inner, ' +
    '.comment-wrapper, .parent-comment, .reply-item, .sub-comment-item, ' +
    '.child-comment-item, .reply-comment-item, [class*="comment"]'
  );
}

function normalizeNoteText(value) {
  return String(value || '')
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function firstVisibleText(selectors, root = document, options = {}) {
  for (const sel of selectors) {
    const nodes = $$(sel, root);
    for (const el of nodes) {
      if (!isVisibleElement(el)) continue;
      if (options.excludeComments && isInCommentArea(el)) continue;
      const value = normalizeNoteText(el.innerText || el.textContent || '');
      if (value) return value;
    }
  }
  return '';
}

function visibleTextMatch(selectors, root = document, options = {}) {
  for (const sel of selectors) {
    const nodes = $$(sel, root);
    for (const el of nodes) {
      if (!isVisibleElement(el)) continue;
      if (options.excludeComments && isInCommentArea(el)) continue;
      const value = normalizeNoteText(el.innerText || el.textContent || '');
      if (value) {
        return { selector: sel, text: value };
      }
    }
  }
  return { selector: '', text: '' };
}

function selectorDebug(selectors, root = document) {
  return selectors.map((sel) => {
    const nodes = $$(sel, root);
    const visible = nodes.filter((el) => isVisibleElement(el) && !isInCommentArea(el));
    return {
      selector: sel,
      count: nodes.length,
      visible_count: visible.length,
      first_text: normalizeNoteText(visible[0]?.innerText || visible[0]?.textContent || '').slice(0, 160),
    };
  });
}

const NOTE_IMAGE_SELECTORS = [
  '.carousel-image img',
  '.slide img',
  '.swiper-slide img',
  '.note-slider img',
  '.note-detail img.note-image',
  '.media-container img',
  '.note-scroller img',
].join(', ');

function noteImageUrl(img) {
  return img?.currentSrc || img?.src || img?.dataset?.src || '';
}

function rectVisibleArea(rect) {
  const visibleWidth = Math.max(0, Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0));
  const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
  return visibleWidth * visibleHeight;
}

function orderedNoteImageUrls(root = getNoteExtractionRoot()) {
  const seen = new Set();
  const centerX = window.innerWidth / 2;
  const centerY = window.innerHeight / 2;

  const ranked = $$(NOTE_IMAGE_SELECTORS, root)
    .filter((img) => img instanceof HTMLImageElement && isVisibleElement(img))
    .map((img, domIndex) => {
      const url = noteImageUrl(img);
      if (!url || url.startsWith('data:')) return null;
      const slide = img.closest(
        '.swiper-slide, [class*="swiper-slide"], .slide, [class*="slide"], ' +
        '.carousel-image, [class*="carousel"], .note-slider, .media-container'
      );
      const classBlob = `${img.className || ''} ${slide?.className || ''}`.toLowerCase();
      const active =
        img.getAttribute('aria-current') === 'true'
        || slide?.getAttribute?.('aria-current') === 'true'
        || slide?.getAttribute?.('data-active') === 'true'
        || /\b(?:active|current|selected|swiper-slide-active)\b/.test(classBlob);
      const rect = img.getBoundingClientRect();
      const visibleArea = rectVisibleArea(rect);
      const imageCenterX = rect.left + rect.width / 2;
      const imageCenterY = rect.top + rect.height / 2;
      const centerDistance = Math.abs(imageCenterX - centerX) + Math.abs(imageCenterY - centerY);
      return {
        url,
        domIndex,
        active: active ? 1 : 0,
        visibleArea,
        centerDistance,
      };
    })
    .filter(Boolean)
    .sort((a, b) => (
      (b.active - a.active)
      || (b.visibleArea - a.visibleArea)
      || (a.centerDistance - b.centerDistance)
      || (a.domIndex - b.domIndex)
    ));

  const urls = [];
  for (const item of ranked) {
    if (seen.has(item.url)) continue;
    seen.add(item.url);
    urls.push(item.url);
  }
  return urls;
}

function isLikelyNoteBodyStopLine(line) {
  return (
    /猜你想搜|说点什么/.test(line) ||
    /^(?:共\s*\d*\s*条评论|展开|收起|THE END|- THE END -)$/i.test(line) ||
    /^\d{4}-\d{1,2}-\d{1,2}(?:\s+\S+)?$/.test(line) ||
    /^\d{1,2}-\d{1,2}(?:\s+\S+)?$/.test(line) ||
    /^(?:刚刚|\d+\s*(?:秒|分钟|小时|天)前|昨天|前天|编辑于\s*.+)$/.test(line) ||
    /^(?:加载中|赞|收藏|评论|分享|发送|取消)$/.test(line)
  );
}

function isIgnorableNoteBodyLine(line) {
  return /^(?:已关注|关注|作者|\.\.\.|…|#?广告|举报)$/.test(line);
}

function extractNoteContentFromRootText(root, title = '', author = '') {
  const raw = normalizeNoteText(root?.innerText || root?.textContent || '');
  if (!raw) return '';

  const titleText = normalizeNoteText(title);
  const authorText = normalizeNoteText(author);
  const lines = raw
    .split(/\n+/)
    .map((line) => normalizeNoteText(line))
    .filter(Boolean);
  if (!lines.length) return '';

  let start = -1;
  if (titleText) {
    start = lines.findIndex((line) => line === titleText || line.includes(titleText) || titleText.includes(line));
  }
  if (start < 0 && authorText) {
    const authorIndex = lines.findIndex((line) => line === authorText);
    if (authorIndex >= 0) start = authorIndex;
  }
  if (start < 0) return '';

  const body = [];
  for (const line of lines.slice(start + 1)) {
    if (!line || line === titleText || line === authorText) continue;
    if (isLikelyNoteBodyStopLine(line)) break;
    if (isIgnorableNoteBodyLine(line)) continue;
    body.push(line);
  }

  const cleaned = normalizeNoteText(body.join('\n'));
  return cleaned.length >= 6 ? cleaned : '';
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

function countVisibleNoteLoadingIndicators(root = getNoteExtractionRoot()) {
  const selectors = [
    '.loading',
    '[class*="loading"]',
    '[class*="Loading"]',
    '[class*="skeleton"]',
    '[class*="Skeleton"]',
    '[class*="shimmer"]',
    '.ant-spin',
    '.ant-skeleton',
  ];
  return $$(selectors.join(', '), root).filter((el) => isVisibleElement(el)).length;
}

function noteHasPendingHydration(note, root = getNoteExtractionRoot()) {
  const preview = normalizeNoteText(
    note?.extraction_debug?.root_text_preview || root?.innerText || root?.textContent || ''
  );
  if (/(^|\n)加载中(?:\n|$)/.test(preview)) return true;
  if (/正在加载|请稍候|loading/i.test(preview)) return true;
  return countVisibleNoteLoadingIndicators(root) > 0;
}

async function waitForNoteContent(timeout = 8000) {
  /** Wait for the detail shell to settle; title/media often appear before body text. */
  const deadline = Date.now() + timeout;
  const startedAt = Date.now();
  const minShellSettleMs = 3500;
  let shellSeenAt = 0;
  let bestNote = null;
  let attempts = 0;

  while (Date.now() < deadline) {
    attempts += 1;
    const note = extractNoteContent();
    const root = getNoteExtractionRoot();
    const hasContent = Boolean(normalizeNoteText(note.content));
    const hasShell = Boolean(
      note.note_id
      || note.title
      || note.author
      || note.date
      || note.likes
      || note.comments_count
      || (Array.isArray(note.image_urls) && note.image_urls.length)
      || note.video_url
      || note.poster_url
    );
    const loadingIndicatorCount = countVisibleNoteLoadingIndicators(root);
    const hasPendingHydration = noteHasPendingHydration(note, root);

    if (hasShell) {
      bestNote = note;
      if (!shellSeenAt) shellSeenAt = Date.now();
    }
    if (hasContent) {
      note.extraction_debug = {
        ...(note.extraction_debug || {}),
        wait_reason: 'content_ready',
        wait_attempts: attempts,
        wait_elapsed_ms: Date.now() - startedAt,
        loading_indicator_count: loadingIndicatorCount,
        pending_hydration: hasPendingHydration,
      };
      return note;
    }
    if (hasShell && !hasPendingHydration && Date.now() - shellSeenAt >= minShellSettleMs) {
      note.extraction_debug = {
        ...(note.extraction_debug || {}),
        wait_reason: 'shell_settled_without_content',
        wait_attempts: attempts,
        wait_elapsed_ms: Date.now() - startedAt,
        loading_indicator_count: loadingIndicatorCount,
        pending_hydration: hasPendingHydration,
      };
      return note;
    }

    await wait(250);
  }

  if (bestNote) {
    bestNote.extraction_debug = {
      ...(bestNote.extraction_debug || {}),
      wait_reason: 'timeout_with_shell',
      wait_attempts: attempts,
      wait_elapsed_ms: Date.now() - startedAt,
      loading_indicator_count: countVisibleNoteLoadingIndicators(getNoteExtractionRoot()),
      pending_hydration: noteHasPendingHydration(bestNote, getNoteExtractionRoot()),
    };
  }
  return bestNote;
}

function extractNoteContent() {
  const note = {};
  const root = getNoteExtractionRoot();
  note.type = detectNoteType();
  note.url = window.location.href;
  note.note_id =
    extractNoteIdFromUrl(window.location.href)
    || root.querySelector?.('[data-note-id]')?.dataset?.noteId
    || document.querySelector('[data-note-id]')?.dataset?.noteId
    || '';

  // Title — multiple fallbacks for image-text vs video notes
  note.title = firstVisibleText([
    '#detail-title',
    '.note-content .title',
    '.note-scroller .title',
    '.note-detail .title',
    'h1',
  ], root, { excludeComments: true });

  // Author
  note.author = firstVisibleText([
    '.author-container .username',
    '.author-wrapper .username',
    '.info .username',
    '.user-name',
  ], root);

  // Content — different containers for different note types
  const contentSelectors = [
    '#detail-desc .note-text',
    '#detail-desc',
    '.note-content #detail-desc',
    '.note-scroller #detail-desc',
    '.note-content .note-text',
    '.note-scroller .note-text',
    '.note-content .desc',
    '.note-scroller .desc',
    '.note-detail .desc',
  ];
  const visibleContentNodes = $$(contentSelectors.join(', '), root)
    .filter((el) => isVisibleElement(el) && !isInCommentArea(el));
  const contentMatch = visibleTextMatch(contentSelectors, root, { excludeComments: true });
  note.content = contentMatch.text;
  note.content_source = contentMatch.selector ? `selector:${contentMatch.selector}` : '';

  // If content has nested spans/elements, get the full text
  if (!note.content) {
    const descEl = $$('#detail-desc, .note-content .desc, .note-scroller .desc', root)
      .find(el => isVisibleElement(el) && !isInCommentArea(el));
    if (descEl) {
      note.content = normalizeNoteText(descEl.innerText || descEl.textContent || '');
      note.content_source = 'desc_element_text';
    }
  }
  if (!note.content) {
    note.content = extractNoteContentFromRootText(root, note.title, note.author);
    if (note.content) note.content_source = 'root_text_after_title';
  }
  // Date
  note.date = firstVisibleText([
    '.note-content .date',
    '.bottom-container .date',
    '.note-scroller .date',
    '.date',
  ], root, { excludeComments: true });

  // Engagement metrics
  const likeSelectors = [
    '.like-wrapper .count',
    '.engage-bar .like .count',
    '[data-type="like"] .count',
    '.engage-bar-style .like-wrapper .count',
  ];
  const favoriteSelectors = [
    '.collect-wrapper .count',
    '.engage-bar .collect .count',
    '[data-type="collect"] .count',
  ];
  const commentCountSelectors = [
    '.chat-wrapper .count',
    '.engage-bar .chat .count',
    '[data-type="chat"] .count',
  ];
  const shareSelectors = [
    '.share-wrapper .count',
    '.engage-bar .share .count',
  ];

  const likesMatch = visibleTextMatch(likeSelectors, root, { excludeComments: true });
  const favoritesMatch = visibleTextMatch(favoriteSelectors, root, { excludeComments: true });
  const commentsMatch = visibleTextMatch(commentCountSelectors, root, { excludeComments: true });
  const sharesMatch = visibleTextMatch(shareSelectors, root, { excludeComments: true });

  note.likes = likesMatch.text;
  note.favorites = favoritesMatch.text;
  note.comments_count = commentsMatch.text;
  note.shares = sharesMatch.text;

  note.extraction_debug = {
    content_source: note.content_source || 'empty',
    root_selector: root.id ? `#${root.id}` : (root.className ? `.${String(root.className).trim().split(/\s+/).join('.')}` : root.tagName),
    root_text_preview: normalizeNoteText(root.innerText || root.textContent || '').slice(0, 1200),
    content_selector_visible_count: visibleContentNodes.length,
    loading_indicator_count: countVisibleNoteLoadingIndicators(root),
    content_selector_debug: selectorDebug(contentSelectors, root),
    engagement_selector_debug: {
      likes: { selector: likesMatch.selector || '', text: (likesMatch.text || '').slice(0, 64) },
      favorites: { selector: favoritesMatch.selector || '', text: (favoritesMatch.text || '').slice(0, 64) },
      comments_count: { selector: commentsMatch.selector || '', text: (commentsMatch.text || '').slice(0, 64) },
      shares: { selector: sharesMatch.selector || '', text: (sharesMatch.text || '').slice(0, 64) },
    },
  };

  // Author profile link
  const authorLink = root.querySelector?.(
    '.author-container a[href*="/user/"], .info a[href*="/user/profile/"]'
  );
  note.author_url = authorLink ? authorLink.href : '';
  note.ip_location = firstVisibleText([
    '.note-content .ip-location',
    '.publish-info .ip-location',
    '.ip-location',
    '.note-ip-location',
  ], root, { excludeComments: true });
  note.location = firstVisibleText([
    '.note-content .location',
    '.publish-info .location',
    '.location-info',
    '.note-location',
  ], root, { excludeComments: true });

  // Hashtags
  note.hashtags = $$('.hash-tag a, a[href*="/page/topics/"], .note-content .tag, #detail-desc a.tag', root)
    .map(el => text(el))
    .filter(Boolean);

  // Images — different for image-text vs video notes
  if (note.type === 'image') {
    note.image_urls = orderedNoteImageUrls(root);

    // Image indicator (e.g. "3/7")
    const indicator = root.querySelector?.(
      '.indicator, .carousel-indicator, .slide-indicator, .image-index'
    );
    if (indicator) {
      const m = text(indicator).match(/(\d+)\s*[/／]\s*(\d+)/);
      if (m) note.total_images = parseInt(m[2]);
    }
  } else {
    // Video note — get video poster/thumbnail
    const video = root.querySelector?.('video') || document.querySelector('video');
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
  const root = getNoteExtractionRoot();
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

  const items = $$(rootSelectors, root).filter(item => !item.parentElement?.closest(rootSelectors));
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

  const seenUrls = new Set();
  const orderedUrls = [];
  let matchedStrategy = '';

  function collectCurrent() {
    const prioritized = orderedNoteImageUrls(searchRoot);
    for (const url of prioritized) {
      if (seenUrls.has(url)) continue;
      seenUrls.add(url);
      orderedUrls.push(url);
      if (!matchedStrategy) matchedStrategy = 'orderedNoteImageUrls';
      return true;
    }
    return false;
  }

  collectCurrent();

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
    const advanced = collectCurrent();

    if (!advanced || seenUrls.size === prevCount) {
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


  async function extractNoteContentCommand(params = {}) {
    const overlaySelectors = '.note-detail-mask, .note-overlay, .note-detail-modal, #noteContainer';
    const overlay = document.querySelector(overlaySelectors);
    const overlayVisible = !!(overlay && overlay.offsetHeight > 0);
    const isDirectNotePage = !!extractNoteIdFromUrl(window.location.href);
    if (!overlayVisible && !isDirectNotePage) {
      return {
        error: 'no_note_modal_open',
        message: 'extract_note_content called but no note detail modal is open. Use extract_page_data with command=click_card (or click_note_by_id) to open a note first, or close_note if a stuck modal needs to be dismissed.',
        url: window.location.href,
      };
    }
    const note = await waitForNoteContent(params.timeout || 8000) || extractNoteContent();
    const prev = window.__socaiLastNoteId || '';
    if (note.note_id && prev && note.note_id === prev) {
      note._stale_warning = `This looks like the same note as the previous extract (note_id=${note.note_id}). The note modal may not have closed between clicks — use extract_page_data command=close_note, verify the modal is gone, then re-open the target card.`;
    }
    if (note.note_id) window.__socaiLastNoteId = note.note_id;
    return { note };
  }

  window.SocaiXhs = {
    detectAntiBotState,
    detectState,
    detectNoteType,
    extractSearchCards,
    extractSearchTabs,
    detectSearchPageState,
    clickSearchTab,
    submitSearchQuery,
    extractNoteContentCommand,
    collectAllCarouselImages,
    extractComments,
    clickNoteCard,
    clickNoteByLink,
    clickNoteById,
    closeNoteDetail,
    scrollInNote,
    extractProfileInfo,
    extractProfileNotes,
  };

  setTimeout(() => {
    try {
      chrome.runtime.sendMessage({ type: 'content_ready', url: window.location.href, adapter: 'xhs' });
    } catch {}
  }, 0);

  console.log('[Socai XHS] Adapter loaded:', window.location.href);
})();
