# Architecture

## Component diagram

```
              ┌────────────────────────────────────────┐
              │            sparqlgen CLI               │
              │   (Typer)  →  REPL  /  ask one-shot    │
              └───────────────┬────────────────────────┘
                              │ user line
                              ▼
                   ┌──────────────────┐
                   │   commands.py    │  /help /clear /model /sparql ...
                   └─────────┬────────┘
                             │ (not a slash command)
                             ▼
                ┌──────────────────────────┐
                │       agent.py           │ ◄── pre-checks: detect_conflict,
                │  tool-use loop, ≤ 8 iter │     detect_lang, looks_like_injection
                └─────────┬────────────────┘
                          │ messages + tool schema
                          ▼
                ┌──────────────────────────┐
                │      providers.py        │
                │  OpenAIProvider          │
                │  (any OpenAI model id)   │
                └─────────┬────────────────┘
                          │ tool_calls
                          ▼
                ┌──────────────────────────┐
                │       tools.py           │
                │  search_entity,          │
                │  search_property,        │
                │  get_entity, run_sparql  │
                └─────────┬────────────────┘
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
      ┌──────────────┐        ┌──────────────────┐
      │  hardening   │        │    cache.py      │
      │  assert_safe │        │  sqlite (kv,ttl) │
      │  auto_limit  │        └──────────────────┘
      │  validate    │
      └──────┬───────┘
             ▼
   ┌────────────────────┐
   │     wikidata.py    │
   │  MediaWiki API +   │
   │  SPARQLWrapper     │
   └─────────┬──────────┘
             ▼
       Wikidata HTTP

   results bubble back up → rendering.py → console
```

## Why these design choices

### Tool-use agent loop, not single-shot

A naive design is "prompt LLM with the question, get SPARQL back, run it."
It fails badly on entity ids: every Wikidata Q-id is essentially a memorized
fact, and LLMs hallucinate them at non-trivial rates. A tool-use loop forces
the model to *look up* Q-ids and P-ids before composing the query. This
trades latency for correctness, but in practice cuts entity-link failures
from "frequently wrong" to "almost never wrong on common entities."

### Provider abstraction

Part 1 ships with `OpenAIProvider` only. The `Provider` base class isolates
the tool-call message-shape differences between vendors, so adding Anthropic
or Gemini for Part 2's open-weight + closed-source mix is a single new
subclass without touching the agent loop.

### Hardening as a *separate* layer

The agent loop is provider-controlled; we don't trust the LLM to enforce
read-only or LIMIT-100 itself. Phase 3 puts those enforcements in
`hardening.py` so they fire deterministically on every `run_sparql` call,
regardless of whether the model "remembered" to add them.

### Caching

Wikidata's `wbsearchentities` is the hottest call (every query touches it
≥1 time). A 7-day sqlite TTL cache cuts repeat-question latency to ~0 and
plays nicely with the eval pipeline in Part 2 where we re-run the same 30
inputs across multiple models.
