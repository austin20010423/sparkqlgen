"""Auto-compaction memory.

When the OpenAI conversation history grows past a soft token budget, we
summarize the older portion into a single synthetic user message and keep the
most recent few user-initiated turns verbatim. The agent never notices a
discontinuity — it just sees its own context get shorter.

Constraint: OpenAI's chat-completions format requires every `role: tool`
message to be preceded by an `assistant` message that contains the matching
`tool_call_id` in its `tool_calls`. We therefore only cut at boundaries where
the next message is a fresh user turn (`role == "user"` without
`tool_call_id`), so we never split a tool-call group in half.
"""

from __future__ import annotations

import json
from typing import Any

from .providers import Provider


# Tunables — order of magnitude, not precise.
APPROX_CHARS_PER_TOKEN = 4
SOFT_LIMIT_TOKENS = 8000          # trigger compaction once estimated tokens exceed this
KEEP_LAST_USER_TURNS = 2          # keep this many recent user turns verbatim
SUMMARY_TARGET_TOKENS = 400       # how long the summary itself should be


def estimate_tokens(history: list[dict[str, Any]]) -> int:
    """Cheap char-based token estimate. Within ±20% for English/Chinese mix."""
    total_chars = 0
    for msg in history:
        c = msg.get("content")
        if isinstance(c, str):
            total_chars += len(c)
        elif isinstance(c, list):
            total_chars += len(json.dumps(c, ensure_ascii=False))
        if msg.get("tool_calls"):
            total_chars += len(json.dumps(msg["tool_calls"], ensure_ascii=False))
    return total_chars // APPROX_CHARS_PER_TOKEN


def _is_user_turn_start(msg: dict[str, Any]) -> bool:
    """A real user message — not a `role: tool` reply, not a synthetic note."""
    return msg.get("role") == "user" and "tool_call_id" not in msg


def find_cut_point(history: list[dict[str, Any]], keep_last_turns: int) -> int | None:
    """Return an index `i` such that history[:i] is safe to summarize.

    Returns None if there are not enough complete turns to compact.
    """
    user_indices = [i for i, m in enumerate(history) if _is_user_turn_start(m)]
    if len(user_indices) <= keep_last_turns:
        return None
    cut = user_indices[-keep_last_turns]
    return cut if cut > 0 else None


def _format_for_summary(history: list[dict[str, Any]]) -> str:
    """Render a chunk of history as plain text for the summarizer to read."""
    out = []
    for msg in history:
        role = msg.get("role", "?")
        if role == "tool":
            content = msg.get("content", "")
            # tool replies can be massive — clip them
            content = content if len(content) < 800 else content[:780] + "…(truncated)"
            out.append(f"[tool result] {content}")
            continue
        if role == "assistant":
            text = msg.get("content") or ""
            calls = msg.get("tool_calls") or []
            if calls:
                names = ", ".join(
                    f"{tc['function']['name']}({tc['function'].get('arguments', '')[:120]})"
                    for tc in calls
                )
                out.append(f"[assistant tool_calls] {names}")
            if text:
                out.append(f"[assistant] {text}")
            continue
        if role == "user":
            out.append(f"[user] {msg.get('content', '')}")
    return "\n".join(out)


_SUMMARY_SYSTEM = (
    "You are a precise summarizer of an AI agent's tool-use conversation about Wikidata. "
    "Your summary will be fed back into the agent as compressed context. "
    "Be concrete and dense — names, Q-ids, P-ids, numbers, languages, partial query results, "
    "user constraints. Preserve anything the agent might need to answer the *next* user turn."
)


def _summary_prompt(formatted: str) -> str:
    return (
        f"Summarize this earlier portion of the conversation in {SUMMARY_TARGET_TOKENS} tokens "
        "or fewer. Use a few short paragraphs or bullet points — no preamble. Capture:\n"
        "- The user's overall goal and any constraints they stated (language, time range, etc.)\n"
        "- Q-ids and P-ids that have been resolved, with what they refer to\n"
        "- Results that have already been retrieved (counts, top values)\n"
        "- Anything the user accepted/rejected, or any pending follow-up question\n\n"
        f"CONVERSATION:\n{formatted}"
    )


def compact(history: list[dict[str, Any]], provider: Provider) -> str | None:
    """Compact `history` in-place. Returns the summary text, or None if nothing was done."""
    cut = find_cut_point(history, KEEP_LAST_USER_TURNS)
    if cut is None:
        return None

    to_summarize = history[:cut]
    formatted = _format_for_summary(to_summarize)

    # Direct OpenAI call — we don't want tools available during summarization,
    # and provider.chat() unconditionally attaches the tool schema.
    resp = provider.client.chat.completions.create(
        model=provider.model_id,
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": _summary_prompt(formatted)},
        ],
    )
    summary = (resp.choices[0].message.content or "").strip()
    if not summary:
        return None

    history[:cut] = [
        {
            "role": "user",
            "content": f"[Earlier conversation summary — compacted to save context]\n{summary}",
        },
        {
            "role": "assistant",
            "content": "Got it — continuing with that context.",
        },
    ]
    return summary


def maybe_compact(history: list[dict[str, Any]], provider: Provider) -> str | None:
    """Compact only when we've crossed the soft token budget."""
    if estimate_tokens(history) < SOFT_LIMIT_TOKENS:
        return None
    return compact(history, provider)
