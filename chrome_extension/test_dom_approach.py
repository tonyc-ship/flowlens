"""
DOM-based XHS research — Playwright implementation.

Same research flow as research_agent.py but using DOM extraction
instead of vision/screenshots. Uses the same Claude model for LLM decisions.

This script demonstrates the DOM approach's speed/cost/accuracy
for direct comparison with the vision approach.
"""

import json
import os
import re
import time
from pathlib import Path

import anthropic
from playwright.sync_api import sync_playwright, Page

# Load API key
for p in [os.path.expanduser("~/.zshrc.pre-oh-my-zsh"), os.path.expanduser("~/.zshrc")]:
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                if "ANTHROPIC_API_KEY" in line and "export" in line:
                    val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    os.environ["ANTHROPIC_API_KEY"] = val
                    break

MODEL = "claude-sonnet-4-6"  # same as vision approach
OUTPUT_DIR = Path("tests/eval_report/dom_research")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

client = anthropic.Anthropic()
log_entries = []
step_count = 0


def log_step(action: str, detail: str = ""):
    global step_count
    step_count += 1
    t = time.strftime("%H:%M:%S")
    log_entries.append({"step": step_count, "time": t, "action": action, "detail": detail})
    print(f"  [{step_count:03d}] {action}: {detail[:80]}")


def call_claude(prompt: str, max_tokens: int = 1024) -> str:
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def extract_json(text: str):
    m = re.search(r"[\[{][\s\S]*[\]}]", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


# ── DOM Extraction ──────────────────────────────────────────────

def extract_search_cards(page: Page) -> list[dict]:
    """Extract note cards from XHS search results page via DOM."""
    cards = page.evaluate("""() => {
        // XHS search results: each card is inside a section or div with note info
        const cards = document.querySelectorAll('section.note-item, [data-note-id], .feeds-container > div > div');
        const results = [];
        for (const card of cards) {
            const titleEl = card.querySelector('.title, a.title span, .note-link .title');
            const authorEl = card.querySelector('.author-wrapper .name, .author .name, .nickname');
            const likesEl = card.querySelector('.like-wrapper .count, .count');
            const linkEl = card.querySelector('a[href*="/explore/"], a[href*="/search_result/"]') || card.closest('a');

            const title = titleEl?.textContent?.trim() || '';
            const author = authorEl?.textContent?.trim() || '';
            const likes = likesEl?.textContent?.trim() || '';
            const link = linkEl?.href || '';

            if (title || link) {
                results.push({ title, author, likes, link, position: results.length });
            }
        }
        return results;
    }""")
    return cards


def extract_note_content(page: Page) -> dict:
    """Extract note detail from the current page/modal via DOM."""
    note = page.evaluate("""() => {
        const n = {};
        // Try multiple selector patterns
        const selectors = {
            title: ['.note-content .title', '#detail-title', '.note-text .title', 'h1'],
            author: ['.author-container .username', '.info .username', '.author-wrapper .username', '.user-name'],
            authorUrl: ['.author-container a[href*="/user/"]', '.info a[href*="/user/profile/"]'],
            content: ['.note-content .desc', '#detail-desc', '.note-text .content', '.note-scroller .content'],
            date: ['.note-content .date', '.bottom-container .date', '.note-text .date'],
            likes: ['.like-wrapper .count', '.engage-bar .like .count', '[data-type="like"] .count'],
            favorites: ['.collect-wrapper .count', '.engage-bar .collect .count', '[data-type="collect"] .count'],
            commentsCount: ['.chat-wrapper .count', '.engage-bar .chat .count', '[data-type="chat"] .count'],
        };

        function trySelectors(sels) {
            for (const sel of sels) {
                const el = document.querySelector(sel);
                if (el && el.textContent.trim()) return el.textContent.trim();
            }
            return '';
        }

        n.title = trySelectors(selectors.title);
        n.author = trySelectors(selectors.author);
        n.content = trySelectors(selectors.content);
        n.date = trySelectors(selectors.date);
        n.likes = trySelectors(selectors.likes);
        n.favorites = trySelectors(selectors.favorites);
        n.comments_count = trySelectors(selectors.commentsCount);

        const authorLink = document.querySelector(selectors.authorUrl[0]) || document.querySelector(selectors.authorUrl[1]);
        n.author_url = authorLink?.href || '';

        // Hashtags
        n.hashtags = [...document.querySelectorAll('.hash-tag a, a[href*="/page/topics/"], .tag')].map(el => el.textContent.trim()).filter(Boolean);

        // Images - get ALL URLs from carousel
        const imgs = document.querySelectorAll('.carousel-image img, .slide img, .swiper-slide img, .note-slider img');
        n.image_urls = [...imgs].map(img => img.src || img.dataset?.src || '').filter(Boolean);
        n.image_count = n.image_urls.length || 1;

        // Image indicator
        const indicator = document.querySelector('.indicator, .carousel-indicator, .slide-indicator');
        if (indicator) {
            const m = indicator.textContent.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
            if (m) n.image_count = parseInt(m[2]);
        }

        return n;
    }""")
    return note


def extract_comments(page: Page) -> list[dict]:
    """Extract comments from the current note detail via DOM."""
    comments = page.evaluate("""() => {
        const items = document.querySelectorAll('.comment-item, .parent-comment, .comment-inner');
        return [...items].map(item => {
            const user = item.querySelector('.name, .user-name, .nickname');
            const text = item.querySelector('.content, .comment-text, .note-text');
            const likes = item.querySelector('.like .count, .like-wrapper .count');
            return {
                username: user?.textContent?.trim() || '',
                text: text?.textContent?.trim() || '',
                likes: likes?.textContent?.trim() || '',
            };
        }).filter(c => c.text);
    }""")
    return comments


# ── LLM Decision Functions (identical to vision approach) ───────

def generate_keywords(topic: str) -> list[str]:
    raw = call_claude(
        f"I want to research '{topic}' on Xiaohongshu (小红书). "
        f"Generate 3-5 Chinese search keywords that would find the most "
        f"relevant and diverse results. Return only a JSON array of strings.\n"
        f'Example: ["关键词1", "关键词2", "关键词3"]',
        256,
    )
    return extract_json(raw) or [topic]


def pick_notes(cards: list[dict], topic: str, max_picks: int) -> list[dict]:
    if len(cards) <= max_picks:
        return cards
    raw = call_claude(
        f"I'm researching '{topic}' on Xiaohongshu.\n"
        f"Here are the visible note cards:\n"
        f"{json.dumps(cards, ensure_ascii=False, indent=1)}\n\n"
        f"Pick the {max_picks} most relevant and interesting notes for this research. "
        f"Prefer notes with high engagement, diverse perspectives, and content-rich titles. "
        f"Return a JSON array of the selected card objects (copy them exactly).",
        2048,
    )
    picks = extract_json(raw)
    return (picks if isinstance(picks, list) else cards)[:max_picks]


def synthesize(topic: str, keywords: list[str], notes: list[dict]) -> str:
    summaries = [
        {
            "title": n.get("title", ""),
            "author": n.get("author", ""),
            "likes": n.get("likes", ""),
            "content_preview": n.get("content", "")[:200],
            "hashtags": n.get("hashtags", []),
            "image_count": n.get("image_count", 0),
            "comments_count": n.get("comments_count", ""),
            "keyword": n.get("source_keyword", ""),
        }
        for n in notes
    ]
    return call_claude(
        f"I researched '{topic}' on Xiaohongshu. Here's what I found:\n\n"
        f"Keywords searched: {json.dumps(keywords, ensure_ascii=False)}\n\n"
        f"Notes collected:\n{json.dumps(summaries, ensure_ascii=False, indent=1)}\n\n"
        f"Synthesize the key findings into a research report (2-3 paragraphs in Chinese). "
        f"Focus on: main trends, popular content themes, notable creators, "
        f"audience engagement patterns, and actionable insights.",
        2048,
    )


# ── Main Research Flow ──────────────────────────────────────────

def research(topic: str, keywords: list[str] | None = None):
    t0 = time.time()
    all_notes = []
    seen_titles = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        # Inject cookies from user's Chrome (XHS requires login for search results)
        try:
            import rookiepy
            raw_cookies = rookiepy.chrome(domains=[".xiaohongshu.com"])
            pw_cookies = []
            for c in raw_cookies:
                pw_cookies.append({
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c.get("path", "/"),
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "secure": bool(c.get("secure", False)),
                    "sameSite": "Lax",
                })
            context.add_cookies(pw_cookies)
            log_step("cookies", f"Injected {len(pw_cookies)} XHS cookies from Chrome")
        except Exception as e:
            log_step("cookies_failed", f"Could not load cookies: {e}")

        page = context.new_page()

        log_step("start", f"Research topic: {topic}")

        # Navigate to XHS
        page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        log_step("open_xhs", "Opened xiaohongshu.com")

        # Take a screenshot for reference
        page.screenshot(path=str(OUTPUT_DIR / "00_homepage.png"))

        if keywords is None:
            keywords = generate_keywords(topic)
        log_step("keywords", f"{len(keywords)} keywords: {keywords}")

        for keyword in keywords:
            log_step("search", keyword)

            # Navigate to search URL directly (more reliable than typing)
            search_url = f"https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_search_result_notes"
            page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(5000)  # Wait for cards to render

            # Screenshot search results
            page.screenshot(path=str(OUTPUT_DIR / f"search_{keyword}.png"))

            # Extract cards from DOM
            cards = extract_search_cards(page)
            log_step("cards_extracted", f"{len(cards)} cards from DOM")

            if not cards:
                # Retry once
                page.wait_for_timeout(5000)
                cards = extract_search_cards(page)
                log_step("cards_retry", f"{len(cards)} cards after retry")

            if not cards:
                continue

            # Print what we got
            for c in cards[:5]:
                print(f"    → {c.get('title', '?')[:40]} | {c.get('author', '?')} | {c.get('likes', '?')} likes")

            # LLM picks best notes
            picks = pick_notes(cards, topic, 2)
            log_step("picked", f"{len(picks)} notes to examine")

            for card in picks:
                title = card.get("title", f"card_{card.get('position', '?')}")
                if title in seen_titles:
                    log_step("skip_dup", f"Already examined: {title[:40]}")
                    continue
                seen_titles.add(title)

                log_step("open_note", title[:60])

                # Navigate to note by URL if available
                link = card.get("link", "")
                if link:
                    page.goto(link, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)
                else:
                    log_step("no_link", "No link available, skipping")
                    continue

                # Extract content from DOM
                note = extract_note_content(page)
                note["source_keyword"] = keyword

                # Screenshot note
                page.screenshot(path=str(OUTPUT_DIR / f"note_{title[:20]}.png"))

                if note.get("title"):
                    log_step("extracted", f"\"{note['title']}\" by {note.get('author', '?')}, "
                             f"{note.get('likes', '?')} likes, {note.get('image_count', '?')} imgs")
                else:
                    log_step("extract_partial", f"Title empty, content length: {len(note.get('content', ''))}")

                # Extract comments from DOM
                comments = extract_comments(page)
                note["comments"] = comments
                log_step("comments", f"{len(comments)} comments from DOM")

                # Image URLs (already in note from extraction)
                log_step("images", f"{len(note.get('image_urls', []))} image URLs (no vision needed)")

                all_notes.append(note)

                # Go back to search results
                page.go_back(wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(2000)

        browser.close()

    # Synthesize
    elapsed_collect = time.time() - t0
    log_step("synthesize", f"Data collection took {elapsed_collect:.1f}s. Generating report...")

    synthesis = ""
    if all_notes:
        synthesis = synthesize(topic, keywords, all_notes)

    elapsed_total = time.time() - t0
    log_step("done", f"Total: {elapsed_total:.1f}s, {len(all_notes)} notes")

    # Save report
    report = {
        "topic": topic,
        "keywords": keywords,
        "notes": all_notes,
        "synthesis": synthesis,
        "timing": {
            "data_collection_seconds": round(elapsed_collect, 1),
            "total_seconds": round(elapsed_total, 1),
        },
        "log": log_entries,
    }

    with open(OUTPUT_DIR / "report.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print summary
    print(f"\n{'='*60}")
    print(f"DOM Research Complete")
    print(f"{'='*60}")
    print(f"Topic: {topic}")
    print(f"Keywords: {keywords}")
    print(f"Notes: {len(all_notes)}")
    print(f"Data collection: {elapsed_collect:.1f}s")
    print(f"Total time: {elapsed_total:.1f}s")
    print(f"LLM calls: {2 + len(keywords)} (keywords + {len(keywords)} picks + synthesis)")
    print(f"\nNotes collected:")
    for i, n in enumerate(all_notes):
        print(f"  {i+1}. {n.get('title', '?')[:50]} — {n.get('author', '?')} ({n.get('likes', '?')} likes, {n.get('image_count', '?')} imgs)")
        print(f"     Content: {n.get('content', '')[:80]}...")
        print(f"     Comments: {len(n.get('comments', []))}, Images: {len(n.get('image_urls', []))}")
        print(f"     Hashtags: {n.get('hashtags', [])[:5]}")
    print(f"\nReport saved to: {OUTPUT_DIR}/report.json")

    return report


if __name__ == "__main__":
    research(
        topic="2025春季露营装备趋势",
        keywords=["露营装备推荐", "露营好物清单"],
    )
