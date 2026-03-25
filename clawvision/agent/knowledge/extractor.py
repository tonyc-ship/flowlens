"""LLM-based knowledge extractor — distills observer events into site knowledge.

Pulls raw observer events from the Chrome extension, then uses Claude to extract:
  1. Interaction primitives — how to interact with elements on this site
  2. Navigation flows — state machine of page transitions
  3. Session intents — what the user was trying to accomplish

Output is written to YAML files under a per-site knowledge directory.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..media import MediaProcessor


def _parse_json_response(raw: str) -> dict | list:
    """Parse JSON from an LLM response, handling markdown code blocks."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(cleaned)


def _summarize_events(events: list[dict]) -> str:
    """Build a compact text summary of observer events for LLM consumption."""
    if not events:
        return "(no events)"

    t0 = events[0].get("ts", 0)
    lines = []
    for e in events:
        t = round((e.get("ts", 0) - t0) / 1000, 1)
        etype = e["type"]
        pt = e.get("page_type", "")

        if etype == "session_start":
            lines.append(f"[{t}s] SESSION_START")
        elif etype == "click":
            el = e.get("element", {})
            path = el.get("css_path", "?")
            text = el.get("text_preview", "")[:50]
            tag = el.get("tag", "?")
            cont = e.get("container", {})
            cont_info = f" in <{cont.get('tag', '')}>{'.' + '.'.join(cont.get('classes', [])) if cont.get('classes') else ''}" if cont else ""
            text_info = f' "{text}"' if text else ""
            lines.append(f"[{t}s] CLICK <{tag}> {path}{cont_info}{text_info} [{pt}]")
        elif etype == "scroll":
            container = e.get("container", "window")
            lines.append(f"[{t}s] SCROLL {e.get('direction', '?')} to {e.get('scroll_pct', '?')}% in {container} [{pt}]")
        elif etype == "navigate":
            lines.append(f"[{t}s] NAVIGATE -> {e.get('to_page_type', '?')} ({e.get('to_url', '')[:60]})")
        elif etype == "page_leave":
            dur = round(e.get("duration_ms", 0) / 1000, 1)
            lines.append(f"[{t}s] LEAVE {e.get('from_page_type', '?')} after {dur}s")
        elif etype == "overlay_open":
            lines.append(f"[{t}s] OVERLAY_OPEN [{pt}]")
        elif etype == "overlay_close":
            lines.append(f"[{t}s] OVERLAY_CLOSE [{pt}]")
        elif etype == "page_structure":
            st = e.get("structure", {})
            scrollables = st.get("scrollable_containers", [])
            scroll_info = f" scrollable: {', '.join(s['path'] for s in scrollables[:3])}" if scrollables else ""
            lines.append(f"[{t}s] PAGE_STRUCTURE {st.get('page_type', '?')} url={st.get('url_pattern', '?')}{scroll_info}")

    return "\n".join(lines)


def _summarize_click_patterns(events: list[dict]) -> str:
    """Aggregate click events by CSS path with frequency counts."""
    from collections import Counter

    clicks = [e for e in events if e.get("type") == "click" and e.get("element")]
    path_counts: dict[str, dict] = {}

    for c in clicks:
        el = c["element"]
        path = el.get("css_path", "?")
        if path not in path_counts:
            path_counts[path] = {"count": 0, "tag": el.get("tag", "?"),
                                 "classes": el.get("classes", []), "examples": []}
        path_counts[path]["count"] += 1
        text = el.get("text_preview", "")[:40]
        if text and text not in path_counts[path]["examples"]:
            path_counts[path]["examples"].append(text)

    lines = []
    for path, info in sorted(path_counts.items(), key=lambda x: -x[1]["count"]):
        examples = ", ".join(f'"{e}"' for e in info["examples"][:2])
        lines.append(f"  {info['count']}x <{info['tag']}> {path}" +
                     (f"  examples: {examples}" if examples else ""))
    return "\n".join(lines) or "(no clicks)"


class KnowledgeExtractor:
    """Extracts site knowledge from observer events using LLM."""

    def __init__(self, media: MediaProcessor):
        self.media = media

    def extract_all(
        self,
        events: list[dict],
        site_name: str,
        output_dir: str | Path,
    ) -> dict:
        """Run all extraction passes and write knowledge files.

        Returns a summary dict of what was extracted.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = {}
        t0 = time.time()

        # 1. Interaction primitives
        print(f"  [extractor] Extracting interaction primitives...")
        interactions = self._extract_interactions(events, site_name)
        (output_dir / "interactions.json").write_text(
            json.dumps(interactions, ensure_ascii=False, indent=2))
        results["interactions"] = len(interactions.get("primitives", []))

        # 2. Navigation flows
        print(f"  [extractor] Extracting navigation flows...")
        navigation = self._extract_navigation(events, site_name)
        (output_dir / "navigation.json").write_text(
            json.dumps(navigation, ensure_ascii=False, indent=2))
        results["page_types"] = len(navigation.get("page_types", []))
        results["transitions"] = len(navigation.get("transitions", []))

        # 3. Session intents
        print(f"  [extractor] Inferring session intents...")
        intents = self._extract_intents(events, site_name)
        (output_dir / "intents.json").write_text(
            json.dumps(intents, ensure_ascii=False, indent=2))
        results["sessions_analyzed"] = len(intents.get("sessions", []))

        # 4. Write catalog (progressive disclosure index)
        catalog = self._build_catalog(site_name, interactions, navigation, intents, events)
        (output_dir / "catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2))

        results["total_time_s"] = round(time.time() - t0, 1)
        print(f"  [extractor] Done in {results['total_time_s']}s")
        return results

    def _extract_interactions(self, events: list[dict], site_name: str) -> dict:
        """Extract interaction primitives from click/scroll patterns."""
        click_summary = _summarize_click_patterns(events)
        event_timeline = _summarize_events(events)

        prompt = f"""You are analyzing user interaction data from a website to extract reusable automation knowledge.

**Site:** {site_name}
**Total events:** {len(events)}

**Click patterns (aggregated by CSS path):**
{click_summary}

**Full event timeline:**
{event_timeline}

From these observations, extract **interaction primitives** — the atomic actions a browser agent would need to automate this site. For each primitive, provide:
- A short name (e.g. "open_note", "expand_replies")
- What it does
- The CSS selector to target
- The action type (click, scroll, type, hover)
- Any text patterns that identify the element (e.g. "展开.*条回复")
- How many times it was observed
- Confidence level (high/medium/low based on observation count)

Also note any scroll-based interactions you can infer (scrolling through feed, scrolling in comment panels, etc.).

Return JSON:
{{
  "site": "{site_name}",
  "primitives": [
    {{
      "name": "short_name",
      "description": "what this action does",
      "selector": "css selector",
      "action": "click|scroll|type|hover",
      "text_pattern": "regex pattern if applicable, or null",
      "triggers": "what happens after (e.g. overlay_open, page_navigate, content_expand)",
      "observed_count": 3,
      "confidence": "high|medium|low",
      "notes": "any additional context"
    }}
  ]
}}

Return ONLY the JSON."""

        raw = self.media.call_text(prompt, max_tokens=4096)
        try:
            return _parse_json_response(raw)
        except (json.JSONDecodeError, ValueError):
            return {"site": site_name, "primitives": [], "raw_error": raw[:500]}

    def _extract_navigation(self, events: list[dict], site_name: str) -> dict:
        """Extract navigation flow graph from events."""
        event_timeline = _summarize_events(events)

        # Also gather page_structure events for URL patterns
        structures = [e for e in events if e.get("type") == "page_structure"]
        page_info = []
        for s in structures:
            st = s.get("structure", {})
            pt = st.get("page_type", "unknown")
            url = st.get("url_pattern", "")
            scrollables = st.get("scrollable_containers", [])
            page_info.append(f"  {pt}: url={url}, scrollable={[s['path'] for s in scrollables[:3]]}")
        page_info_text = "\n".join(page_info) if page_info else "(none captured)"

        prompt = f"""You are analyzing navigation patterns on a website to build a state machine.

**Site:** {site_name}

**Page types observed:**
{page_info_text}

**Full event timeline:**
{event_timeline}

Extract the **navigation flow graph**:
1. All page types and their URL patterns
2. How the site uses overlays vs. full page navigation (SPA behavior)
3. Transitions between page types (what action triggers each transition)
4. Scrollable containers on each page type

Return JSON:
{{
  "site": "{site_name}",
  "spa_behavior": "description of how the site handles navigation (overlays, history API, etc.)",
  "page_types": [
    {{
      "name": "page_type_name",
      "url_pattern": "/path/pattern",
      "description": "what this page shows",
      "is_overlay": false,
      "scrollable_containers": ["selector1", "selector2"]
    }}
  ],
  "transitions": [
    {{
      "from": "page_type_A",
      "to": "page_type_B",
      "action": "what the user does",
      "method": "overlay_open|overlay_close|navigation|history_push",
      "observed_count": 3
    }}
  ]
}}

Return ONLY the JSON."""

        raw = self.media.call_text(prompt, max_tokens=4096)
        try:
            return _parse_json_response(raw)
        except (json.JSONDecodeError, ValueError):
            return {"site": site_name, "page_types": [], "transitions": [], "raw_error": raw[:500]}

    def _extract_intents(self, events: list[dict], site_name: str) -> dict:
        """Infer user intentions from session timeline."""
        # Split events into sessions (by session_start markers)
        sessions: list[list[dict]] = []
        current: list[dict] = []
        for e in events:
            if e.get("type") == "session_start" and current:
                sessions.append(current)
                current = []
            current.append(e)
        if current:
            sessions.append(current)

        event_timeline = _summarize_events(events)

        prompt = f"""You are analyzing browsing sessions to infer what the user intended to do.

**Site:** {site_name}
**Sessions:** {len(sessions)}
**Total events:** {len(events)}

**Full event timeline:**
{event_timeline}

For each session, infer the user's **intent** by analyzing:
- Did they search for something specific, or browse the feed?
- How long did they spend on each note? (short = skimming, long = deep reading)
- Did they interact deeply (expand comments, view images) or just glance?
- Did their focus shift mid-session (started researching X, got distracted by Y)?
- What content types did they engage with (video, image posts, comments)?

Intent categories:
- "casual_browsing" — scrolling feed for entertainment
- "topic_research" — searching/exploring a specific topic
- "creator_analysis" — looking at a specific creator's content
- "deep_reading" — spending significant time on specific notes
- "serendipitous_discovery" — started with one intent, shifted to another
- "content_creation_research" — looking at what performs well for inspiration

Return JSON:
{{
  "site": "{site_name}",
  "sessions": [
    {{
      "session_index": 0,
      "duration_s": 600,
      "event_count": 38,
      "primary_intent": "casual_browsing",
      "confidence": 0.8,
      "reasoning": "why you inferred this intent",
      "sub_intents": [
        {{
          "type": "deep_reading",
          "evidence": "spent 113s on second note, expanded 5 comment threads",
          "time_range": "208s-321s"
        }}
      ],
      "content_interests": ["topic1", "topic2"],
      "engagement_level": "high|medium|low"
    }}
  ]
}}

Return ONLY the JSON."""

        raw = self.media.call_text(prompt, max_tokens=4096)
        try:
            return _parse_json_response(raw)
        except (json.JSONDecodeError, ValueError):
            return {"site": site_name, "sessions": [], "raw_error": raw[:500]}

    def _build_catalog(
        self,
        site_name: str,
        interactions: dict,
        navigation: dict,
        intents: dict,
        events: list[dict],
    ) -> dict:
        """Build progressive disclosure catalog — the only file always loaded."""
        primitives = interactions.get("primitives", [])
        page_types = navigation.get("page_types", [])
        transitions = navigation.get("transitions", [])
        sessions = intents.get("sessions", [])

        # Level 0 summary — always in context
        prim_names = [p["name"] for p in primitives]
        page_names = [p["name"] for p in page_types]

        return {
            "site": site_name,
            "knowledge_version": time.strftime("%Y-%m-%d"),
            "observation_stats": {
                "total_events": len(events),
                "sessions_analyzed": len(sessions),
                "observation_period": {
                    "first": events[0].get("ts") if events else None,
                    "last": events[-1].get("ts") if events else None,
                },
            },
            "summary": (
                f"{site_name} site knowledge extracted from {len(events)} observed events "
                f"across {len(sessions)} session(s). "
                f"Page types: {', '.join(page_names)}. "
                f"Interaction primitives: {', '.join(prim_names)}. "
                f"SPA behavior: {navigation.get('spa_behavior', 'unknown')}."
            ),
            "sections": {
                "interactions": {
                    "file": "interactions.json",
                    "count": len(primitives),
                    "names": prim_names,
                },
                "navigation": {
                    "file": "navigation.json",
                    "page_types": page_names,
                    "transition_count": len(transitions),
                },
                "intents": {
                    "file": "intents.json",
                    "sessions": len(sessions),
                },
            },
        }
