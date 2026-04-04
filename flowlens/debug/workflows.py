"""Higher-level visual-debug workflows."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PIL import ImageDraw

from .macos import MacOSController
from .visual_debug import VisualDebugger


@dataclass
class WorkflowResult:
    success: bool
    output_dir: str
    summary: str
    report: dict

    def to_dict(self) -> dict:
        return asdict(self)


def run_sidepanel_demo_sync(output_dir: Path | None = None) -> WorkflowResult:
    """Run a small end-to-end visual-debug task around the FlowLens side panel."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = output_dir or Path("task_runs") / "visual_debug" / f"sidepanel_demo_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    controller = MacOSController()
    debugger = VisualDebugger(controller=controller, grounding_backend="uitars_mlx")

    report: dict = {
        "workflow": "sidepanel_demo",
        "timestamp": timestamp,
        "steps": [],
    }

    controller.activate_app("Google Chrome")
    controller.open_url("https://www.google.com", browser="Google Chrome")
    controller.activate_app("Google Chrome")
    time.sleep(1.0)

    before = _inspect_visible_chrome(
        debugger,
        output_dir / "01_before",
        "Verify that this is the currently visible Chrome window before opening the FlowLens side panel.",
    )
    report["steps"].append({"name": "before", "record": before})

    initial_visible = _side_panel_visible(before)
    if initial_visible:
        normalize_click = _click_toolbar_extension(
            debugger,
            controller,
            output_dir / "02_close_existing_panel",
            app_name="Google Chrome",
            target_text="XHS Research Agent",
        )
        report["steps"].append({"name": "close_existing_panel", "record": normalize_click})
        time.sleep(1.0)

        after_close = _inspect_visible_chrome(
            debugger,
            output_dir / "03_after_close",
            "Did the existing FlowLens side panel close after clicking the toolbar icon?",
        )
        report["steps"].append({"name": "after_close", "record": after_close})
    else:
        after_close = before

    ax_click = _click_toolbar_extension(
        debugger,
        controller,
        output_dir / "04_open_side_panel",
        app_name="Google Chrome",
        target_text="XHS Research Agent",
    )
    report["steps"].append({"name": "open_side_panel", "record": ax_click})
    time.sleep(1.0)

    after_click = _inspect_visible_chrome(
        debugger,
        output_dir / "05_after_open_click",
        "Did the FlowLens side panel open on the right side of this visible Chrome window after clicking the toolbar icon?",
    )
    report["steps"].append({"name": "after_open_click", "record": after_click})

    success = _side_panel_visible(after_click)
    if not success:
        controller.hotkey("command", "shift", "y")
        time.sleep(1.0)
        after_hotkey = _inspect_visible_chrome(
            debugger,
            output_dir / "06_after_hotkey",
            "Did the FlowLens side panel open on the right side of this visible Chrome window after the shortcut?",
        )
        report["steps"].append({"name": "after_hotkey", "record": after_hotkey})
        success = _side_panel_visible(after_hotkey)

    summary = (
        "Clicked the Chrome toolbar XHS Research Agent icon and visually verified the FlowLens side panel."
        if success
        else "The workflow ran but the final screenshot did not verify a visible FlowLens side panel."
    )
    report["success"] = success
    report["summary"] = summary

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return WorkflowResult(
        success=success,
        output_dir=str(output_dir),
        summary=summary,
        report=report,
    )


def _inspect_visible_chrome(
    debugger: VisualDebugger,
    save_dir: Path,
    question: str,
    *,
    locate: str | None = None,
    click_located: bool = False,
) -> dict:
    target = debugger.resolve_targets(app_name="Google Chrome", visible_only=True)[0]
    image = debugger.capture_target(target)
    record = debugger.inspect_image(
        image,
        target=target,
        mode="chrome-watch",
        question=question,
        max_dim=768,
        max_tokens=128,
        locate=locate,
        click_located=click_located,
        save_dir=save_dir,
        verify_capture=True,
    )
    record["side_panel_verification"] = debugger.verify_right_side_panel(
        image,
        save_path=save_dir / "right_panel.png",
    )
    return record


def _click_toolbar_extension(
    debugger: VisualDebugger,
    controller: MacOSController,
    save_dir: Path,
    *,
    app_name: str,
    target_text: str,
) -> dict:
    save_dir.mkdir(parents=True, exist_ok=True)
    target = debugger.resolve_targets(app_name=app_name, visible_only=True)[0]
    image = debugger.capture_target(target)
    ax = controller.find_accessibility_element(app_name, target_text)
    record: dict = {
        "target": target.to_dict(),
        "ax_element": ax.to_dict() if ax else None,
        "artifacts": {},
    }
    if ax is not None:
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        rel_left = max(0, ax.x - target.x)
        rel_top = max(0, ax.y - target.y)
        rel_right = min(image.size[0], rel_left + ax.width)
        rel_bottom = min(image.size[1], rel_top + ax.height)
        draw.rectangle((rel_left, rel_top, rel_right, rel_bottom), outline="red", width=4)
        draw.line((rel_left, rel_top, rel_right, rel_bottom), fill="red", width=3)
        draw.line((rel_left, rel_bottom, rel_right, rel_top), fill="red", width=3)
        annotated_path = save_dir / "toolbar_target.annotated.png"
        image_path = save_dir / "toolbar_target.raw.png"
        image.save(image_path)
        annotated.save(annotated_path)
        record["artifacts"]["raw_image"] = str(image_path)
        record["artifacts"]["annotated_image"] = str(annotated_path)
        clicked = controller.click_accessibility_element(app_name, target_text)
        record["clicked"] = clicked is not None
        if clicked is not None:
            record["clicked_element"] = clicked.to_dict()
    return record


def _side_panel_visible(record: dict) -> bool:
    parsed = (((record or {}).get("side_panel_verification") or {}).get("parsed") or {})
    return bool(parsed.get("side_panel_visible"))
