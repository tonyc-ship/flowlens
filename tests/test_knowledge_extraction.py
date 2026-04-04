#!/usr/bin/env python3
"""Test knowledge extraction from observer data.

Connects to the Chrome extension, pulls observer events, runs LLM extraction,
and writes knowledge files.

Usage:
    python tests/test_knowledge_extraction.py
    python tests/test_knowledge_extraction.py --from-file /tmp/observer_data.json
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flowlens.core.bridge import ExtensionBridge
from flowlens.perception.media import MediaProcessor
from flowlens.reasoning.knowledge.extractor import KnowledgeExtractor
from flowlens.reasoning.knowledge.loader import KnowledgeLoader


async def pull_observer_data(port: int = 8765) -> dict:
    """Connect to extension and pull observer data."""
    bridge = ExtensionBridge(port=port)
    await bridge.start()
    try:
        await bridge.wait_for_connection(timeout=10)
        result = await bridge.send_command("get_observer_data")
        return result
    finally:
        await bridge.stop()


def main():
    parser = argparse.ArgumentParser(description="Extract site knowledge from observer data")
    parser.add_argument("--from-file", help="Load observer data from JSON file instead of extension")
    parser.add_argument("--output", default="knowledge_output", help="Output directory")
    parser.add_argument("--port", type=int, default=8765, help="Extension WebSocket port")
    args = parser.parse_args()

    # Load data
    if args.from_file:
        print(f"Loading from {args.from_file}...")
        data = json.loads(Path(args.from_file).read_text())
    else:
        print("Pulling observer data from Chrome extension...")
        data = asyncio.run(pull_observer_data(args.port))

    events = data.get("events", [])
    stats = data.get("stats", {})

    if not events:
        print("No observer events found. Browse some pages first!")
        sys.exit(1)

    # Determine site name from events
    site_names = set(e.get("site", "") for e in events if e.get("site"))
    site_name = site_names.pop() if len(site_names) == 1 else "unknown"

    print(f"\n{'=' * 60}")
    print(f"  Site: {site_name}")
    print(f"  Events: {len(events)}")
    print(f"  Sessions: {stats.get('sessions', '?')}")
    print(f"{'=' * 60}\n")

    # Run extraction
    output_dir = Path(args.output) / site_name
    media = MediaProcessor()
    extractor = KnowledgeExtractor(media)

    t0 = time.time()
    results = extractor.extract_all(events, site_name, output_dir)
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"  EXTRACTION RESULTS")
    print(f"{'=' * 60}")
    print(f"  Interaction primitives: {results.get('interactions', 0)}")
    print(f"  Page types: {results.get('page_types', 0)}")
    print(f"  Navigation transitions: {results.get('transitions', 0)}")
    print(f"  Sessions analyzed: {results.get('sessions_analyzed', 0)}")
    print(f"  Total time: {elapsed:.1f}s")
    print(f"  Output: {output_dir}/")
    print()

    # Verify with loader
    loader = KnowledgeLoader(output_dir)
    print("--- Catalog Summary (Level 0 — always in context) ---")
    print(loader.get_catalog_summary())
    print()

    # Show extracted files
    for name in loader.list_sections():
        data = loader.get_section(name)
        filepath = output_dir / f"{name}.json"
        size = filepath.stat().st_size if filepath.exists() else 0
        print(f"--- {name}.json ({size} bytes) ---")
        # Print a compact preview
        preview = json.dumps(data, ensure_ascii=False, indent=2)
        if len(preview) > 800:
            print(preview[:800] + "\n  ... (truncated)")
        else:
            print(preview)
        print()


if __name__ == "__main__":
    main()
