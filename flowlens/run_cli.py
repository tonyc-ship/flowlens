"""Unified CLI entry point for FlowLens task execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .agent.loop import run_agent
from .core.auth import default_cloud_model, preferred_provider, resolve_model_provider
from .core.bridge import BridgeAlreadyRunningError
from .core.runtime import task_runs_root
from .perception.media import MediaConfig, MediaProcessor
from .platforms.xhs.agent_profile import DEFAULT_START_URL as XHS_START_URL
from .tools.capability_packs import CAPABILITY_PACKS, capability_pack_spec, dependency_closure

PROFILE_URL_RE = re.compile(r"https?://www\.xiaohongshu\.com/user/profile/[^\s]+")
WEB_URL_RE = re.compile(r"https?://[^\s]+")


@dataclass
class UnifiedRunPlan:
    prompt: str
    recommended_packs: list[str]
    use_browser: bool
    reasoning: str
    llm_backend: str = "auto"


def _backend_to_model(backend: str) -> str:
    if backend == "auto":
        return default_cloud_model()
    if backend in {"sonnet", "anthropic"}:
        return default_cloud_model(provider="anthropic")
    if backend == "qwen-local":
        return "qwen-local"
    if backend == "ui-tars-local":
        return "ui-tars-local"
    if backend in {"openai", "kimi", "qwen"}:
        return default_cloud_model(provider=backend)
    return default_cloud_model()


def _provider_to_backend(provider: str | None) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == "anthropic":
        return "sonnet"
    if normalized in {"openai", "kimi", "qwen"}:
        return normalized
    return "sonnet"


def resolve_llm_backend(choice: str | None, *, model: str | None = None) -> str:
    normalized_choice = str(choice or "").strip().lower()
    normalized_model = str(model or "").strip()

    if normalized_model:
        if normalized_model == "qwen-local" or normalized_model.startswith("Qwen"):
            return "qwen-local"
        if normalized_model == "ui-tars-local" or normalized_model.startswith("UI-TARS"):
            return "ui-tars-local"
        return _provider_to_backend(resolve_model_provider(normalized_model))

    if normalized_choice and normalized_choice != "auto":
        return normalized_choice

    configured_provider = preferred_provider()
    if configured_provider:
        return _provider_to_backend(configured_provider)

    return _provider_to_backend(resolve_model_provider(default_cloud_model()))


def _planner_prompt(prompt: str) -> str:
    pack_lines = []
    for spec in CAPABILITY_PACKS.values():
        dependency_text = f" Depends on: {', '.join(spec.dependencies)}." if spec.dependencies else ""
        surface = []
        if spec.requires_browser:
            surface.append("browser")
        if spec.requires_desktop:
            surface.append("desktop")
        surface_text = f" Surface: {', '.join(surface)}." if surface else ""
        pack_lines.append(
            f"- {spec.pack_id}: {spec.summary} When to use: {spec.when_to_use}.{dependency_text}{surface_text}"
        )
    pack_catalog = "\n".join(pack_lines)
    return f"""You are the runtime capability planner for FlowLens.

Pick the high-level capability packs that should be available at the start of a run.
Do not route to fixed task kinds. Think in terms of programs/sites/surfaces.

Available capability packs:
{pack_catalog}

Planning rules:
- Include `browser_generic` for arbitrary websites, web research, or when the task is web-facing or ambiguous.
- Include `xiaohongshu` when the task is explicitly about Xiaohongshu / 小红书 / xhs.
- Include `wechat` when the task is explicitly about WeChat conversations, groups, chat history, or the WeChat desktop app.
- Include `desktop_generic` for native-app tasks or when the task may require interacting with a macOS program other than the browser.
- Set `use_browser=false` only when the task is clearly desktop-only.
- Be inclusive enough that the agent is not boxed in, but do not enable unrelated packs.

Return JSON only:
{{
  "recommended_packs": ["browser_generic"],
  "use_browser": true,
  "reasoning": "one short sentence"
}}

User request:
{prompt}
"""


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = str(raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    if not cleaned.startswith("{") and "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
    payload = json.loads(cleaned)
    return payload if isinstance(payload, dict) else {}


def _plan_with_llm(prompt: str, *, llm_backend: str = "auto") -> dict[str, Any]:
    resolved_backend = resolve_llm_backend(llm_backend)
    media = MediaProcessor(MediaConfig(model=_backend_to_model(resolved_backend), backend=resolved_backend))
    return _parse_json_object(media.call_text(_planner_prompt(prompt), max_tokens=600))


def _fallback_plan(prompt: str) -> UnifiedRunPlan:
    trimmed = prompt.strip()
    lower = trimmed.casefold()
    packs: list[str] = []
    use_browser = True
    reasons: list[str] = []

    if ("微信" in trimmed or "wechat" in lower) and not any(keyword in lower for keyword in ("browser", "网页", "网站", "web ")):
        packs.extend(["desktop_generic", "wechat"])
        use_browser = False
        reasons.append("The task explicitly refers to WeChat desktop conversation work.")
    elif "微信" in trimmed or "wechat" in lower:
        packs.extend(["browser_generic", "desktop_generic", "wechat"])
        reasons.append("The task refers to WeChat and may need both browser and desktop surfaces.")

    if PROFILE_URL_RE.search(trimmed) or "小红书" in trimmed or "xiaohongshu" in lower or re.search(r"\bxhs\b", lower):
        packs.extend(["browser_generic", "xiaohongshu"])
        reasons.append("The task refers to Xiaohongshu.")

    if not packs and ("桌面" in trimmed or "程序" in trimmed or "app" in lower or "应用" in trimmed):
        packs.append("desktop_generic")
        use_browser = False if not WEB_URL_RE.search(trimmed) else True
        reasons.append("The task looks like native-app work.")

    if not packs:
        packs.append("browser_generic")
        reasons.append("The task is ambiguous, so generic browser access is the safest default.")

    return UnifiedRunPlan(
        prompt=trimmed,
        recommended_packs=dependency_closure(packs),
        use_browser=use_browser or "browser_generic" in packs or "xiaohongshu" in packs,
        reasoning=" ".join(reasons)[:240],
    )


def infer_run_plan(
    prompt: str,
    *,
    llm_backend: str = "auto",
    planner: Callable[[str], dict[str, Any]] | None = None,
) -> UnifiedRunPlan:
    trimmed = str(prompt or "").strip()
    if not trimmed:
        raise ValueError("Task prompt is empty")
    try:
        payload = planner(trimmed) if planner is not None else _plan_with_llm(trimmed, llm_backend=llm_backend)
        raw_packs = payload.get("recommended_packs") or []
        if not isinstance(raw_packs, list):
            raise ValueError("recommended_packs must be a list")
        recommended = [
            spec.pack_id
            for spec in (
                capability_pack_spec(str(item or "").strip())
                for item in raw_packs
            )
            if spec is not None
        ]
        if not recommended:
            raise ValueError("planner returned no valid capability packs")
        return UnifiedRunPlan(
            prompt=trimmed,
            recommended_packs=dependency_closure(recommended),
            use_browser=bool(payload.get("use_browser", True))
            or any(CAPABILITY_PACKS[item].requires_browser for item in dependency_closure(recommended)),
            reasoning=str(payload.get("reasoning") or "").strip() or "Planner selected capability packs from the task semantics.",
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("Unified run planner failed; using fallback: %s", exc)
        return _fallback_plan(trimmed)


def _planner_hint(plan: UnifiedRunPlan) -> str:
    packs = ", ".join(f"`{pack_id}`" for pack_id in plan.recommended_packs)
    surface = "browser+desktop" if plan.use_browser else "desktop-only"
    return (
        "Planner recommendation:\n"
        f"- Likely useful capability packs: {packs}\n"
        f"- Initial runtime surface: {surface}\n"
        f"- Reasoning: {plan.reasoning}\n\n"
        "Treat this recommendation as advisory. You still decide which packs to inspect or activate."
    )


def _default_start_url(plan: UnifiedRunPlan) -> str | None:
    if "xiaohongshu" in plan.recommended_packs:
        return XHS_START_URL
    return None


def _make_run_dir(output_root: Path, prompt: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", prompt.lower()).strip("_")[:80] or "task"
    return output_root / f"run_{ts}_{slug}"


async def _run_plan(plan: UnifiedRunPlan, *, args) -> dict[str, Any]:
    backend = resolve_llm_backend(plan.llm_backend, model=args.model)
    model = args.model or _backend_to_model(backend)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(args.run_dir) if args.run_dir else _make_run_dir(output_root, plan.prompt)

    result = await run_agent(
        task=plan.prompt,
        run_dir=run_dir,
        max_turns=args.max_turns,
        model=model,
        extra_instructions=_planner_hint(plan),
        start_url=_default_start_url(plan),
        use_browser=plan.use_browser,
        initial_capability_packs=set(),
    )
    return {
        "request": {
            "prompt": plan.prompt,
            "llm_backend": backend,
            "model": model,
        },
        "plan": asdict(plan),
        "task_dir": str(run_dir),
        "report_md": str(run_dir / "report.md"),
        "reasoning_log": result.get("reasoning_log", ""),
        "run_state_dir": result.get("run_state_dir", ""),
        "turns": result.get("turns", 0),
        "total_duration_s": result.get("total_duration_s", 0),
        "site_results": result.get("site_results", []),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified FlowLens task runner.")
    parser.add_argument("task", nargs="?", help="Natural language task description")
    parser.add_argument("--prompt", help="Natural language task description")
    parser.add_argument("--max-turns", type=int, default=40, help="Maximum reasoning turns")
    parser.add_argument("--model", default=None, help="Model ID override")
    parser.add_argument(
        "--llm-backend",
        choices=["auto", "sonnet", "anthropic", "openai", "kimi", "qwen", "qwen-local", "ui-tars-local"],
        default="auto",
        help="Reasoning backend. `auto` follows `flowlens auth` for cloud defaults; local remains an explicit opt-in.",
    )
    parser.add_argument("--run-dir", default=None, help="Exact run directory for artifacts")
    parser.add_argument(
        "--output-root",
        default=str(task_runs_root() / "runs"),
        help="Parent directory for generated run folders",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only print the inferred capability plan")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    parser = _build_parser()
    args = parser.parse_args(argv)
    prompt = str(args.prompt or args.task or "").strip()
    if not prompt:
        parser.error("A task prompt is required.")

    resolved_backend = resolve_llm_backend(args.llm_backend, model=args.model)
    plan = infer_run_plan(prompt, llm_backend=resolved_backend)
    plan.llm_backend = resolved_backend
    if args.dry_run:
        print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
        return 0

    try:
        result = asyncio.run(_run_plan(plan, args=args))
    except KeyboardInterrupt:
        message = {"error": "cancelled"}
        print(json.dumps(message, ensure_ascii=False) if args.json else "\nCancelled.")
        return 130
    except BridgeAlreadyRunningError as exc:
        message = {"error": str(exc)}
        print(json.dumps(message, ensure_ascii=False) if args.json else f"\nError: {exc}\n")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"Task completed in {result['turns']} turns")
        print(f"Run directory: {result['task_dir']}")
        print(f"Report: {result['report_md']}")
        print(f"{'=' * 60}")
    return 0
