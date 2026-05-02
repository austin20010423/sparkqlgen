"""LLM provider abstraction (OpenAI only)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    text: str | None
    tool_calls: list[ToolCall]
    raw: Any


class Provider:
    name: str = "openai"
    model_id: str = ""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> ChatResponse:
        raise NotImplementedError

    def append_assistant_msg(
        self,
        messages: list[dict[str, Any]],
        assistant_msg: Any,
    ) -> None:
        raise NotImplementedError

    def append_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call: ToolCall,
        result: Any,
    ) -> None:
        raise NotImplementedError


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(
        self,
        model_id: str = "gpt-5.4-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        from openai import OpenAI
        self.model_id = model_id
        self.client = OpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url,
        )

    def chat(self, messages, tools, system):
        from .tools import to_openai_schema
        oa_messages = [{"role": "system", "content": system}] + [
            {k: v for k, v in m.items() if not k.startswith("_")} for m in messages
        ]
        resp = self.client.chat.completions.create(
            model=self.model_id,
            messages=oa_messages,
            tools=to_openai_schema(),
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        tcs: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tcs.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return ChatResponse(text=msg.content, tool_calls=tcs, raw=msg)

    def append_assistant_msg(self, messages, assistant_msg):
        messages.append(
            {
                "role": "assistant",
                "content": assistant_msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (assistant_msg.tool_calls or [])
                ],
            }
        )

    def append_tool_result(self, messages, tool_call, result):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )


# Whitelist of allowed models. All routed to api.openai.com.
OPENAI_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-4o-mini"]
ALLOWED_MODELS = OPENAI_MODELS


def make_provider(model_id: str) -> Provider:
    if model_id not in ALLOWED_MODELS:
        raise ValueError(
            f"model {model_id!r} not allowed. Pick one of: {', '.join(ALLOWED_MODELS)}"
        )
    return OpenAIProvider(model_id=model_id)


def list_providers() -> list[str]:
    return ALLOWED_MODELS
