"""Stream text output from the local Qwen MLX model.

Usage:
    python tests/manual_local_llm_stream.py
    python tests/manual_local_llm_stream.py --prompt "请用中文总结本地模型的优缺点。"
    python tests/manual_local_llm_stream.py --model Qwen3.5-2B-6bit
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mlx_lm import load, stream_generate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from socai.perception.local_llm import DEFAULT_LOCAL_MODEL, WEIGHTS_DIR


def resolve_model_path(model_name: str) -> str:
    candidate = WEIGHTS_DIR / model_name
    if candidate.is_dir():
        return str(candidate)
    return model_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream text output from a local Qwen MLX model")
    parser.add_argument(
        "--model",
        default=DEFAULT_LOCAL_MODEL,
        help=f"Model directory name under {WEIGHTS_DIR} or a full path",
    )
    parser.add_argument(
        "--prompt",
        default="请用中文写一段约 180 字的说明，介绍本地大模型的优点和局限。",
        help="Prompt to send to the local model",
    )
    parser.add_argument("--max-tokens", type=int, default=220, help="Maximum generation tokens")
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Enable Qwen thinking mode. Disabled by default for cleaner streamed output.",
    )
    args = parser.parse_args()

    model_path = resolve_model_path(args.model)
    if not Path(model_path).exists() and "/" not in args.model:
        print(f"ERROR: local model not found: {args.model}", file=sys.stderr)
        print(f"Expected directory: {WEIGHTS_DIR / args.model}", file=sys.stderr)
        return 1

    model, tokenizer = load(model_path)
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": args.prompt}],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=args.enable_thinking,
    )

    started = time.perf_counter()
    for chunk in stream_generate(model, tokenizer, prompt=prompt, max_tokens=args.max_tokens):
        text = getattr(chunk, "text", "")
        if text:
            print(text, end="", flush=True)
    elapsed = time.perf_counter() - started
    print()
    print(f"\n[done in {elapsed:.2f}s]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
