/**
 * XHS Research Agent — Background Service Worker
 *
 * Orchestrates the research flow:
 *   1. Generate keywords (LLM)
 *   2. For each keyword: search → extract cards → pick best notes (LLM)
 *   3. For each note: open → extract content + comments + images from DOM
 *   4. Synthesize findings (LLM)
 *   5. Generate report
 *
 * LLM calls use the same Claude model as the vision approach (claude-sonnet-4-6).
 * The ONLY LLM usage is for decisions + synthesis, NOT for reading page content.
 */

// ── Config ─────────────────────────────────────────────────────

const CONFIG = {
  model: 'claude-sonnet-4-6', // same as vision approach
  maxNotesPerKeyword: 3,
  maxCommentScrolls: 2,
  apiKeyStorageKey: 'anthropic_api_key',
};

// ── LLM Client ─────────────────────────────────────────────────

async function getApiKey() {
  const { anthropic_api_key } = await chrome.storage.local.get(CONFIG.apiKeyStorageKey);
  return anthropic_api_key;
}

async function callClaude(prompt, maxTokens = 1024) {
  const apiKey = await getApiKey();
  if (!apiKey) throw new Error('No API key set. Configure in extension popup.');

  const resp = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
    },
    body: JSON.stringify({
      model: CONFIG.model,
      max_tokens: maxTokens,
      messages: [{ role: 'user', content: prompt }],
    }),
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Claude API error: ${resp.status} ${err}`);
  }

  const data = await resp.json();
  return data.content[0].text;
}

function extractJson(text) {
  const m = text.match(/[\[{][\s\S]*[\]}]/);
  if (m) {
    try { return JSON.parse(m[0]); } catch {}
  }
  return null;
}

// ── Tab Communication ──────────────────────────────────────────

async function sendToTab(tabId, msg) {
  return chrome.tabs.sendMessage(tabId, msg);
}

function wait(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ── LLM Decision Functions (same logic as vision approach) ─────

async function generateKeywords(topic) {
  const raw = await callClaude(
    `I want to research '${topic}' on Xiaohongshu (小红书). ` +
    `Generate 3-5 Chinese search keywords that would find the most ` +
    `relevant and diverse results. Return only a JSON array of strings.\n` +
    `Example: ["关键词1", "关键词2", "关键词3"]`,
    256
  );
  return extractJson(raw) || [topic];
}

async function pickNotes(cards, topic, maxPicks) {
  if (cards.length <= maxPicks) return cards;

  const raw = await callClaude(
    `I'm researching '${topic}' on Xiaohongshu.\n` +
    `Here are the visible note cards:\n` +
    `${JSON.stringify(cards, null, 1)}\n\n` +
    `Pick the ${maxPicks} most relevant and interesting notes for this research. ` +
    `Prefer notes with high engagement, diverse perspectives, and content-rich titles. ` +
    `Return a JSON array of the selected card objects (copy them exactly).`,
    2048
  );
  const picks = extractJson(raw);
  return (Array.isArray(picks) ? picks : cards).slice(0, maxPicks);
}

async function synthesize(topic, keywords, notes) {
  const notesSummary = notes.map(n => ({
    title: n.title,
    author: n.author,
    likes: n.likes,
    content_preview: (n.content || '').slice(0, 200),
    hashtags: n.hashtags,
    image_count: n.image_count,
    comments_count: n.comments_count,
    keyword: n.source_keyword,
  }));

  return callClaude(
    `I researched '${topic}' on Xiaohongshu. Here's what I found:\n\n` +
    `Keywords searched: ${JSON.stringify(keywords)}\n\n` +
    `Notes collected:\n${JSON.stringify(notesSummary, null, 1)}\n\n` +
    `Synthesize the key findings into a research report (2-3 paragraphs in Chinese). ` +
    `Focus on: main trends, popular content themes, notable creators, ` +
    `audience engagement patterns, and actionable insights.`,
    2048
  );
}

// ── Research Flow ──────────────────────────────────────────────

async function research(tabId, topic, providedKeywords = null) {
  const log = [];
  const step = (action, detail) => {
    const entry = { time: new Date().toLocaleTimeString(), action, detail };
    log.push(entry);
    console.log(`[${entry.time}] ${action}: ${detail}`);
  };

  step('start', `Research topic: ${topic}`);

  // 1. Generate keywords
  const keywords = providedKeywords || await generateKeywords(topic);
  step('keywords', `${keywords.length} keywords: ${JSON.stringify(keywords)}`);

  const allNotes = [];
  const seenTitles = new Set();

  for (const keyword of keywords) {
    // 2. Search
    step('search', keyword);
    const searchResult = await sendToTab(tabId, { action: 'search', keyword });

    if (searchResult.error) {
      step('search_error', searchResult.error);
      continue;
    }

    // Wait for page to settle, re-extract
    await wait(2000);
    const { cards } = await sendToTab(tabId, { action: 'extract_search_cards' });

    if (!cards || cards.length === 0) {
      step('no_cards', 'No cards found');
      continue;
    }
    step('cards_found', `${cards.length} cards`);

    // 3. LLM picks best notes
    const picks = await pickNotes(cards, topic, CONFIG.maxNotesPerKeyword);
    step('picked', `${picks.length} notes to examine`);

    for (const card of picks) {
      const title = card.title || `card_${card.position}`;
      if (seenTitles.has(title)) {
        step('skip_dup', `Already examined: ${title.slice(0, 40)}`);
        continue;
      }
      seenTitles.add(title);

      // 4. Open note
      const cardIndex = card.position ?? cards.indexOf(card);
      step('open_note', title.slice(0, 60));

      // Try clicking by index, or navigate by URL if available
      if (card.link) {
        await sendToTab(tabId, { action: 'navigate', url: card.link });
      } else {
        await sendToTab(tabId, { action: 'click_card', index: cardIndex });
      }
      await wait(2000);

      // 5. Extract note content from DOM
      const { note } = await sendToTab(tabId, { action: 'extract_note_content' });
      if (!note || !note.title) {
        step('extract_failed', 'Could not extract note content');
        await sendToTab(tabId, { action: 'close_note' });
        continue;
      }
      note.source_keyword = keyword;
      step('extracted', `"${note.title}" by ${note.author}, ${note.likes} likes, ${note.image_count} imgs`);

      // 6. Extract comments
      const { comments } = await sendToTab(tabId, { action: 'extract_comments' });
      note.comments = comments || [];
      step('comments', `${note.comments.length} comments`);

      // 7. Get all image URLs (no need to "browse" — DOM has them all)
      const { urls } = await sendToTab(tabId, { action: 'get_image_urls' });
      note.image_urls = urls || [];
      step('images', `${note.image_urls.length} image URLs collected`);

      allNotes.push(note);

      // Close note / go back
      await sendToTab(tabId, { action: 'close_note' });
      await wait(1000);
    }
  }

  // 8. Synthesize
  step('synthesize', `Generating report from ${allNotes.length} notes`);
  const synthesis = await synthesize(topic, keywords, allNotes);

  const report = { topic, keywords, notes: allNotes, synthesis, log };
  step('done', `Research complete: ${allNotes.length} notes`);

  return report;
}

// ── Message handler from popup ─────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'start_research') {
    (async () => {
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const report = await research(tab.id, msg.topic, msg.keywords);
        sendResponse({ report });
      } catch (err) {
        sendResponse({ error: err.message });
      }
    })();
    return true;
  }

  if (msg.action === 'set_api_key') {
    chrome.storage.local.set({ [CONFIG.apiKeyStorageKey]: msg.key });
    sendResponse({ ok: true });
    return;
  }
});
