/**
 * XHS Research Agent — Content Script
 *
 * Runs inside xiaohongshu.com pages. Extracts structured data from the DOM
 * and performs navigation actions. No vision/screenshots needed.
 *
 * Communication: background.js sends messages, content.js replies with data.
 */

// ── DOM Selectors ──────────────────────────────────────────────
// XHS is a React SPA. Class names are semi-stable but may change.
// These selectors are based on the current (2026-03) XHS web structure.
// If XHS updates their DOM, only this section needs updating.

const SEL = {
  // Search
  searchInput: '#search-input, input[placeholder*="搜索"], .search-input input',
  searchIcon: '.search-icon, #search-input + .search-icon',

  // Search results page
  noteCards: '.note-item, section.note-item, [data-note-id]',
  cardTitle: '.title, .note-title, a.title span',
  cardAuthor: '.author-wrapper .name, .author .name',
  cardLikes: '.like-wrapper .count, .engagement .like .count',
  cardImage: '.cover img, .note-cover img',
  cardLink: 'a[href*="/explore/"], a[href*="/search_result/"]',

  // Note detail (modal overlay or full page)
  noteModal: '.note-detail-mask, .note-overlay, #noteContainer',
  noteTitle: '.note-content .title, .note-text .title',
  noteAuthor: '.author-container .username, .info .username, .author-wrapper .username',
  noteAuthorLink: '.author-container a, .info a[href*="/user/profile/"]',
  noteContent: '.note-content .desc, .note-text .content, #detail-desc',
  noteDate: '.note-content .date, .note-text .date, .bottom-container .date',
  noteLikes: '.like-wrapper .count, .engage-bar .like .count',
  noteFavorites: '.collect-wrapper .count, .engage-bar .collect .count',
  noteCommentCount: '.chat-wrapper .count, .engage-bar .chat .count',
  noteHashtags: '.note-content .tag, .hash-tag a, a[href*="/page/topics/"]',
  noteImages: '.carousel-image img, .slide img, .swiper-slide img',
  imageIndicator: '.indicator, .carousel-indicator, .slide-indicator',
  noteCloseBtn: '.close-circle, .note-detail-mask .close, [aria-label="close"]',

  // Comments
  commentList: '.comment-item, .comment-inner, .parent-comment',
  commentUser: '.name, .user-name',
  commentText: '.content, .comment-text',
  commentLikes: '.like .count',

  // Profile page
  profileName: '.user-name, .info .name',
  profileBio: '.user-desc, .desc',
  profileFollowers: '.count[data-type="fans"], .data-count:nth-child(2)',
  profileNotes: '.count[data-type="notes"], .data-count:nth-child(1)',
};

// ── Utility ────────────────────────────────────────────────────

function $(selector, root = document) {
  return root.querySelector(selector);
}

function $$(selector, root = document) {
  return [...root.querySelectorAll(selector)];
}

function text(el) {
  return el ? el.textContent.trim() : '';
}

function wait(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function waitForSelector(selector, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const el = $(selector);
    if (el) return resolve(el);
    const observer = new MutationObserver(() => {
      const el = $(selector);
      if (el) { observer.disconnect(); resolve(el); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); reject(new Error(`Timeout waiting for ${selector}`)); }, timeout);
  });
}

// ── State Detection ────────────────────────────────────────────

function detectState() {
  const url = window.location.href;
  // Note detail as overlay or page
  if ($(SEL.noteModal) || url.includes('/explore/')) return 'note_detail';
  // Search results
  if (url.includes('/search_result') || url.includes('keyword=')) return 'search_results';
  // Profile page
  if (url.includes('/user/profile/')) return 'profile_page';
  // Homepage
  if (url === 'https://www.xiaohongshu.com/' || url.endsWith('/explore')) return 'homepage';
  return 'unknown';
}

// ── Extraction ─────────────────────────────────────────────────

function extractSearchCards() {
  const cards = $$(SEL.noteCards);
  return cards.map((card, i) => {
    // Try multiple selector patterns for robustness
    const titleEl = $(SEL.cardTitle, card);
    const authorEl = $(SEL.cardAuthor, card);
    const likesEl = $(SEL.cardLikes, card);
    const imgEl = $(SEL.cardImage, card);
    const linkEl = card.closest('a') || $('a', card);

    return {
      position: i,
      title: text(titleEl),
      author: text(authorEl),
      likes: text(likesEl),
      image_url: imgEl ? imgEl.src : '',
      link: linkEl ? linkEl.href : '',
      note_id: card.dataset?.noteId || linkEl?.href?.match(/\/([a-f0-9]{24})/)?.[1] || '',
    };
  }).filter(c => c.title || c.link); // skip empty skeleton cards
}

function extractNoteContent() {
  const note = {};
  note.title = text($(SEL.noteTitle));
  note.author = text($(SEL.noteAuthor));
  note.content = text($(SEL.noteContent));
  note.date = text($(SEL.noteDate));
  note.likes = text($(SEL.noteLikes));
  note.favorites = text($(SEL.noteFavorites));
  note.comments_count = text($(SEL.noteCommentCount));

  // Author profile link
  const authorLink = $(SEL.noteAuthorLink);
  note.author_url = authorLink ? authorLink.href : '';

  // Hashtags
  note.hashtags = $$(SEL.noteHashtags).map(el => text(el)).filter(Boolean);

  // Images
  const images = $$(SEL.noteImages);
  note.image_urls = images.map(img => img.src || img.dataset?.src || '').filter(Boolean);
  note.image_count = note.image_urls.length || 1;

  // Image indicator (e.g. "3/7")
  const indicator = $(SEL.imageIndicator);
  if (indicator) {
    const m = text(indicator).match(/(\d+)\s*\/\s*(\d+)/);
    if (m) note.image_count = parseInt(m[2]);
  }

  return note;
}

function extractComments() {
  const items = $$(SEL.commentList);
  return items.map(item => ({
    username: text($(SEL.commentUser, item)),
    text: text($(SEL.commentText, item)),
    likes: text($(SEL.commentLikes, item)),
  })).filter(c => c.text);
}

function extractProfileInfo() {
  return {
    display_name: text($(SEL.profileName)),
    bio: text($(SEL.profileBio)),
    followers: text($(SEL.profileFollowers)),
    notes_count: text($(SEL.profileNotes)),
  };
}

// ── Navigation Actions ─────────────────────────────────────────

async function doSearch(keyword) {
  const input = await waitForSelector(SEL.searchInput, 5000);
  input.focus();
  // Clear and set value via DOM
  const nativeSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value'
  ).set;
  nativeSetter.call(input, keyword);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await wait(200);
  // Press Enter
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
  input.dispatchEvent(new KeyboardEvent('keypress', { key: 'Enter', keyCode: 13, bubbles: true }));
  input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', keyCode: 13, bubbles: true }));
  // Also try form submit
  const form = input.closest('form');
  if (form) form.dispatchEvent(new Event('submit', { bubbles: true }));
  await wait(3000); // wait for navigation + results
  return true;
}

async function clickNoteCard(index) {
  const cards = $$(SEL.noteCards);
  if (index >= cards.length) return false;
  const card = cards[index];
  const link = card.closest('a') || $('a', card);
  if (link) {
    link.click();
    await wait(2000);
    return true;
  }
  card.click();
  await wait(2000);
  return true;
}

async function closeNoteDetail() {
  const closeBtn = $(SEL.noteCloseBtn);
  if (closeBtn) {
    closeBtn.click();
    await wait(1000);
    return true;
  }
  // Fallback: press Escape
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
  await wait(1000);
  return true;
}

async function scrollDown(pixels = 500) {
  window.scrollBy({ top: pixels, behavior: 'smooth' });
  await wait(1000);
}

async function navigateToUrl(url) {
  window.location.href = url;
  await wait(3000);
}

async function navigateBack() {
  window.history.back();
  await wait(2000);
}

async function browseImageByIndex(index) {
  // XHS carousel: try clicking indicator dots, or use keyboard
  const indicators = $$('.indicator-item, .carousel-indicator span, .swiper-pagination-bullet');
  if (indicators[index]) {
    indicators[index].click();
    await wait(500);
    return true;
  }
  // Fallback: dispatch keyboard arrow
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', keyCode: 39, bubbles: true }));
  await wait(500);
  return true;
}

// ── Message Handler ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg.action) {
        case 'detect_state':
          sendResponse({ state: detectState() });
          break;

        case 'extract_search_cards':
          sendResponse({ cards: extractSearchCards() });
          break;

        case 'extract_note_content':
          sendResponse({ note: extractNoteContent() });
          break;

        case 'extract_comments':
          sendResponse({ comments: extractComments() });
          break;

        case 'extract_profile':
          sendResponse({ profile: extractProfileInfo() });
          break;

        case 'search':
          await doSearch(msg.keyword);
          sendResponse({ ok: true, cards: extractSearchCards() });
          break;

        case 'click_card':
          await clickNoteCard(msg.index);
          sendResponse({ ok: true });
          break;

        case 'close_note':
          await closeNoteDetail();
          sendResponse({ ok: true });
          break;

        case 'scroll_down':
          await scrollDown(msg.pixels || 500);
          sendResponse({ ok: true });
          break;

        case 'navigate':
          await navigateToUrl(msg.url);
          sendResponse({ ok: true });
          break;

        case 'navigate_back':
          await navigateBack();
          sendResponse({ ok: true });
          break;

        case 'browse_image':
          await browseImageByIndex(msg.index);
          sendResponse({ ok: true });
          break;

        case 'get_image_urls':
          sendResponse({ urls: extractNoteContent().image_urls });
          break;

        default:
          sendResponse({ error: `Unknown action: ${msg.action}` });
      }
    } catch (err) {
      sendResponse({ error: err.message });
    }
  })();
  return true; // keep channel open for async response
});
