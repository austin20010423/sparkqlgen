"""Tool definitions exposed to the LLM via tool-use.

Schema is provider-agnostic; we translate to Anthropic / OpenAI on the fly.
"""

from __future__ import annotations

from typing import Any

from . import wikidata


# (name, description, json schema, python callable)
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_entity",
        "description": (
            "Search Wikidata for an entity (Q-id) by name or alias. "
            "ALWAYS call this before writing a SPARQL query that mentions a real-world entity, "
            "to avoid hallucinating Q-ids. Returns top candidates with id, label, description."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or alias to search for."},
                "lang": {
                    "type": "string",
                    "description": "Language code (en, zh, ja, es, ...)",
                    "default": "en",
                },
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        "fn": lambda query, lang="en", limit=5: wikidata.search_entity(query, lang, limit),
    },
    {
        "name": "search_property",
        "description": (
            "Search Wikidata for a property (P-id) by description, e.g. 'place of birth' -> P19. "
            "Use this whenever you are unsure which property id encodes a relation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "lang": {"type": "string", "default": "en"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        "fn": lambda query, lang="en", limit=5: wikidata.search_property(query, lang, limit),
    },
    {
        "name": "get_entity",
        "description": (
            "SCHEMA DISCOVERY ONLY. Returns the list of property ids an entity has claims for, "
            "so you know which P-ids are valid for this entity. Does NOT return the label, "
            "description, or any user-visible data. Never use the output of this tool to compose "
            "your final answer to the user — you MUST call run_sparql afterwards to fetch the "
            "actual content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "qid": {"type": "string"},
                "lang": {"type": "string", "default": "en"},
            },
            "required": ["qid"],
        },
        "fn": lambda qid, lang="en": wikidata.get_entity(qid, lang),
    },
    {
        "name": "run_sparql",
        "description": (
            "Execute a SPARQL query against the Wikidata endpoint and return the result rows. "
            "Always include a LIMIT (default 100) unless the user explicitly asked for everything. "
            "Read-only queries only — INSERT/DELETE/LOAD/CLEAR are blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Full SPARQL query."},
            },
            "required": ["query"],
        },
        "fn": lambda query: wikidata.run_sparql(query),
    },
]


def get_tool(name: str) -> dict[str, Any] | None:
    return next((t for t in TOOLS if t["name"] == name), None)


def to_anthropic_schema() -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in TOOLS
    ]


def to_openai_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOLS
    ]
