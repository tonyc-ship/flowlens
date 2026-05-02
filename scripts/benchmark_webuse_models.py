#!/usr/bin/env python3
"""Benchmark Sonnet vs local Qwen 9B on web-use style tasks.

Covers:
1. text-only reasoning / parsing
2. DOM-like structured state reasoning
3. screenshot understanding

Outputs a timestamped folder under task_runs/ with:
- results.json
- README.md
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flowlens.core.runtime import load_runtime_env
from flowlens.perception.media import MediaConfig, MediaProcessor


BackendName = str
ModeName = str


@dataclass
class BenchCase:
    case_id: str
    mode: ModeName
    label: str
    prompt: str
    expected: dict[str, Any]
    image_path: str | None = None
    max_tokens: int = 256
    judge: str = "exact"


@dataclass
class PerfSample:
    wall_s: float
    cpu_time_s: float
    cpu_util_pct: float
    rss_before_mb: float
    rss_after_mb: float
    rss_peak_mb: float
    threads_before: int
    threads_after: int
    threads_peak: int


class ProcessSampler:
    def __init__(self, interval_s: float = 0.05):
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_rss = 0
        self.peak_threads = 0
        self._proc = None

    def __enter__(self):
        import psutil

        self._proc = psutil.Process()
        self.peak_rss = self._proc.memory_info().rss
        self.peak_threads = self._proc.num_threads()

        def _run():
            while not self._stop.is_set():
                try:
                    self.peak_rss = max(self.peak_rss, self._proc.memory_info().rss)
                    self.peak_threads = max(self.peak_threads, self._proc.num_threads())
                except Exception:
                    pass
                time.sleep(self.interval_s)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)


def _mb(raw: float) -> float:
    return round(raw / (1024 * 1024), 1)


def _json_only_prompt(schema_text: str, task_text: str) -> str:
    return (
        f"{task_text}\n\n"
        "Return JSON only. No markdown fences. No extra commentary.\n"
        f"JSON schema:\n{schema_text}\n"
    )


def build_cases() -> list[BenchCase]:
    xhs_search_img = (
        "/Users/tonychong/Library/Application Support/com.flowlens.desktop/task_runs/"
        "app/task-1775014014696/topic_research_话题研究_qwen_3_5_9b_20260401_112655/"
        "workflow/screenshots/search_1_qwen 3.5 9b.png"
    )
    chat_typed_img = (
        "/Users/tonychong/Library/Application Support/com.flowlens.desktop/task_runs/"
        "multi_chat/chatbots-1774667863760/chatgpt_02_text_entered.png"
    )
    chat_generating_img = (
        "/Users/tonychong/Library/Application Support/com.flowlens.desktop/task_runs/"
        "multi_chat/chatbots-1774667863760/chatgpt_03_generating.png"
    )

    return [
        BenchCase(
            case_id="text_xhs_task_parse",
            mode="text",
            label="Parse XHS research task request",
            prompt=_json_only_prompt(
                '{"task_kind":"topic_research|creator_growth_breakdown|unknown","site":"xhs|chat|unknown","topic":"string","needs_login":true,"next_action":"string"}',
                'User request: "帮我研究小红书上的露营装备，看看高互动内容模式和用户评论里最常见的需求。"',
            ),
            expected={
                "task_kind": "topic_research",
                "site": "xhs",
                "topic_contains": "露营装备",
                "needs_login": True,
                "next_action_any": ["search", "搜索"],
            },
        ),
        BenchCase(
            case_id="text_chat_fanout_parse",
            mode="text",
            label="Parse ask-all-chatbots request",
            prompt=_json_only_prompt(
                '{"task_kind":"fanout_chatbots|unknown","targets":["chatgpt","gemini","claude"],"question":"string","needs_visible_windows":true}',
                'User request: "同时问 ChatGPT、Gemini、Claude：Qwen3.5-9B 和 Claude Sonnet 4.6 在 coding 上怎么选？"',
            ),
            expected={
                "task_kind": "fanout_chatbots",
                "targets": ["chatgpt", "gemini", "claude"],
                "question_contains": "Qwen3.5-9B",
                "needs_visible_windows": True,
            },
        ),
        BenchCase(
            case_id="text_xhs_micro_synthesis",
            mode="text",
            label="Summarize XHS note snippets into structured insight",
            prompt=_json_only_prompt(
                '{"content_patterns":["3 short bullets"],"user_needs":["3 short bullets"],"unknowns":["2 short bullets"]}',
                (
                    "You are summarizing three Xiaohongshu notes about camping.\n"
                    "Sample 1: 标题《新手党露营装备指南》，正文强调装备清单、哪些值得买、哪些不建议买，评论里大量追问价格和替代品。\n"
                    "Sample 2: 标题《北京人自己的阿勒泰》，正文强调地点风景很出片，评论里大量追问精确位置、停车和明火限制。\n"
                    "Sample 3: 标题《对露营祛魅了》，正文强调真实体验和踩坑，评论里有人质疑种草内容太假，也有人说还是想去试试。\n"
                ),
            ),
            expected={
                "content_patterns_any": ["装备清单", "地点风景", "真实体验"],
                "user_needs_any": ["价格", "停车", "明火", "位置"],
                "unknowns_any": ["样本", "地域", "转化", "泛化", "真实性"],
            },
            judge="synthesis",
            max_tokens=512,
        ),
        BenchCase(
            case_id="dom_xhs_search_next_action",
            mode="dom",
            label="Decide next action from XHS search DOM state",
            prompt=_json_only_prompt(
                '{"action":"open_note|scroll|refine_search|abort","target_note_id":"string|null","reason":"string"}',
                (
                    "DOM state:\n"
                    "{\n"
                    '  "page_state":"search_results",\n'
                    '  "input_keyword":"露营装备",\n'
                    '  "active_filter":"全部",\n'
                    '  "card_count": 16,\n'
                    '  "visible_cards":[\n'
                    '    {"note_id":"note_a","title":"新手露营装备清单","likes":"231","favorites":"1180"},\n'
                    '    {"note_id":"note_b","title":"露营拍照姿势","likes":"502","favorites":"120"},\n'
                    '    {"note_id":"note_c","title":"超轻量过夜露营装备","likes":"188","favorites":"910"}\n'
                    "  ]\n"
                    "}\n"
                    "Goal: start a breadth-first research run focused on high-save practical content.\n"
                ),
            ),
            expected={
                "action": "open_note",
                "target_note_id": "note_a",
            },
        ),
        BenchCase(
            case_id="dom_chat_submit_ready",
            mode="dom",
            label="Decide submit action from chatbot DOM state",
            prompt=_json_only_prompt(
                '{"action":"submit|keep_typing|wait","method":"enter|button|none","reason":"string"}',
                (
                    "DOM state:\n"
                    "{\n"
                    '  "site":"chatgpt",\n'
                    '  "composer_text":"Reply with exactly READY.",\n'
                    '  "send_button_enabled":true,\n'
                    '  "generating":false,\n'
                    '  "composer_visible":true\n'
                    "}\n"
                    "Choose the next action.\n"
                ),
            ),
            expected={
                "action": "submit",
                "method_any": ["enter", "button"],
            },
        ),
        BenchCase(
            case_id="dom_chat_generating_wait",
            mode="dom",
            label="Decide wait action from generating DOM state",
            prompt=_json_only_prompt(
                '{"action":"submit|keep_typing|wait","reason":"string"}',
                (
                    "DOM state:\n"
                    "{\n"
                    '  "site":"chatgpt",\n'
                    '  "composer_text":"",\n'
                    '  "send_button_enabled":false,\n'
                    '  "generating":true,\n'
                    '  "response_tokens_visible":true\n'
                    "}\n"
                    "Choose the next action.\n"
                ),
            ),
            expected={
                "action": "wait",
            },
        ),
        BenchCase(
            case_id="vision_xhs_search_state",
            mode="vision",
            label="Classify XHS search screenshot",
            image_path=xhs_search_img,
            prompt=_json_only_prompt(
                '{"page_state":"search_results|homepage|note_detail|unknown","has_search_results":true,"visible_query":"string","next_action":"open_note|scroll|retry_search|abort"}',
                "Look at this Xiaohongshu screenshot and classify the current state for a web-use agent.",
            ),
            expected={
                "page_state": "search_results",
                "has_search_results": True,
                "visible_query_contains": "qwen",
                "next_action_any": ["open_note", "scroll"],
            },
            max_tokens=128,
        ),
        BenchCase(
            case_id="vision_chat_typed_state",
            mode="vision",
            label="Classify typed chatbot screenshot",
            image_path=chat_typed_img,
            prompt=_json_only_prompt(
                '{"page_state":"chat_ready|generating|blocked|unknown","composer_has_text":true,"generating":false,"next_action":"submit|wait|retry"}',
                "Look at this chatbot screenshot and classify the current state for a browser agent.",
            ),
            expected={
                "page_state": "chat_ready",
                "composer_has_text": True,
                "generating": False,
                "next_action": "submit",
            },
            max_tokens=128,
        ),
        BenchCase(
            case_id="vision_chat_generating_state",
            mode="vision",
            label="Classify generating chatbot screenshot",
            image_path=chat_generating_img,
            prompt=_json_only_prompt(
                '{"page_state":"chat_ready|generating|blocked|unknown","composer_has_text":false,"generating":true,"next_action":"submit|wait|retry"}',
                "Look at this chatbot screenshot and classify the current state for a browser agent.",
            ),
            expected={
                "page_state": "generating",
                "generating": True,
                "next_action": "wait",
            },
            max_tokens=128,
        ),
    ]


def _read_image_b64(path: str) -> tuple[str, str]:
    raw = Path(path).read_bytes()
    media_type = "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    return base64.b64encode(raw).decode(), media_type


def _extract_json(media: MediaProcessor, text: str) -> dict[str, Any] | None:
    parsed = media.extract_json(text)
    return parsed if isinstance(parsed, dict) else None


def _contains_any(text: str, needles: list[str]) -> bool:
    base = (text or "").lower()
    return any(needle.lower() in base for needle in needles)


def judge_exact(expected: dict[str, Any], parsed: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not parsed:
        return 0.0, ["no_json"]
    checks: list[bool] = []
    notes: list[str] = []
    for key, val in expected.items():
        if key.endswith("_contains"):
            field = key[:-9]
            ok = val.lower() in str(parsed.get(field, "")).lower()
            checks.append(ok)
            if not ok:
                notes.append(f"{field} missing '{val}'")
        elif key.endswith("_any"):
            field = key[:-4]
            actual = parsed.get(field)
            if isinstance(actual, list):
                blob = " ".join(str(x) for x in actual)
            else:
                blob = str(actual or "")
            ok = _contains_any(blob, list(val))
            checks.append(ok)
            if not ok:
                notes.append(f"{field} missing any of {val}")
        else:
            ok = parsed.get(key) == val
            checks.append(ok)
            if not ok:
                notes.append(f"{key} != {val!r} (got {parsed.get(key)!r})")
    score = sum(1 for c in checks if c) / len(checks) if checks else 0.0
    return score, notes


def judge_synthesis(expected: dict[str, Any], parsed: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not parsed:
        return 0.0, ["no_json"]
    score = 0.0
    notes: list[str] = []
    for field, weight in (("content_patterns", 0.4), ("user_needs", 0.4), ("unknowns", 0.2)):
        actual = parsed.get(field)
        if isinstance(actual, list):
            blob = " ".join(str(x) for x in actual)
        else:
            blob = str(actual or "")
        key = f"{field}_any"
        ok = _contains_any(blob, list(expected.get(key, [])))
        if ok:
            score += weight
        else:
            notes.append(f"{field} missing expected concepts {expected.get(key, [])}")
    return score, notes


def judge_case(case: BenchCase, media: MediaProcessor, raw_text: str) -> tuple[float, dict[str, Any]]:
    parsed = _extract_json(media, raw_text)
    if case.judge == "synthesis":
        score, notes = judge_synthesis(case.expected, parsed)
    else:
        score, notes = judge_exact(case.expected, parsed)
    return score, {"parsed": parsed, "notes": notes}


def run_call(media: MediaProcessor, case: BenchCase) -> tuple[str, PerfSample]:
    import psutil

    proc = psutil.Process()
    rss_before = proc.memory_info().rss
    cpu_before = proc.cpu_times()
    threads_before = proc.num_threads()
    t0 = time.perf_counter()
    with ProcessSampler() as sampler:
        if case.mode == "vision":
            img_b64, media_type = _read_image_b64(case.image_path or "")
            raw = media.call_vision(img_b64, case.prompt, media_type=media_type, max_tokens=case.max_tokens)
        else:
            raw = media.call_text(case.prompt, max_tokens=case.max_tokens)
    wall = time.perf_counter() - t0
    cpu_after = proc.cpu_times()
    rss_after = proc.memory_info().rss
    threads_after = proc.num_threads()
    cpu_time = (cpu_after.user + cpu_after.system) - (cpu_before.user + cpu_before.system)
    perf = PerfSample(
        wall_s=round(wall, 3),
        cpu_time_s=round(cpu_time, 3),
        cpu_util_pct=round((cpu_time / wall) * 100, 1) if wall > 0 else 0.0,
        rss_before_mb=_mb(rss_before),
        rss_after_mb=_mb(rss_after),
        rss_peak_mb=_mb(max(sampler.peak_rss, rss_after)),
        threads_before=threads_before,
        threads_after=threads_after,
        threads_peak=max(sampler.peak_threads, threads_after),
    )
    return raw, perf


def backend_label(name: str) -> str:
    return {
        "sonnet": "Claude Sonnet 4.6",
        "qwen-local": "Local Qwen3.5-9B-MLX-4bit",
    }.get(name, name)


def _format_json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def _format_text_block(text: str) -> str:
    return "```text\n" + text.rstrip() + "\n```"


def _perf_line(case: dict[str, Any]) -> str:
    perf = case["perf"]
    return (
        f"score={case['score']} | wall={perf['wall_s']}s | cpu={perf['cpu_time_s']}s "
        f"({perf['cpu_util_pct']}%) | rss_peak={perf['rss_peak_mb']}MB | "
        f"threads_peak={perf['threads_peak']}"
    )


def run_backend_cases(backend: str, cases: list[BenchCase]) -> dict[str, Any]:
    media = MediaProcessor(MediaConfig(backend=backend))
    warmup = None
    if backend == "qwen-local":
        t0 = time.perf_counter()
        ping = media.call_text("Reply with exactly OK.", max_tokens=8)
        warmup = {
            "probe_output": ping,
            "elapsed_s": round(time.perf_counter() - t0, 3),
        }

    results = []
    for case in cases:
        raw, perf = run_call(media, case)
        score, judged = judge_case(case, media, raw)
        results.append({
            "case_id": case.case_id,
            "mode": case.mode,
            "label": case.label,
            "score": round(score, 3),
            "perf": asdict(perf),
            "raw_output": raw,
            "parsed": judged["parsed"],
            "judge_notes": judged["notes"],
            "expected": case.expected,
            "image_path": case.image_path,
        })

    avg_score = sum(item["score"] for item in results) / len(results)
    avg_wall = sum(item["perf"]["wall_s"] for item in results) / len(results)
    by_mode = {}
    for mode in sorted({case.mode for case in cases}):
        mode_items = [item for item in results if item["mode"] == mode]
        by_mode[mode] = {
            "avg_score": round(sum(x["score"] for x in mode_items) / len(mode_items), 3),
            "avg_wall_s": round(sum(x["perf"]["wall_s"] for x in mode_items) / len(mode_items), 3),
        }
    return {
        "backend": backend,
        "label": backend_label(backend),
        "warmup": warmup,
        "summary": {
            "avg_score": round(avg_score, 3),
            "avg_wall_s": round(avg_wall, 3),
            "by_mode": by_mode,
        },
        "cases": results,
    }


def write_report(output_dir: Path, payload: dict[str, Any]) -> None:
    (output_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    case_defs = {case["case_id"]: case for case in payload["cases"]}
    backend_map = {backend["backend"]: backend for backend in payload["backends"]}
    backend_order = [backend["backend"] for backend in payload["backends"]]
    cases_by_backend = {
        backend["backend"]: {case["case_id"]: case for case in backend["cases"]}
        for backend in payload["backends"]
    }

    lines = [
        "# Web-Use Model Benchmark",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Summary",
    ]
    for backend in payload["backends"]:
        lines.extend([
            f"### {backend['label']}",
            f"- Avg score: {backend['summary']['avg_score']}",
            f"- Avg wall time: {backend['summary']['avg_wall_s']}s",
        ])
        if backend.get("warmup"):
            lines.append(f"- Local warmup/ping: {backend['warmup']['elapsed_s']}s")
        for mode, summary in backend["summary"]["by_mode"].items():
            lines.append(f"- {mode}: score {summary['avg_score']}, wall {summary['avg_wall_s']}s")
        lines.append("")

    lines.extend([
        "## Case Matrix",
        "",
        "| Case | Mode | Claude Sonnet 4.6 | Local Qwen3.5-9B-MLX-4bit |",
        "| --- | --- | --- | --- |",
    ])
    for case_id in [case["case_id"] for case in payload["cases"]]:
        left = cases_by_backend.get("sonnet", {}).get(case_id)
        right = cases_by_backend.get("qwen-local", {}).get(case_id)
        left_txt = f"{left['score']} / {left['perf']['wall_s']}s" if left else "-"
        right_txt = f"{right['score']} / {right['perf']['wall_s']}s" if right else "-"
        lines.append(f"| `{case_id}` | {case_defs[case_id]['mode']} | {left_txt} | {right_txt} |")

    lines.append("")
    lines.append("## Detailed Cases")
    for case_id in [case["case_id"] for case in payload["cases"]]:
        case_def = case_defs[case_id]
        lines.extend([
            f"### {case_def['label']}",
            f"- Case ID: `{case_id}`",
            f"- Mode: `{case_def['mode']}`",
        ])
        if case_def.get("image_path"):
            lines.append(f"- Image: [{Path(case_def['image_path']).name}]({case_def['image_path']})")
        lines.append("- Prompt:")
        lines.append(_format_text_block(case_def["prompt"]))
        lines.append("- Expected:")
        lines.append(_format_json_block(case_def["expected"]))

        for backend_name in backend_order:
            backend = backend_map[backend_name]
            case = cases_by_backend[backend_name][case_id]
            lines.append(f"#### {backend['label']}")
            lines.append(f"- {_perf_line(case)}")
            lines.append(f"- Judge notes: {', '.join(case['judge_notes']) if case['judge_notes'] else 'ok'}")
            if case.get("parsed") is not None:
                lines.append("- Parsed output:")
                lines.append(_format_json_block(case["parsed"]))
            lines.append("- Raw output:")
            lines.append(_format_text_block(case["raw_output"]))
        lines.append("")
    (output_dir / "README.md").write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    load_runtime_env()
    parser = argparse.ArgumentParser(description="Benchmark Sonnet vs local Qwen 9B on web-use tasks.")
    parser.add_argument(
        "--backends",
        nargs="+",
        default=["sonnet", "qwen-local"],
        choices=["sonnet", "qwen-local"],
        help="Backends to benchmark.",
    )
    parser.add_argument(
        "--rewrite-results",
        default="",
        help="Rewrite README.md for an existing benchmark results.json without rerunning calls.",
    )
    args = parser.parse_args(argv)

    if args.rewrite_results:
        results_path = Path(args.rewrite_results).expanduser().resolve()
        payload = json.loads(results_path.read_text())
        write_report(results_path.parent, payload)
        print(results_path.parent)
        return 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = PROJECT_ROOT / "task_runs" / f"webuse_model_bench_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = build_cases()
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cases": [asdict(case) for case in cases],
        "backends": [],
    }

    for backend in args.backends:
        payload["backends"].append(run_backend_cases(backend, cases))

    write_report(output_dir, payload)
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
