"""Visual debugging workflow built on macOS capture + local MLX vision."""

from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat

from ..perception.grounding import GroundingModel
from ..perception.local_llm import LocalLLM
from .macos import DisplayInfo, MacOSController, WindowInfo

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


GENERAL_PROMPT = """You are debugging a live macOS UI session.
Inspect this screenshot and return JSON only with this schema:
{
  "visible_app": "short string",
  "active_surface": "short string",
  "state_summary": "short string",
  "notable_elements": ["short string", "short string"],
  "possible_blocker": "short string or empty",
  "next_step": "short string"
}
Keep every string short and concrete. Do not include markdown."""


CHROME_WATCH_PROMPT = """You are debugging Chrome extension watch mode.
Inspect this screenshot and return JSON only with this schema:
{
  "chrome_visible": true,
  "window_type": "browser|extensions|popup|other|unknown",
  "page_or_dialog": "short string",
  "side_panel_visible": true,
  "extension_popup_visible": true,
  "agent_activity_visible": true,
  "manual_prompt_visible": true,
  "primary_blocker": "short string or empty",
  "recommended_next_action": "short string"
}
Use false when unsure for booleans. Do not include markdown."""


CAPTURE_VERIFY_PROMPT = """You are validating a screenshot before UI debugging.
Intended target:
- app: {app_name}
- title hint: {title}

Important:
- A browser side panel attached to the same Chrome window is valid and should NOT count as occlusion.
- Normal browser UI changes after clicking an extension, including a right-side extension panel, are still a valid match for the target window.
- Set cross_space_or_split_artifact=true only for obvious stitched captures across different Spaces or displays, not for a legitimate browser side panel.

Return JSON only with this schema:
{{
  "matches_target": true,
  "dominant_app": "short string",
  "wrong_or_occluded": true,
  "cross_space_or_split_artifact": true,
  "notes": "short string"
}}
Set matches_target=false if the screenshot is mostly another app, the target is covered by another window, or the image looks stitched across spaces/displays."""


RIGHT_PANEL_VERIFY_PROMPT = """This image is only the right edge of a Chrome window.
Decide whether it shows a distinct browser side panel, not just normal webpage content.
Treat "XHS Research Agent" as the FlowLens panel identity.

Return JSON only with this schema:
{
  "side_panel_visible": true,
  "panel_identity": "FlowLens|other|none",
  "looks_like_regular_page_content": true
}

Set side_panel_visible=true only if the crop clearly shows a separate vertical panel with its own background, controls, header, or bounded layout. If it just looks like webpage content or empty page margin, return false."""


@dataclass
class CaptureTarget:
    kind: str
    target_id: int
    label: str
    x: int
    y: int
    width: int
    height: int
    owner: str = ""
    title: str = ""
    capture_backend: str = "quartz"

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_image(image: Image.Image, max_dim: int) -> Image.Image:
    image = image.convert("RGB")
    width, height = image.size
    if max(width, height) <= max_dim:
        return image
    scale = max_dim / max(width, height)
    return image.resize((max(1, int(width * scale)), max(1, int(height * scale))), RESAMPLE)


def _thumbnail_signature(image: Image.Image) -> Image.Image:
    return image.convert("L").resize((64, 64), RESAMPLE)


def _mean_diff(left: Image.Image, right: Image.Image) -> float:
    diff = ImageChops.difference(left, right)
    return float(ImageStat.Stat(diff).mean[0])


def _extract_json(text: str) -> dict | None:
    candidate = text.strip()
    candidate = candidate.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    if candidate.startswith("json\n"):
        candidate = candidate.split("\n", 1)[1].strip()

    raw_candidates = [candidate]
    if "{" in candidate:
        brace_start = candidate.find("{")
        brace_end = candidate.rfind("}")
        if brace_end >= brace_start:
            raw_candidates.append(candidate[brace_start: brace_end + 1])
        else:
            raw = candidate[brace_start:]
            balance = raw.count("{") - raw.count("}")
            if balance > 0:
                raw += "}" * balance
            raw_candidates.append(raw)

    for raw in raw_candidates:
        if not raw:
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None


class VisualDebugger:
    """Capture UI state, run local vision understanding, and ground UI elements."""

    def __init__(
        self,
        *,
        controller: MacOSController | None = None,
        llm_backend: str = "qwen-local",
        grounding_backend: str = "auto",
    ):
        self.controller = controller or MacOSController()
        self.llm_backend = llm_backend
        self.grounding_backend = grounding_backend
        self._local_llm: LocalLLM | None = None
        self._grounding_model: GroundingModel | None = None

    @property
    def local_llm(self) -> LocalLLM:
        if self._local_llm is None:
            if self.llm_backend != "qwen-local":
                raise ValueError(f"Unsupported visual backend: {self.llm_backend}")
            self._local_llm = LocalLLM()
        return self._local_llm

    @property
    def grounding_model(self) -> GroundingModel:
        if self._grounding_model is None:
            self._grounding_model = GroundingModel(backend=self.grounding_backend)
        return self._grounding_model

    def list_displays(self) -> list[DisplayInfo]:
        return self.controller.list_displays()

    def list_windows(
        self,
        app_name: str | None = None,
        *,
        on_screen_only: bool = False,
        title_contains: str | None = None,
    ) -> list[WindowInfo]:
        return self.controller.list_windows(
            app_name,
            on_screen_only=on_screen_only,
            title_contains=title_contains,
        )

    def resolve_targets(
        self,
        *,
        app_name: str | None = None,
        window_id: int | None = None,
        display_index: int | None = None,
        display_id: int | None = None,
        all_displays: bool = False,
        all_windows: bool = False,
        frontmost_app: bool = False,
        visible_only: bool = False,
        title_contains: str | None = None,
    ) -> list[CaptureTarget]:
        if frontmost_app:
            app_name = self.controller.frontmost_app_name()
            if not app_name:
                raise RuntimeError("Could not determine frontmost application")

        if window_id is not None:
            windows = [item for item in self.list_windows(title_contains=title_contains) if item.window_id == window_id]
            if not windows:
                raise RuntimeError(f"Window not found: {window_id}")
            return [self._window_target(windows[0])]

        if app_name:
            if visible_only and not all_windows:
                best = self.controller.best_window_for_app(
                    app_name,
                    title_contains=title_contains,
                    visible_only=True,
                )
                if best is None:
                    raise RuntimeError(f"No visible window found for app: {app_name}")
                return [self._window_target(best)]
            windows = self.list_windows(app_name=app_name, title_contains=title_contains)
            if not windows:
                raise RuntimeError(f"No windows found for app: {app_name}")
            selected = windows if all_windows else [windows[0]]
            return [self._window_target(item) for item in selected]

        displays = self.list_displays()
        if display_id is not None:
            matches = [item for item in displays if item.display_id == display_id]
            if not matches:
                raise RuntimeError(f"Display not found: {display_id}")
            return [self._display_target(matches[0])]

        if display_index is not None:
            matches = [item for item in displays if item.index == display_index]
            if not matches:
                raise RuntimeError(f"Display index not found: {display_index}")
            return [self._display_target(matches[0])]

        if all_displays or not displays:
            return [self._display_target(item) for item in displays]

        main = next((item for item in displays if item.is_main), displays[0])
        return [self._display_target(main)]

    def capture_target(self, target: CaptureTarget) -> Image.Image:
        if target.kind == "window":
            if target.capture_backend == "region":
                return self.controller.capture_region(target.x, target.y, target.width, target.height)
            return self.controller.capture_window(target.target_id)
        if target.kind == "display":
            return self.controller.capture_display(target.target_id)
        raise ValueError(f"Unsupported target kind: {target.kind}")

    def inspect_targets(
        self,
        targets: list[CaptureTarget],
        *,
        mode: str = "general",
        question: str | None = None,
        max_dim: int = 896,
        max_tokens: int = 192,
        locate: str | None = None,
        click_located: bool = False,
        save_dir: Path | None = None,
        verify_capture: bool = False,
    ) -> list[dict]:
        results: list[dict] = []
        for target in targets:
            image = self.capture_target(target)
            inspected = self.inspect_image(
                image,
                target=target,
                mode=mode,
                question=question,
                max_dim=max_dim,
                max_tokens=max_tokens,
                locate=locate,
                click_located=click_located,
                save_dir=save_dir,
                verify_capture=verify_capture,
            )
            results.append(inspected)
        return results

    def inspect_image(
        self,
        image: Image.Image,
        *,
        target: CaptureTarget,
        mode: str = "general",
        question: str | None = None,
        max_dim: int = 896,
        max_tokens: int = 192,
        locate: str | None = None,
        click_located: bool = False,
        save_dir: Path | None = None,
        verify_capture: bool = False,
    ) -> dict:
        timestamp = datetime.now().isoformat(timespec="seconds")
        normalized = _normalize_image(image, max_dim=max_dim)
        analysis_prompt = self._build_prompt(mode=mode, question=question)
        analysis = self._analyze_image(normalized, analysis_prompt, max_tokens=max_tokens)

        record: dict = {
            "timestamp": timestamp,
            "target": target.to_dict(),
            "mode": mode,
            "analysis": analysis,
            "artifacts": {},
        }

        if verify_capture:
            record["capture_verification"] = self._verify_capture(normalized, target)

        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{self._slugify(target.label)}_{int(time.time() * 1000)}"
            raw_path = save_dir / f"{stem}.png"
            normalized_path = save_dir / f"{stem}.small.png"
            image.save(raw_path)
            normalized.save(normalized_path)
            record["artifacts"]["raw_image"] = str(raw_path)
            record["artifacts"]["small_image"] = str(normalized_path)

        if locate:
            grounding = self.grounding_model.ground(normalized, locate)
            if grounding is not None:
                scale_x = image.size[0] / normalized.size[0]
                scale_y = image.size[1] / normalized.size[1]
                rel_x = int(grounding.x * scale_x)
                rel_y = int(grounding.y * scale_y)
                abs_x = target.x + rel_x
                abs_y = target.y + rel_y
                grounded = {
                    "query": locate,
                    "x": rel_x,
                    "y": rel_y,
                    "abs_x": abs_x,
                    "abs_y": abs_y,
                    "confidence": grounding.confidence,
                    "source": grounding.source,
                    "raw_output": grounding.raw_output,
                }
                record["grounding"] = grounded
                if save_dir is not None:
                    annotated = image.copy()
                    draw = ImageDraw.Draw(annotated)
                    radius = max(8, int(min(image.size) * 0.02))
                    draw.ellipse((rel_x - radius, rel_y - radius, rel_x + radius, rel_y + radius), outline="red", width=4)
                    draw.line((rel_x - radius * 2, rel_y, rel_x + radius * 2, rel_y), fill="red", width=3)
                    draw.line((rel_x, rel_y - radius * 2, rel_x, rel_y + radius * 2), fill="red", width=3)
                    annotated_path = Path(record["artifacts"]["raw_image"]).with_name(
                        Path(record["artifacts"]["raw_image"]).stem + ".grounded.png"
                    )
                    annotated.save(annotated_path)
                    record["artifacts"]["grounded_image"] = str(annotated_path)
                if click_located:
                    self.controller.click(abs_x, abs_y)
                    record["clicked"] = True
            else:
                record["grounding"] = None

        return record

    def watch(
        self,
        targets: list[CaptureTarget],
        *,
        mode: str = "general",
        question: str | None = None,
        interval: float = 1.5,
        iterations: int | None = None,
        max_dim: int = 896,
        max_tokens: int = 192,
        change_threshold: float = 4.0,
        locate: str | None = None,
        click_located: bool = False,
        save_dir: Path | None = None,
        verify_capture: bool = False,
    ):
        previous: dict[tuple[str, int], Image.Image] = {}
        emitted = 0
        while iterations is None or emitted < iterations:
            now = datetime.now().isoformat(timespec="seconds")
            for target in targets:
                image = self.capture_target(target)
                thumb = _thumbnail_signature(image)
                key = (target.kind, target.target_id)
                prev = previous.get(key)
                mean_diff = _mean_diff(prev, thumb) if prev is not None else 255.0
                changed = prev is None or mean_diff >= change_threshold
                record = {
                    "timestamp": now,
                    "target": target.to_dict(),
                    "changed": changed,
                    "mean_diff": round(mean_diff, 2),
                }
                if changed:
                    inspected = self.inspect_image(
                        image,
                        target=target,
                        mode=mode,
                        question=question,
                        max_dim=max_dim,
                        max_tokens=max_tokens,
                        locate=locate,
                        click_located=click_located,
                        save_dir=save_dir,
                        verify_capture=verify_capture,
                    )
                    record.update({
                        "analysis": inspected["analysis"],
                        "artifacts": inspected.get("artifacts", {}),
                    })
                    if "capture_verification" in inspected:
                        record["capture_verification"] = inspected["capture_verification"]
                    if "grounding" in inspected:
                        record["grounding"] = inspected["grounding"]
                    if inspected.get("clicked"):
                        record["clicked"] = True
                yield record
                previous[key] = thumb
            emitted += 1
            if iterations is None or emitted < iterations:
                time.sleep(interval)

    def _analyze_image(self, image: Image.Image, prompt: str, *, max_tokens: int) -> dict:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        img_b64 = base64.b64encode(buffer.getvalue()).decode()
        raw = self.local_llm.call_vision(img_b64, prompt, media_type="image/png", max_tokens=max_tokens)
        parsed = _extract_json(raw)
        return {"parsed": parsed, "raw": raw}

    def _verify_capture(self, image: Image.Image, target: CaptureTarget) -> dict:
        prompt = CAPTURE_VERIFY_PROMPT.format(
            app_name=target.owner or "unknown",
            title=target.title or target.label,
        )
        return self._analyze_image(image, prompt, max_tokens=96)

    def verify_right_side_panel(
        self,
        image: Image.Image,
        *,
        crop_ratio: float = 0.34,
        max_dim: int = 640,
        save_path: Path | None = None,
    ) -> dict:
        width, height = image.size
        left = max(0, int(width * (1.0 - crop_ratio)))
        crop = image.crop((left, 0, width, height))
        normalized = _normalize_image(crop, max_dim=max_dim)
        if save_path is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            crop.save(save_path)
        result = self._analyze_image(normalized, RIGHT_PANEL_VERIFY_PROMPT, max_tokens=72)
        result["crop_box"] = {"left": left, "top": 0, "right": width, "bottom": height}
        if save_path is not None:
            result["crop_image"] = str(save_path)
        return result

    @staticmethod
    def _window_target(window: WindowInfo) -> CaptureTarget:
        label = f"window_{window.window_id}_{window.owner}"
        if window.title:
            label += "_" + window.title[:40]
        return CaptureTarget(
            kind="window",
            target_id=window.window_id,
            label=label,
            x=window.x,
            y=window.y,
            width=window.width,
            height=window.height,
            owner=window.owner,
            title=window.title,
            capture_backend=window.capture_backend,
        )

    @staticmethod
    def _display_target(display: DisplayInfo) -> CaptureTarget:
        return CaptureTarget(
            kind="display",
            target_id=display.display_id,
            label=f"display_{display.index}_{display.display_id}",
            x=display.x,
            y=display.y,
            width=display.width,
            height=display.height,
        )

    @staticmethod
    def _build_prompt(*, mode: str, question: str | None) -> str:
        base = CHROME_WATCH_PROMPT if mode == "chrome-watch" else GENERAL_PROMPT
        if not question:
            return base
        return f"{base}\nAdditional focus: {question}"

    @staticmethod
    def _slugify(value: str) -> str:
        text = "".join(char if char.isalnum() else "_" for char in value)
        return text.strip("_")[:80] or "capture"
