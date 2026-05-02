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
        import time as _time
        from openai import RateLimitError
        from .tools import to_openai_schema
        oa_messages = [{"role": "system", "content": system}] + [
            {k: v for k, v in m.items() if not k.startswith("_")} for m in messages
        ]
        # Honour the provider's `Retry-After` header on 429s. Groq's free tier
        # caps RPM/TPM and rejects bursts mid-eval; backoff lets a long run
        # finish instead of dropping every case after the cap is hit.
        attempts = 0
        while True:
            try:
                resp = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=oa_messages,
                    tools=to_openai_schema(),
                    tool_choice="auto",
                )
                break
            except RateLimitError as e:
                attempts += 1
                if attempts > 5:
                    raise
                wait = 5
                hdrs = getattr(getattr(e, "response", None), "headers", None)
                if hdrs:
                    try:
                        wait = max(wait, int(float(hdrs.get("retry-after", 5))))
                    except (TypeError, ValueError):
                        pass
                _time.sleep(min(wait, 60))
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


# Whitelist of allowed models.
# OpenAI proper goes to api.openai.com. Groq-hosted open-weight models go to
# api.groq.com via Groq's OpenAI-compatible endpoint.
OPENAI_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-4o-mini"]
GROQ_MODELS = ["openai/gpt-oss-120b"]
ALLOWED_MODELS = OPENAI_MODELS + GROQ_MODELS


def _is_groq(model_id: str) -> bool:
    return model_id in GROQ_MODELS


def make_provider(model_id: str) -> Provider:
    if model_id not in ALLOWED_MODELS:
        raise ValueError(
            f"model {model_id!r} not allowed. Pick one of: {', '.join(ALLOWED_MODELS)}"
        )
    if _is_groq(model_id):
        if not settings.groq_api_key:
            raise ValueError(
                f"GROQ_API_KEY is not set. Add it to .env to use {model_id!r}."
            )
        return OpenAIProvider(
            model_id=model_id,
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
        )
    return OpenAIProvider(model_id=model_id)


def list_providers() -> list[str]:
    return ALLOWED_MODELS
