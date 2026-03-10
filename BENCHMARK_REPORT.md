# ClawVision Benchmark Report

Date: 2026-03-10
Platform: macOS (Apple Silicon), Chrome on separate Space
Model: Claude Sonnet 4.6 (`claude-sonnet-4-6`)

## Summary

| Metric | Value |
|--------|-------|
| Tasks | 6/6 passed |
| Total time | 238s (~4 min) |
| Total API calls | 13 |
| Estimated API cost | ~$0.30 |

## Task Results

### Task 1: Homepage Analysis (Easy) - PASS

| Metric | Value |
|--------|-------|
| Time | 20.9s |
| API calls | 1 |

**What it does**: Capture XHS homepage, identify page structure.

**Quality**: Excellent.
- Correctly identified all 11 category tabs
- Extracted 7 visible note cards with titles, authors, likes
- Detected login state (3 unread notifications)
- Distinguished between fully visible (4) and partially visible (4) cards

**Score: 9/10** — Minor: some partial cards had incomplete data (expected).

---

### Task 2: Precise Note Card Crop (Easy) - PASS

| Metric | Value |
|--------|-------|
| Time | 13.8s |
| API calls | 2 |

**What it does**: Locate first note card, crop it precisely, verify crop quality.

**Quality**: Poor crop precision.
- LLM located the note at x=22%, y=38% — this is roughly correct area
- But the crop captured only the cover image, missing title/author/likes
- The percentage-based coordinates lack pixel precision

**Score: 4/10** — Crop boundary detection is the weakest point. LLM coordinates are approximate (~50px error margin on 2704px image).

**Root cause**: LLM returns center+size as percentages. For a note card that's ~500px wide, a 2% error = 54px, which easily cuts off the title area below the image.

---

### Task 3: Search & Extract (Medium) - PASS

| Metric | Value |
|--------|-------|
| Time | 27.6s |
| API calls | 1 |

**What it does**: Navigate to search "露营装备", extract structured note data.

**Quality**: Excellent.
- URL-based navigation worked perfectly (bypasses cross-Space keyboard issues)
- Correctly extracted search results with titles, authors, likes
- Identified filter tabs (综合/二手闲置/高端/租赁平台/广州/重庆...)

**Score: 9/10** — Note: first attempt (keyboard-based search) failed because Chrome was on another Space. URL navigation is the reliable approach.

---

### Task 4: Scroll & Collect (Medium) - PASS

| Metric | Value |
|--------|-------|
| Time | 45.6s |
| API calls | 3 |

**What it does**: Scroll through search results 3 times, extract notes at each position.

**Quality**: Good.
- Successfully captured 3 different scroll positions
- Extracted notes from each viewport
- Image resize fix prevented the 5MB API limit error

**Score: 7/10** — Dedup across scroll positions not implemented (same notes may appear in overlapping viewports).

---

### Task 5: Note Detail Extraction (Hard) - PASS

| Metric | Value |
|--------|-------|
| Time | 38.8s |
| API calls | 2 |

**What it does**: Click first note in search results, extract full detail page.

**Quality**: Excellent.
- Successfully clicked the correct note card
- Extracted comprehensive detail:
  - Title: "新手党露营装备指南，20个好物建议&不建议"
  - Author: "轻户外｜钱十安"
  - Full text content + image descriptions (5 slides)
  - 10 hashtags
  - Engagement: 329 likes, 278 favorites, 19 comments
  - Top comments with usernames
  - Structured "建议 vs 不建议" comparison from the note images

**Score: 9/10** — This is the strongest result. The LLM extracted structured data from a complex multi-image note that would be very hard to get via DOM scraping.

---

### Task 6: Multi-Topic Research Report (Hard) - PASS

| Metric | Value |
|--------|-------|
| Time | 91.3s |
| API calls | 4 |

**What it does**: Compare "露营装备" vs "徒步路线", generate research report.

**Quality**: Excellent.
- Successfully navigated to both search topics
- Generated a comprehensive comparison report in Chinese (~2000 chars):
  - Engagement metrics comparison table
  - Content type analysis
  - High-engagement content patterns (4 patterns identified)
  - Actionable creator recommendations for each topic

**Score: 9/10** — Report quality is publication-ready. This demonstrates the full value proposition.

---

## Issue Log & Fixes Applied During Benchmark

| # | Issue | Root Cause | Fix | Impact |
|---|-------|-----------|-----|--------|
| 1 | Search didn't execute | Chrome on another Space, keyboard input went to wrong window | Use `open_url()` instead of keyboard shortcuts | Critical |
| 2 | Screenshot >5MB API error | Retina 2704x1688 PNG too large | Auto-resize to 1568px max + JPEG fallback in `_image_to_base64()` | Critical |
| 3 | pyautogui failsafe crash | Mouse coordinates outside current display bounds | Avoid cross-Space mouse operations; use URL navigation | Critical |
| 4 | Note card crop imprecise | LLM percentage coordinates have ~2-5% error | Not yet fixed — needs local CV model (YOLO/OWLv2) | Quality |

## Performance Profile

```
                        Time (s)    API Calls    Quality
Task 1 (Homepage)        20.9          1          9/10
Task 2 (Crop)            13.8          2          4/10
Task 3 (Search)          27.6          1          9/10
Task 4 (Scroll)          45.6          3          7/10
Task 5 (Detail)          38.8          2          9/10
Task 6 (Report)          91.3          4          9/10
─────────────────────────────────────────────────────
Average                  39.7s        2.2         7.8/10
```

## Key Findings

### Strengths
1. **Page understanding is excellent** — Claude Sonnet 4.6 accurately reads Chinese UI, extracts structured data, understands context
2. **Note detail extraction is the killer feature** — extracting structured content from multi-image notes (including text within images) is something DOM scraping cannot do
3. **Research report generation is production-quality** — the LLM can synthesize visual data into actionable insights
4. **Cross-Space window capture works** — CGWindowListCreateImage captures any window regardless of Space

### Weaknesses
1. **Precise element location is poor** — LLM percentage coordinates have 2-5% error, making cropping unreliable
2. **Cross-Space input control is broken** — keyboard/mouse operations cannot target windows on other Spaces
3. **No deduplication** — scrolling collects duplicate notes across viewports
4. **Speed** — each API call takes 5-15s; local CV models would be 10-100x faster for element detection
5. **No state verification** — after clicking/navigating, we don't verify the page actually changed

## Optimization Recommendations

### P0: Fix cross-Space interaction
- **Current**: `activate_app()` + keyboard shortcuts is unreliable
- **Solution**: Use `open_url()` for all navigation (already proven reliable)
- **For clicking**: Use AppleScript `tell application "System Events"` to send clicks to specific windows, or always activate Chrome first and wait

### P1: Use local YOLO for element detection (replace LLM locate_element)
- **Current**: LLM returns approximate percentage coordinates (~50px error)
- **Solution**: Use OmniParser YOLOv8 to detect all UI elements with pixel-precise bounding boxes
- **Expected improvement**: Crop accuracy 4/10 → 9/10, speed 5s → 0.1s

### P2: Add scroll deduplication
- **Current**: Same notes appear in overlapping scroll positions
- **Solution**: Use perceptual hashing (pHash) or OCR on note titles to dedup across captures

### P3: Add state verification after actions
- **Current**: After clicking/scrolling, we blindly wait and hope
- **Solution**: Capture → analyze → verify page state changed before proceeding
- **Example**: After search, verify `is_search_results == true` before extracting

### P4: Batch image analysis
- **Current**: One API call per screenshot (13 calls for 6 tasks)
- **Solution**: Send multiple screenshots in one API call where possible (Task 4 scroll positions)
