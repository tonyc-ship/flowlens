"""LLM backend abstraction for the agent loop.

Provides a common interface for hosted Anthropic/OpenAI models and local MLX
inference, so the agent loop can use either backend transparently.
"""

from __future__ import annotations

import json
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.auth import (
    API_STYLE_OPENAI_COMPAT,
    METHOD_API_KEY,
    PROVIDER_ANTHROPIC,
    PROVIDER_KIMI,
    PROVIDER_OPENAI,
    PROVIDER_QWEN,
    default_model_for_provider,
    provider_config,
    resolve_model_provider,
    resolve_provider_auth,
)


@dataclass
class ToolCall:
    """A parsed tool call from the LLM response."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    """Normalized response from any backend."""
    text_blocks: list[str]       # text segments from the response
    tool_calls: list[ToolCall]   # parsed tool calls
    stop_reason: str             # "end_turn", "tool_use", "max_tokens"
    input_tokens: int = 0
    output_tokens: int = 0
    raw: object = None           # original response object (for debugging)
    # Optional detailed timing (local backend only)
    metrics: dict = field(default_factory=dict)


_ASSISTANT_TEXT_MAX_CHARS = 320
_TOOL_RESULT_TEXT_MAX_CHARS = 2200


def _compact_json_value(value):
    if isinstance(value, dict):
        preferred_order = [
            "ok", "error", "message", "site", "action", "entity_type", "level",
            "query", "count", "state", "page_state", "result", "cards", "entity",
            "title", "author", "note_id", "url", "likes", "favorites",
            "comments_count", "content_summary", "key_points", "top_comments",
            "cover_description", "transcript_summary", "visual_summary",
            "timing", "plan",
        ]
        keys = [key for key in preferred_order if key in value]
        keys.extend(key for key in value if key not in keys)
        compacted = {}
        for key in keys[:16]:
            compacted[key] = _compact_json_value(value[key])
        return compacted
    if isinstance(value, list):
        return [_compact_json_value(item) for item in value[:5]]
    if isinstance(value, str):
        return value if len(value) <= 320 else value[:320] + "... [truncated]"
    return value


def _compress_text_maybe_json(text: str, max_chars: int = _TOOL_RESULT_TEXT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    try:
        value = json.loads(text)
        compacted = _compact_json_value(value)
        compact_text = json.dumps(compacted, ensure_ascii=False, indent=2)
        if len(compact_text) <= max_chars:
            return compact_text
        return compact_text[:max_chars] + "\n... [truncated]"
    except Exception:
        return text[:max_chars] + "\n... [truncated]"


def _truncate_assistant_text(text: str, max_chars: int = _ASSISTANT_TEXT_MAX_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"


def _screenshot_hint_from_text(text: str) -> str | None:
    match = re.search(r"Screenshot saved to ([^\s]+)", text or "")
    if not match:
        return None
    return match.group(1)


def _summarize_result_blocks_for_history(
    blocks: list[dict],
    *,
    max_chars: int = _TOOL_RESULT_TEXT_MAX_CHARS,
) -> list[dict]:
    parts: list[str] = []
    screenshot_file = None

    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text", ""))
            if not screenshot_file:
                screenshot_file = _screenshot_hint_from_text(text)
            parts.append(_compress_text_maybe_json(text, max_chars=max_chars))
        elif block.get("type") == "image":
            if screenshot_file:
                parts.append(
                    f"[Image omitted from history. Screenshot file: {screenshot_file}. "
                    "Use analyze_screenshot for visual inspection.]"
                )
            else:
                parts.append("[Image omitted from history. Use analyze_screenshot for visual inspection.]")

    combined = "\n\n".join(part for part in parts if part).strip()
    if len(combined) > max_chars:
        combined = _compress_text_maybe_json(combined, max_chars=max_chars)
    return [{"type": "text", "text": combined or "(empty result)"}]


class Backend(ABC):
    """Abstract LLM backend for the agent loop."""

    @abstractmethod
    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        """Send a message and return a normalized response."""
        ...

    @abstractmethod
    def format_assistant_content(self, response: LLMResponse) -> object:
        """Format the response for appending to messages as assistant content."""
        ...

    @abstractmethod
    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[list[dict]],
    ) -> dict:
        """Format tool results for appending to messages as user content."""
        ...


class AnthropicBackend(Backend):
    """Backend using the Anthropic Messages API."""

    def __init__(self, model: str | None = None):
        import anthropic
        self.model = model or default_model_for_provider(PROVIDER_ANTHROPIC)
        credential = resolve_provider_auth(PROVIDER_ANTHROPIC)
        kwargs = {}
        if credential is not None:
            if credential.method == METHOD_API_KEY:
                kwargs["api_key"] = credential.secret
            else:
                kwargs["auth_token"] = credential.secret
        self.client = anthropic.Anthropic(**kwargs)

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        text_blocks = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_blocks.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return LLMResponse(
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            raw=response,
        )

    def format_assistant_content(self, response: LLMResponse) -> object:
        content = []
        for block in response.raw.content:
            if block.type == "text":
                text = _truncate_assistant_text(block.text)
                if text:
                    content.append({"type": "text", "text": text})
            elif block.type == "tool_use":
                content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        return content

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[list[dict]],
    ) -> dict:
        content = []
        for tc, result_blocks in zip(tool_calls, results):
            content.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": _summarize_result_blocks_for_history(result_blocks),
            })
        return {"role": "user", "content": content}


class OpenAIBackend(Backend):
    """Backend using the OpenAI Responses API with function tools."""

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        self.model = model or default_model_for_provider(PROVIDER_OPENAI)
        credential = resolve_provider_auth(PROVIDER_OPENAI)
        kwargs = {}
        if credential is not None:
            kwargs["api_key"] = credential.secret
        self.client = OpenAI(**kwargs)
        self._previous_response_id: str | None = None
        self._consumed_message_count = 0

    @staticmethod
    def _tool_to_openai_schema(tool: dict) -> dict:
        return {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        }

    @staticmethod
    def _input_text_message(role: str, text: str) -> dict:
        return {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text", "text": text}],
        }

    def _message_to_input_items(self, message: dict) -> list[dict]:
        role = str(message.get("role") or "user")
        content = message.get("content")

        if role == "assistant":
            return []

        if isinstance(content, str):
            text = content.strip()
            return [self._input_text_message(role, text)] if text else []

        if isinstance(content, list):
            function_outputs = [
                item for item in content
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            ]
            if function_outputs:
                return function_outputs

            text_parts = [
                str(item.get("text", "")).strip()
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "\n\n".join(part for part in text_parts if part)
            return [self._input_text_message(role, text)] if text else []

        return []

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        new_messages = messages[self._consumed_message_count :]
        input_items: list[dict] = []
        for message in new_messages:
            input_items.extend(self._message_to_input_items(message))

        openai_tools = [self._tool_to_openai_schema(tool) for tool in tools]
        request: dict = {
            "model": self.model,
            "instructions": system,
            "input": input_items,
            "max_output_tokens": max_tokens,
        }
        if self._previous_response_id:
            request["previous_response_id"] = self._previous_response_id
        if openai_tools:
            request["tools"] = openai_tools
            request["parallel_tool_calls"] = True

        response = self.client.responses.create(**request)
        self._previous_response_id = response.id
        self._consumed_message_count = len(messages)

        text_blocks: list[str] = []
        tool_calls: list[ToolCall] = []
        assistant_content: list[dict] = []

        for item in response.output:
            item_type = getattr(item, "type", "")
            if item_type == "message":
                for content in getattr(item, "content", []):
                    if getattr(content, "type", "") != "output_text":
                        continue
                    text = str(getattr(content, "text", "") or "")
                    if not text:
                        continue
                    text_blocks.append(text)
                    truncated = _truncate_assistant_text(text)
                    if truncated:
                        assistant_content.append({"type": "text", "text": truncated})
            elif item_type == "function_call":
                raw_arguments = str(getattr(item, "arguments", "") or "{}")
                try:
                    parsed_arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    parsed_arguments = {}
                call_id = str(getattr(item, "call_id", "") or getattr(item, "id", "") or uuid.uuid4())
                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        name=str(getattr(item, "name", "")),
                        input=parsed_arguments if isinstance(parsed_arguments, dict) else {},
                    )
                )
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": str(getattr(item, "name", "")),
                        "input": parsed_arguments if isinstance(parsed_arguments, dict) else {},
                    }
                )

        usage = getattr(response, "usage", None)
        return LLMResponse(
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            raw={"assistant_content": assistant_content, "response": response},
        )

    def format_assistant_content(self, response: LLMResponse) -> object:
        raw = response.raw if isinstance(response.raw, dict) else {}
        return list(raw.get("assistant_content") or [])

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[list[dict]],
    ) -> dict:
        content = []
        for tc, result_blocks in zip(tool_calls, results):
            summarized = _summarize_result_blocks_for_history(result_blocks)
            output = "\n\n".join(
                str(block.get("text", "")).strip()
                for block in summarized
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            content.append(
                {
                    "type": "function_call_output",
                    "call_id": tc.id,
                    "output": output or "(empty result)",
                }
            )
        return {"role": "user", "content": content}


class OpenAICompatibleBackend(Backend):
    """Backend for any OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Used by Chinese hosted vendors (Moonshot/Kimi, Alibaba Qwen /
    DashScope) which all expose OpenAI-compatible APIs with function calling.

    Subclasses preset the provider's ``base_url`` and default model so adding a
    new vendor usually means 3 lines: ``PROVIDER_*``, a :class:`ProviderConfig`
    entry in ``flowlens.core.auth``, and a subclass here.
    """

    PROVIDER: str = ""
    PRESERVE_REASONING_CONTENT = False

    def __init__(self, model: str | None = None, provider: str | None = None):
        from openai import OpenAI

        provider_name = provider or self.PROVIDER
        config = provider_config(provider_name)
        if config is None:
            raise ValueError(f"Unknown OpenAI-compatible provider: {provider_name!r}")

        self.provider = config.name
        self.model = model or default_model_for_provider(self.provider)

        credential = resolve_provider_auth(self.provider)
        if credential is None or not credential.secret:
            hint = config.env_var_hint or f"{self.provider.upper()}_API_KEY"
            raise RuntimeError(
                f"No API key found for {config.display_name}. "
                f"Set ${hint} or run `flowlens auth set {self.provider} api_key`."
            )

        client_kwargs: dict = {"api_key": credential.secret}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self.client = OpenAI(**client_kwargs)

    @staticmethod
    def _tool_to_schema(tool: dict) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def _blocks_to_text(blocks: list[dict]) -> str:
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "image":
                parts.append("[image omitted — use analyze_screenshot to inspect visually]")
        return "\n\n".join(p for p in parts if p).strip() or "(empty)"

    @staticmethod
    def _message_extra_value(message: object, key: str) -> object:
        value = getattr(message, key, None)
        if value is not None:
            return value
        extra = getattr(message, "model_extra", None)
        if isinstance(extra, dict):
            return extra.get(key)
        return None

    def _request_extra_body(self, *, has_tools: bool) -> dict:
        return {}

    def _message_to_chat(self, message: dict) -> list[dict]:
        """Convert the agent loop's message dicts into chat.completions format."""
        role = str(message.get("role") or "user")
        content = message.get("content")

        if role == "assistant":
            # content is the list we produced in format_assistant_content
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            reasoning_content: str | None = None
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        text_parts.append(str(item.get("text", "")))
                    elif item.get("type") == "reasoning_content":
                        reasoning_content = str(item.get("text") or "")
                    elif item.get("type") == "tool_use":
                        tool_calls.append({
                            "id": str(item.get("id") or ""),
                            "type": "function",
                            "function": {
                                "name": str(item.get("name") or ""),
                                "arguments": json.dumps(item.get("input") or {}, ensure_ascii=False),
                            },
                        })
            elif isinstance(content, str):
                text_parts.append(content)

            msg: dict = {"role": "assistant"}
            text_joined = "\n".join(p for p in text_parts if p).strip()
            msg["content"] = text_joined or None
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if self.PRESERVE_REASONING_CONTENT and tool_calls:
                msg["reasoning_content"] = reasoning_content or ""
            return [msg]

        if role == "user":
            # User content may be plain text (task) or a list including tool_result blocks.
            if isinstance(content, str):
                text = content.strip()
                return [{"role": "user", "content": text}] if text else []

            if isinstance(content, list):
                tool_messages: list[dict] = []
                text_parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "tool_result":
                        result_blocks = item.get("content") or []
                        if not isinstance(result_blocks, list):
                            result_blocks = [{"type": "text", "text": str(result_blocks)}]
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": str(item.get("tool_use_id") or ""),
                            "content": self._blocks_to_text(result_blocks),
                        })
                    elif item.get("type") == "text":
                        text_parts.append(str(item.get("text", "")))
                result: list[dict] = list(tool_messages)
                joined = "\n".join(p for p in text_parts if p).strip()
                if joined:
                    result.append({"role": "user", "content": joined})
                return result

        return []

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        chat_messages: list[dict] = [{"role": "system", "content": system}]
        for message in messages:
            chat_messages.extend(self._message_to_chat(message))

        chat_tools = [self._tool_to_schema(tool) for tool in tools]
        request: dict = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
        }
        if chat_tools:
            request["tools"] = chat_tools
            request["tool_choice"] = "auto"
        extra_body = self._request_extra_body(has_tools=bool(chat_tools))
        if extra_body:
            request["extra_body"] = extra_body

        response = self.client.chat.completions.create(**request)
        choice = response.choices[0]
        message = choice.message
        reasoning_content = self._message_extra_value(message, "reasoning_content")

        text_blocks: list[str] = []
        if getattr(message, "content", None):
            text_blocks.append(str(message.content))

        tool_calls: list[ToolCall] = []
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        for tc in raw_tool_calls:
            fn = getattr(tc, "function", None)
            name = str(getattr(fn, "name", "") or "")
            raw_args = str(getattr(fn, "arguments", "") or "{}")
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {}
            call_id = str(getattr(tc, "id", "") or uuid.uuid4().hex)
            tool_calls.append(ToolCall(
                id=call_id,
                name=name,
                input=parsed_args if isinstance(parsed_args, dict) else {},
            ))

        finish = str(choice.finish_reason or "")
        if finish == "tool_calls":
            stop_reason = "tool_use"
        elif finish == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        usage = getattr(response, "usage", None)
        return LLMResponse(
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            raw={"response": response, "reasoning_content": reasoning_content},
        )

    def format_assistant_content(self, response: LLMResponse) -> object:
        content: list[dict] = []
        raw = response.raw if isinstance(response.raw, dict) else {}
        if self.PRESERVE_REASONING_CONTENT and raw.get("reasoning_content") is not None:
            content.append({
                "type": "reasoning_content",
                "text": str(raw["reasoning_content"] or ""),
            })
        for text in response.text_blocks:
            truncated = _truncate_assistant_text(text)
            if truncated:
                content.append({"type": "text", "text": truncated})
        for tc in response.tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        return content

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[list[dict]],
    ) -> dict:
        content = []
        for tc, result_blocks in zip(tool_calls, results):
            content.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": _summarize_result_blocks_for_history(result_blocks),
            })
        return {"role": "user", "content": content}


class KimiBackend(OpenAICompatibleBackend):
    """Moonshot AI / Kimi (https://api.moonshot.cn) — kimi-k2 and moonshot-v1 family."""

    PROVIDER = PROVIDER_KIMI
    PRESERVE_REASONING_CONTENT = True

    def _request_extra_body(self, *, has_tools: bool) -> dict:
        if not has_tools:
            return {}
        if self.model.startswith("kimi-k2.5"):
            return {"thinking": {"type": "disabled"}}
        return {}


class QwenBackend(OpenAICompatibleBackend):
    """Alibaba Tongyi Qianwen / DashScope OpenAI-compatible endpoint."""

    PROVIDER = PROVIDER_QWEN
    PRESERVE_REASONING_CONTENT = True

    def _request_extra_body(self, *, has_tools: bool) -> dict:
        if not has_tools:
            return {}
        # DashScope enables Qwen thinking on some models by default. With tools,
        # its validator then requires reasoning_content in every assistant
        # tool-call history message. The browser agent does not need hidden
        # reasoning for mechanical tool turns, so disable it for tool requests.
        return {"enable_thinking": False}


class LocalBackend(Backend):
    """Backend using local MLX inference with text-based tool calling.

    Tool definitions are injected into the system prompt. Tool calls are
    parsed from <tool_call>...</tool_call> tags in the model output.
    Tool results are formatted as text messages.
    """

    # Maximum context tokens (estimated) before older messages get trimmed.
    MAX_CONTEXT_TOKENS = 8000

    def __init__(self, model_name: str = "Qwen3.5-9B-MLX-4bit"):
        from ..perception.local_llm import LocalLLM
        self.model_name = model_name
        self.llm = LocalLLM(model_name=model_name, think=("Qwen" in model_name))

    def _format_tools_for_prompt(self, tools: list[dict]) -> str:
        """Format tool definitions as text for the system prompt."""
        lines = [
            "\n## Available Tools\n",
            "Call a tool by writing a <tool_call> block with JSON containing 'name' and 'arguments'.\n",
            "Example:",
            '<tool_call>{"name": "screenshot", "arguments": {"label": "initial"}}</tool_call>\n',
            "You can (and SHOULD) emit multiple <tool_call> blocks in one response "
            "when the next actions are obvious — e.g. close_note + click_card + "
            "extract_note_content in one turn. Batching tool calls is much faster "
            "than one-per-turn.\n",
            "When done with the task, respond with text only (no tool_call blocks).\n",
            "### Tool Definitions\n",
        ]
        for tool in tools:
            schema = tool.get("input_schema", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])
            lines.append(f"**{tool['name']}**: {tool['description']}")
            if props:
                params_desc = []
                for pname, pdef in props.items():
                    req = " (required)" if pname in required else ""
                    ptype = pdef.get("type", "any")
                    pdesc = pdef.get("description", "")
                    params_desc.append(f"  - {pname} ({ptype}{req}): {pdesc}")
                lines.append("  Parameters:")
                lines.extend(params_desc)
            lines.append("")
        return "\n".join(lines)

    def _build_full_prompt(self, system: str, tools: list[dict]) -> str:
        """Combine system prompt with tool definitions."""
        tool_text = self._format_tools_for_prompt(tools)
        return system + "\n" + tool_text

    def _parse_tool_calls(self, text: str) -> tuple[list[str], list[ToolCall]]:
        """Parse <tool_call>...</tool_call> blocks from model output.

        Also handles unclosed <tool_call> at end of output (common with
        max_tokens cutoff).

        Returns (text_segments, tool_calls).
        """
        # Also match unclosed <tool_call> at end of string
        pattern = r'<tool_call>(.*?)(?:</tool_call>|$)'
        matches = list(re.finditer(pattern, text, re.DOTALL))

        if not matches:
            return [text.strip()], []

        tool_calls = []
        text_segments = []

        # Text before first match
        pre_text = text[:matches[0].start()].strip()
        if pre_text:
            text_segments.append(pre_text)

        for i, match in enumerate(matches):
            try:
                data = json.loads(match.group(1).strip())
                name = str(data.get("name", "")).strip()
                if not name:
                    raise KeyError("missing tool name")
                tc = ToolCall(
                    id=f"local_{uuid.uuid4().hex[:8]}",
                    name=name,
                    input=data.get("arguments", {}),
                )
                tool_calls.append(tc)
            except (json.JSONDecodeError, KeyError) as e:
                text_segments.append(f"[Failed to parse tool_call: {e}]")

            # Text between matches
            if i + 1 < len(matches):
                between = text[match.end():matches[i + 1].start()].strip()
                if between:
                    text_segments.append(between)

        # Text after last match
        post_text = text[matches[-1].end():].strip()
        if post_text:
            text_segments.append(post_text)

        return text_segments, tool_calls

    def _messages_to_prompt(
        self, system: str, messages: list[dict], tools: list[dict]
    ) -> str:
        """Convert the full message history into a single text prompt.

        Uses the Qwen chat template via the processor, injecting tool
        definitions into the system message.
        """
        full_system = self._build_full_prompt(system, tools)

        # Build chat messages for the template
        chat_messages = [{"role": "system", "content": full_system}]

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, str):
                    chat_messages.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Tool results formatted as text
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "tool_result":
                                tool_id = item.get("tool_use_id", "")
                                result_blocks = item.get("content", [])
                                result_text = self._result_blocks_to_text(result_blocks)
                                parts.append(f"[Tool result for {tool_id}]:\n{result_text}")
                            elif item.get("type") == "text":
                                parts.append(item.get("text", ""))
                        elif isinstance(item, str):
                            parts.append(item)
                    chat_messages.append({"role": "user", "content": "\n\n".join(parts)})

            elif role == "assistant":
                if isinstance(content, str):
                    chat_messages.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    # Reconstruct assistant text including tool calls
                    parts = []
                    for item in content:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict):
                            if item.get("type") == "text":
                                parts.append(item.get("text", ""))
                            elif item.get("type") == "tool_call":
                                tc = item.get("tool_call", {})
                                parts.append(
                                    f'<tool_call>{json.dumps({"name": tc["name"], "arguments": tc["input"]}, ensure_ascii=False)}</tool_call>'
                                )
                    chat_messages.append({"role": "assistant", "content": "\n".join(parts)})

        # Apply chat template
        self.llm._ensure_loaded()
        return self.llm.apply_chat_template(
            chat_messages,
            add_generation_prompt=True,
        )

    @staticmethod
    def _result_blocks_to_text(blocks: list[dict]) -> str:
        """Convert content blocks to plain text (skip images for local model)."""
        parts = []
        for block in blocks:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block["text"])
                elif block.get("type") == "image":
                    parts.append("[image omitted for local model]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts) if parts else "(empty result)"

    @classmethod
    def _compress_tool_result_text(cls, text: str, max_chars: int = 2200) -> str:
        return _compress_text_maybe_json(text, max_chars=max_chars)

    def _estimate_tokens(self, text: str) -> int:
        """Conservative token estimate (chars / 2 for CJK-heavy text)."""
        return max(1, len(text) // 2)

    def _trim_messages(self, messages: list[dict]) -> list[dict]:
        """Trim older messages if context is too large.

        Keeps the first message (task) and as many recent messages as fit
        within MAX_CONTEXT_TOKENS. Inserts a summary note when trimming.
        """
        if not messages:
            return messages

        total = sum(self._estimate_tokens(str(m.get("content", ""))) for m in messages)
        if total <= self.MAX_CONTEXT_TOKENS:
            return messages

        # Always keep first message (the task)
        first = messages[0]
        rest = messages[1:]

        # Keep recent messages from the end
        kept = []
        budget = self.MAX_CONTEXT_TOKENS - self._estimate_tokens(str(first.get("content", "")))
        for msg in reversed(rest):
            cost = self._estimate_tokens(str(msg.get("content", "")))
            if budget - cost < 0 and kept:
                break
            kept.append(msg)
            budget -= cost
        kept.reverse()

        trimmed_count = len(rest) - len(kept)
        if trimmed_count > 0:
            summary = {
                "role": "user",
                "content": f"[{trimmed_count} earlier messages trimmed to save context. Focus on the current state.]",
            }
            return [first, summary] + kept
        return messages

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        # Trim context to prevent quadratic slowdown.
        trimmed = self._trim_messages(messages)
        formatted = self._messages_to_prompt(system, trimmed, tools)
        input_tokens_est = self._estimate_tokens(formatted)

        # Use _generate_with_thinking to capture reasoning separately
        thinking, answer, metrics = self.llm._generate_with_thinking(formatted, max_tokens)
        output_tokens_est = metrics.get("generation_tokens") or (
            self._estimate_tokens(answer) + self._estimate_tokens(thinking)
        )
        if metrics.get("prompt_tokens"):
            input_tokens_est = metrics["prompt_tokens"]

        text_segments, tool_calls = self._parse_tool_calls(answer)

        # Prepend thinking as a text block if present (for reasoning log)
        all_text = []
        if thinking:
            all_text.append(f"[Thinking] {thinking}")
        all_text.extend(t for t in text_segments if t)

        stop_reason = "tool_use" if tool_calls else "end_turn"

        return LLMResponse(
            text_blocks=all_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=input_tokens_est,
            output_tokens=output_tokens_est,
            raw=answer,
            metrics=metrics,
        )

    def format_assistant_content(self, response: LLMResponse) -> object:
        """Store assistant content as a list of dicts for message history."""
        content = []
        for text in response.text_blocks:
            if text.startswith("[Thinking] "):
                continue
            content.append({"type": "text", "text": text})
        for tc in response.tool_calls:
            content.append({
                "type": "tool_call",
                "tool_call": {"name": tc.name, "input": tc.input, "id": tc.id},
            })
        return content

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[list[dict]],
    ) -> dict:
        """Format tool results as a text-based user message."""
        parts = []
        for tc, result_blocks in zip(tool_calls, results):
            result_text = self._compress_tool_result_text(
                self._result_blocks_to_text(result_blocks)
            )
            parts.append(f"[Tool result for {tc.name} ({tc.id})]:\n{result_text}")
        return {"role": "user", "content": "\n\n".join(parts)}


_OPENAI_COMPAT_BACKENDS: dict[str, type[OpenAICompatibleBackend]] = {
    PROVIDER_KIMI: KimiBackend,
    PROVIDER_QWEN: QwenBackend,
}


def create_backend(model: str) -> Backend:
    """Factory: create the appropriate backend for a model identifier.

    Dispatch order:
    1. Local aliases (``qwen-local`` / ``ui-tars-local``) and capitalized MLX
       model names (``Qwen*`` / ``UI-TARS*``) → :class:`LocalBackend`.
    2. Everything else is routed via :func:`resolve_model_provider`, which
       matches the model against ``PROVIDERS[...].model_prefixes``. The
       matched provider's ``api_style`` picks the backend class.
    """
    normalized = str(model or "").strip()

    if normalized == "ui-tars-local" or normalized.startswith("UI-TARS"):
        model_name = normalized if normalized != "ui-tars-local" else "UI-TARS-1.5-7B-6bit"
        return LocalBackend(model_name=model_name)
    if normalized == "qwen-local" or normalized.startswith("Qwen"):
        model_name = normalized if normalized != "qwen-local" else "Qwen3.5-9B-MLX-4bit"
        return LocalBackend(model_name=model_name)

    provider = resolve_model_provider(normalized)
    config = provider_config(provider)

    if config is not None and config.api_style == API_STYLE_OPENAI_COMPAT:
        cls = _OPENAI_COMPAT_BACKENDS.get(provider)
        if cls is None:
            raise ValueError(f"No backend class registered for provider {provider!r}")
        return cls(model=normalized or None)

    if provider == PROVIDER_OPENAI:
        return OpenAIBackend(model=normalized or None)

    return AnthropicBackend(model=normalized or default_model_for_provider(PROVIDER_ANTHROPIC))
