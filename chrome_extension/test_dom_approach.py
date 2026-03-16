"""
DOM-based XHS research — via AppleScript JS injection into real Chrome.

Equivalent to a Chrome Extension content script: runs JavaScript
directly in the user's logged-in Chrome tab. No headless browser,
no cookie issues, no anti-bot detection.

Uses the same Claude model for LLM decisions as the vision approach.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import anthropic

# Load API key
for p in [os.path.expanduser("~/.zshrc.pre-oh-my-zsh"), os.path.expanduser("~/.zshrc")]:
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                if "ANTHROPIC_API_KEY" in line and "export" in line:
                    val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    os.environ["ANTHROPIC_API_KEY"] = val
                    break

MODEL = "claude-sonnet-4-6"
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
    print(f"  [{step_count:03d}] {action}: {detail[:100]}")


def call_claude(prompt: str, max_tokens: int = 1024) -> str:
    resp = client.messages.create(
        model=MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def extract_json_from_text(text: str):
    m = re.search(r"[\[{][\s\S]*[\]}]", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


# ── Chrome AppleScript Interface ────────────────────────────────

def chrome_run_js(js_code: str) -> str:
    """Execute JavaScript in the active Chrome tab via AppleScript and return result."""
    # Escape for AppleScript string
    escaped = js_code.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    script = f'''
    tell application "Google Chrome"
        set theTab to active tab of front window
        set result to execute theTab javascript "{escaped}"
        return result
    end tell
    '''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def chrome_navigate(url: str):
    """Navigate the active Chrome tab to a URL."""
    script = f'''
    tell application "Google Chrome"
        set URL of active tab of front window to "{url}"
    end tell
    '''
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def chrome_get_url() -> str:
    """Get the current URL of the active Chrome tab."""
    script = '''
    tell application "Google Chrome"
        return URL of active tab of front window
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def chrome_activate():
    """Bring Chrome to front."""
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], timeout=5)


# ── DOM Extraction (runs in real Chrome tab) ────────────────────

def detect_state() -> str:
    url = chrome_get_url()
    if "/search_result" in url or "keyword=" in url:
        return "search_results"
    if "/explore/" in url:
        return "note_detail"
    if "/user/profile/" in url:
        return "profile_page"
    if url.rstrip("/").endswith("xiaohongshu.com") or url.endswith("/explore"):
        return "homepage"
    return "unknown"


def extract_search_cards() -> list[dict]:
    js = r"""
    (function() {
        // XHS search results: note cards in the feed
        const cards = document.querySelectorAll('section.note-item, [data-note-id]');
        const results = [];
        for (const card of cards) {
            const titleEl = card.querySelector('.title, a.title span');
            const authorEl = card.querySelector('.author-wrapper .name, .author .name');
            const likesEl = card.querySelector('.like-wrapper .count, .count');
            const linkEl = card.querySelector('a[href*="/explore/"], a[href*="/search_result/"]') || card.closest('a');
            const title = (titleEl && titleEl.textContent.trim()) || '';
            const author = (authorEl && authorEl.textContent.trim()) || '';
            const likes = (likesEl && likesEl.textContent.trim()) || '';
            const link = (linkEl && linkEl.href) || '';
            if (title || link) results.push({title, author, likes, link, position: results.length});
        }
        return JSON.stringify(results);
    })()
    """
    raw = chrome_run_js(js)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def extract_note_content() -> dict:
    js = r"""
    (function() {
        function q(sel) {
            for (const s of sel.split(',')) {
                const el = document.querySelector(s.trim());
                if (el && el.textContent.trim()) return el.textContent.trim();
            }
            return '';
        }
        const n = {};
        n.title = q('#detail-title, .note-content .title, h1');
        n.author = q('.author-container .username, .info .username, .author-wrapper .username');
        n.content = q('#detail-desc, .note-content .desc, .note-text .content, .note-scroller .content');
        n.date = q('.note-content .date, .bottom-container .date');
        n.likes = q('.like-wrapper .count, .engage-bar .like .count, [data-type="like"] .count');
        n.favorites = q('.collect-wrapper .count, .engage-bar .collect .count');
        n.comments_count = q('.chat-wrapper .count, .engage-bar .chat .count');
        n.hashtags = [...document.querySelectorAll('.hash-tag a, a[href*="/page/topics/"], .tag')]
            .map(el => el.textContent.trim()).filter(Boolean);
        const imgs = document.querySelectorAll('.carousel-image img, .slide img, .swiper-slide img, .note-slider img');
        n.image_urls = [...imgs].map(i => i.src || i.dataset.src || '').filter(Boolean);
        n.image_count = n.image_urls.length || 1;
        const authorLink = document.querySelector('.author-container a[href*="/user/"], .info a[href*="/user/profile/"]');
        n.author_url = (authorLink && authorLink.href) || '';
        return JSON.stringify(n);
    })()
    """
    raw = chrome_run_js(js)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def extract_comments() -> list[dict]:
    js = r"""
    (function() {
        const items = document.querySelectorAll('.comment-item, .parent-comment, .comment-inner');
        const results = [...items].map(item => {
            const user = item.querySelector('.name, .user-name, .nickname');
            const text = item.querySelector('.content, .comment-text, .note-text');
            const likes = item.querySelector('.like .count');
            return {
                username: (user && user.textContent.trim()) || '',
                text: (text && text.textContent.trim()) || '',
                likes: (likes && likes.textContent.trim()) || '',
            };
        }).filter(c => c.text);
        return JSON.stringify(results);
    })()
    """
    raw = chrome_run_js(js)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def click_note_card(index: int):
    """Click the nth note card in search results."""
    js = f"""
    (function() {{
        const cards = document.querySelectorAll('section.note-item, [data-note-id]');
        if (cards[{index}]) {{
            const link = cards[{index}].querySelector('a') || cards[{index}].closest('a');
            if (link) {{ link.click(); return 'clicked'; }}
            cards[{index}].click();
            return 'clicked_card';
        }}
        return 'not_found';
    }})()
    """
    return chrome_run_js(js)


def close_note_detail():
    """Close note detail overlay."""
    js = r"""
    (function() {
        const btn = document.querySelector('.close-circle, .note-detail-mask .close, [aria-label="close"]');
        if (btn) { btn.click(); return 'closed'; }
        document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', keyCode: 27, bubbles: true}));
        return 'escape';
    })()
    """
    return chrome_run_js(js)


# ── LLM Decisions (identical to vision approach) ────────────────

def generate_keywords(topic: str) -> list[str]:
    raw = call_claude(
        f"I want to research '{topic}' on Xiaohongshu (小红书). "
        f"Generate 3-5 Chinese search keywords. Return only a JSON array of strings.",
        256,
    )
    return extract_json_from_text(raw) or [topic]


def pick_notes(cards: list[dict], topic: str, max_picks: int) -> list[dict]:
    if len(cards) <= max_picks:
        return cards
    raw = call_claude(
        f"I'm researching '{topic}' on Xiaohongshu.\n"
        f"Note cards:\n{json.dumps(cards, ensure_ascii=False, indent=1)}\n\n"
        f"Pick the {max_picks} most relevant/interesting. Return JSON array of selected cards.",
        2048,
    )
    picks = extract_json_from_text(raw)
    return (picks if isinstance(picks, list) else cards)[:max_picks]


def synthesize(topic, keywords, notes):
    summaries = [{
        "title": n.get("title", ""), "author": n.get("author", ""),
        "likes": n.get("likes", ""), "content_preview": n.get("content", "")[:200],
        "hashtags": n.get("hashtags", []), "image_count": n.get("image_count", 0),
        "comments_count": n.get("comments_count", ""), "keyword": n.get("source_keyword", ""),
    } for n in notes]
    return call_claude(
        f"I researched '{topic}' on Xiaohongshu:\n"
        f"Keywords: {json.dumps(keywords, ensure_ascii=False)}\n"
        f"Notes:\n{json.dumps(summaries, ensure_ascii=False, indent=1)}\n\n"
        f"Synthesize into 2-3 paragraphs in Chinese. Focus on trends, themes, insights.",
        2048,
    )


# ── Main Research Flow ──────────────────────────────────────────

def research(topic: str, keywords: list[str] | None = None):
    t0 = time.time()
    all_notes = []
    seen_titles = set()

    chrome_activate()
    log_step("start", f"Research topic: {topic}")

    # Ensure we're on XHS
    url = chrome_get_url()
    if "xiaohongshu.com" not in url:
        chrome_navigate("https://www.xiaohongshu.com")
        time.sleep(5)
    log_step("page", f"Current URL: {chrome_get_url()}")

    if keywords is None:
        keywords = generate_keywords(topic)
    log_step("keywords", f"{len(keywords)} keywords: {keywords}")

    for keyword in keywords:
        log_step("search", keyword)

        # Navigate to search URL
        search_url = f"https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_search_result_notes"
        chrome_navigate(search_url)
        time.sleep(5)

        # Extract cards
        cards = extract_search_cards()
        if not cards:
            time.sleep(5)
            cards = extract_search_cards()
        log_step("cards", f"{len(cards)} cards extracted from DOM")

        for c in cards[:5]:
            print(f"    → {c.get('title', '?')[:40]} | {c.get('author', '?')} | {c.get('likes', '?')}")

        if not cards:
            continue

        picks = pick_notes(cards, topic, 2)
        log_step("picked", f"{len(picks)} notes to examine")

        for card in picks:
            title = card.get("title", "")
            if not title or title in seen_titles:
                if title:
                    log_step("skip_dup", f"Already: {title[:40]}")
                continue
            seen_titles.add(title)

            log_step("open_note", title[:60])

            # Click the card (by index in search results)
            idx = card.get("position", 0)
            click_note_card(idx)
            time.sleep(3)

            # Extract content
            note = extract_note_content()
            note["source_keyword"] = keyword
            log_step("extracted",
                     f"title='{note.get('title', '')[:30]}' author='{note.get('author', '')}' "
                     f"likes={note.get('likes', '?')} imgs={note.get('image_count', '?')}")

            # Content preview
            content = note.get("content", "")
            if content:
                log_step("content_preview", content[:100])

            # Comments
            comments = extract_comments()
            note["comments"] = comments
            log_step("comments", f"{len(comments)} comments")

            # Images
            log_step("images", f"{len(note.get('image_urls', []))} image URLs")

            all_notes.append(note)

            # Close note / go back
            close_note_detail()
            time.sleep(1)
            # If still on note page, navigate back
            if "/explore/" in chrome_get_url():
                chrome_navigate(search_url)
                time.sleep(3)

    elapsed_collect = time.time() - t0
    log_step("synthesize", f"Data collection: {elapsed_collect:.1f}s. Generating report...")

    synthesis = ""
    if all_notes:
        synthesis = synthesize(topic, keywords, all_notes)

    elapsed_total = time.time() - t0
    log_step("done", f"Total: {elapsed_total:.1f}s, {len(all_notes)} notes")

    report = {
        "topic": topic, "keywords": keywords, "notes": all_notes,
        "synthesis": synthesis,
        "timing": {"data_collection_s": round(elapsed_collect, 1), "total_s": round(elapsed_total, 1)},
        "log": log_entries,
    }
    with open(OUTPUT_DIR / "report.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"DOM Research Complete — {elapsed_total:.1f}s total")
    print(f"{'='*60}")
    print(f"Notes: {len(all_notes)}, LLM calls: {2 + len(keywords)} text-only")
    for i, n in enumerate(all_notes):
        print(f"  {i+1}. {n.get('title', '?')[:50]} — {n.get('author', '?')} ({n.get('likes', '?')} likes)")
        print(f"     Content: {n.get('content', '')[:80]}...")
        print(f"     Comments: {len(n.get('comments', []))}, Images: {len(n.get('image_urls', []))}")
    print(f"\nReport: {OUTPUT_DIR}/report.json")

    return report


if __name__ == "__main__":
    research(
        topic="2025春季露营装备趋势",
        keywords=["露营装备推荐", "露营好物清单"],
    )
