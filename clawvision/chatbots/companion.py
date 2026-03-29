"""Long-lived desktop companion for chatbot fan-out tasks.

The Tauri app talks to this process over line-delimited JSON on stdin/stdout.
This keeps the Chrome bridge and local models warm across requests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .runner import (
    CHATBOT_PAGE_SIMPLE_CHECK,
    DEFAULT_CONNECT_TIMEOUT_S,
    FAST_CONNECT_TIMEOUT_S,
    MultiChatRunner,
)
from ..agent.bridge import ExtensionBridge
from ..vision.llm import VisionLLM

logger = logging.getLogger(__name__)


def _emit_json(payload: dict) -> bool:
    """Write a protocol payload to stdout, returning False on broken pipes."""
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return True
    except BrokenPipeError:
        logger.warning("Companion stdout pipe closed while sending payload")
        return False


def _task_stub(task_id: str, question: str, output_root: Path) -> dict:
    created_at = str(int(time.time() * 1000))
    return {
        "id": task_id,
        "kind": "multi_chatbot",
        "prompt": question,
        "status": "running",
        "createdAt": created_at,
        "logPath": str(output_root / "desktop.log"),
        "outputRoot": str(output_root),
        "pid": os.getpid(),
    }


@contextmanager
def _task_log_handler(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()


class ChatbotsCompanion:
    def __init__(self, *, port: int, output_root_base: Path, vision_backend: str | None = None):
        self.port = port
        self.output_root_base = output_root_base
        self.vision_backend = vision_backend
        self.bridge = ExtensionBridge(port=port)
        self.bridge.on_log(lambda action, detail: logger.info("bridge %s: %s", action, detail))
        self.vision = VisionLLM(backend=vision_backend)
        self._active_run: asyncio.Task | None = None
        self._latest_task: dict | None = None
        self._started = False
        self._model_preload_task: asyncio.Task | None = None
        self._extension_passive_ready_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._started:
            return
        self.output_root_base.mkdir(parents=True, exist_ok=True)
        await self.bridge.start()
        self._started = True
        self._model_preload_task = asyncio.create_task(
            asyncio.to_thread(self.vision.preload_local_model, CHATBOT_PAGE_SIMPLE_CHECK.local_model_name)
        )
        self._extension_passive_ready_task = asyncio.create_task(self._passive_wait_for_extension())

    async def _passive_wait_for_extension(self) -> None:
        """Let the extension auto-reconnect in the background without waking Chrome."""
        try:
            await self.bridge.wait_for_connection(
                timeout=DEFAULT_CONNECT_TIMEOUT_S,
                warmup_active_tab=False,
            )
            logger.info("Companion passive extension warmup connected")
        except RuntimeError:
            logger.info("Companion passive extension warmup timed out; will connect on demand")

    async def _ensure_extension_ready(self) -> None:
        try:
            await self.bridge.wait_for_connection(
                timeout=FAST_CONNECT_TIMEOUT_S,
                warmup_active_tab=False,
            )
            return
        except RuntimeError:
            logger.info("Companion waking Google Chrome for extension reconnect")
        subprocess.run(["open", "-a", "Google Chrome"], check=True)
        await self.bridge.wait_for_connection(
            timeout=DEFAULT_CONNECT_TIMEOUT_S,
            warmup_active_tab=False,
        )

    async def handle_request(self, request: dict) -> dict:
        action = request.get("action", "")
        if action == "status":
            return {
                "ok": True,
                "status": {
                    "connected": self.bridge._connected.is_set(),  # noqa: SLF001
                    "hasActiveRun": bool(self._active_run and not self._active_run.done()),
                    "latestTask": self._latest_task,
                },
            }
        if action == "latest_task":
            return {"ok": True, "task": self._latest_task}
        if action == "ask_chatbots":
            question = str(request.get("question", "")).strip()
            if not question:
                return {"ok": False, "error": "Question is empty"}
            if self._active_run and not self._active_run.done():
                return {"ok": False, "error": "A chatbot fan-out run is already active"}
            task_id = f"chatbots-{int(time.time() * 1000)}"
            output_root = self.output_root_base / task_id
            task = _task_stub(task_id, question, output_root)
            self._latest_task = task
            self._active_run = asyncio.create_task(
                self._run_chatbots_task(
                    question=question,
                    output_root=output_root,
                    close_windows_on_finish=bool(request.get("closeWindowsOnFinish", False)),
                )
            )
            return {"ok": True, "task": task}
        if action == "shutdown":
            return {"ok": True, "shutdown": True}
        return {"ok": False, "error": f"Unknown action: {action}"}

    async def _run_chatbots_task(self, *, question: str, output_root: Path, close_windows_on_finish: bool) -> None:
        log_path = output_root / "desktop.log"
        with _task_log_handler(log_path):
            try:
                await self._ensure_extension_ready()
                if self._model_preload_task is not None:
                    await self._model_preload_task
                runner = MultiChatRunner(
                    bridge=self.bridge,
                    output_dir=output_root,
                    vision_backend=self.vision_backend,
                    cleanup_orphaned=True,
                    verify_visible_windows=True,
                    close_windows_on_finish=close_windows_on_finish,
                )
                await runner.run(question)
                if self._latest_task:
                    self._latest_task["status"] = "finished"
            except Exception as exc:
                logger.exception("Companion chatbot task failed")
                if self._latest_task:
                    self._latest_task["status"] = "error"
                    self._latest_task["error"] = str(exc)


async def _read_requests(companion: ChatbotsCompanion) -> int:
    ready = {"type": "ready", "pid": os.getpid()}
    if not _emit_json(ready):
        return 0

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        response = await companion.handle_request(request)
        if not _emit_json(response):
            return 0
        if response.get("shutdown"):
            return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Long-lived companion for desktop chatbot fan-out.")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port for the Chrome extension bridge")
    parser.add_argument("--output-root-base", required=True, help="Base output directory for chatbots-* runs")
    parser.add_argument("--vision", default="qwen-local", help="Vision backend to use")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    companion = ChatbotsCompanion(
        port=args.port,
        output_root_base=Path(args.output_root_base),
        vision_backend=args.vision,
    )

    async def _run() -> int:
        await companion.start()
        return await _read_requests(companion)

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
