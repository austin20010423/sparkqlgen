# sparqlgen — A Claude Code-style Wikidata Agent

`sparqlgen` is an interactive CLI agent that takes a natural-language question,
plans a Wikidata SPARQL query through a tool-use loop (entity search →
property search → query → execute), and returns results — all from a REPL
that mirrors Claude Code's UX.

> Built for the GoFeight take-home challenge — Part 1.

---

## Quick start

```bash
git clone <this repo> && cd GoFeight_Interview
uv sync --extra dev          # creates .venv and installs everything

cp .env.example .env
# fill in OPENAI_API_KEY

uv run sparqlgen                                    # interactive REPL
uv run sparqlgen ask "Top 5 cities in Japan by pop" # one-shot
uv run sparqlgen ask --json "..."                   # machine-readable
uv run sparqlgen ask --dry-run "..."                # SPARQL only, no execution
uv run sparqlgen models                             # list providers
uv run pytest                                       # run tests
```

Don't have uv? Install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
or `brew install uv`.

---

## Slash commands

| command | description |
| --- | --- |
| `/help` | List commands |
| `/clear` | Reset conversation context |
| `/exit` (`/quit`) | Leave the REPL |
| `/model <id>` | Switch OpenAI model (`gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, ...) |
| `/sparql <query>` | Run raw SPARQL, skip the LLM |
| `/explain` | Have the LLM explain the most recent SPARQL it produced |
| `/export <file>` | Save last results as `.csv` or `.json` |

---

## Architecture

```
                ┌──────────────────────────────────────────────┐
   user ──► REPL (prompt_toolkit) ──► SessionState (history)   │
                                          │                     │
                                          ▼                     │
                                   Agent loop (≤8 iter)         │
                                          │                     │
                              ┌───────────┴───────────┐         │
                              ▼                       ▼         │
                       Provider (Claude/GPT/Open)  Tools         │
                       — tool_use roundtrip ──► search_entity   │
                                                search_property│
                                                get_entity      │
                                                run_sparql      │
                                          │                     │
                                          ▼                     │
                                   Hardening layer              │
                                   (assert_safe, auto_limit,    │
                                    syntax check, conflict)     │
                                          │                     │
                                          ▼                     │
                                  Wikidata HTTP / SPARQL        │
                                          │                     │
                                          ▼                     │
                                  rich rendering ◄──────────────┘
```

Key pieces:
- **`providers.py`** — `OpenAIProvider` exposing the same `chat(messages, tools, system)`
  contract. Switching the underlying model id is one `/model gpt-4o-mini` away.
  The `Provider` base class makes it easy to add Anthropic / Gemini later for Part 2.
- **`tools.py`** — provider-agnostic tool schema, translated to Anthropic /
  OpenAI shape on the fly.
- **`agent.py`** — drives the tool-use loop; each `run_sparql` call passes
  through `permission_check` so the REPL can ask before executing.
- **`hardening.py`** — write-op blocker, auto-LIMIT, brace-balance check,
  conflict detection, language detection, prompt-injection prefilter.
- **`wikidata.py`** — `wbsearchentities`, `wbgetentities`, `SPARQLWrapper`
  execution with retry + sqlite cache.

---

## Examples

**Single-turn**
```
sparqlgen ❯ Nobel laureates in physics born in Germany
⏺ search_entity(query="Nobel Prize in Physics", lang="en")
  └─ Q38104 (Nobel Prize in Physics) | ...
⏺ search_entity(query="Germany", lang="en")
  └─ Q183 (Germany)
⏺ run_sparql(...)
  └─ ✓ 28 rows returned (0.42s)
```

**Multi-turn coreference**
```
sparqlgen ❯ Movies directed by Christopher Nolan
sparqlgen ❯ now only after 2010
sparqlgen ❯ /export nolan.csv
```

**Multilingual**
```
sparqlgen ❯ 台灣的總統有誰
# detect_lang -> zh, search_entity uses language="zh"
```

---

## Failure cases — fixed in Phase 3

The full break-it set is in [`docs/failures.md`](docs/failures.md). High-impact
fixes that Phase 3 ships:

| ID | Failure | Fix |
| --- | --- | --- |
| A1, A2, C1–C4 | Q-id hallucination, typos | System prompt forces `search_entity` first; `wbsearchentities` does the fuzzy match |
| B1, B2, B3 | Conflicting constraints | `hardening.detect_conflict` catches contradictions before the agent runs |
| D1–D5 | Non-English input | `hardening.detect_lang` injects a language hint into the user message; `search_entity` uses correct `language=` |
| G1–G3 | Time-qualified property mistakes | System prompt explicitly mentions `pq:P580/P582` qualifiers |
| I1–I4 | Prompt injection / write ops | `looks_like_injection` prefilter + `assert_safe` regex blocker reject `INSERT/DELETE/LOAD/CLEAR/DROP/CREATE` before the SPARQL endpoint sees them |
| L1–L3 | Pathologically large results | `auto_limit` adds `LIMIT 100` to any SELECT without an explicit limit |
| Syntax errors | Brace/paren imbalance | `basic_sparql_validate` short-circuits and tells the agent to fix |

---

## Failure cases that remain — and why they are *fundamentally* hard

These are not engineering bugs. They are limits of the problem itself.

1. **Entity ambiguity is underdetermined in single-turn mode.**
   `Apple revenue` → company. `Apple recipes` → fruit. Without a longer
   conversation history or a user profile, the input alone does not contain
   enough information to pin down a Q-id. We can surface candidates, but a
   *deterministic* correct answer is information-theoretically impossible.

2. **Open-world property space.** Wikidata has tens of thousands of properties.
   `search_property` retrieves a top-K of those, but if the *correct* property
   isn't in the top-K (say a niche property like P3722, "preceded by"), the
   model is forced to guess. RAG mitigates this; it does not eliminate it.

3. **Closed-world inference over an open-world graph.**
   *"Countries without a coastline"* requires a closed-world assumption — we
   need to be sure the country *has no* statement saying it touches a coast.
   Wikidata is open-world: missing data ≠ negative fact. `FILTER NOT EXISTS`
   only works when the *negative fact* itself has been recorded somewhere
   (e.g. an explicit `landlocked = true` claim), which isn't always true.

4. **Temporal granularity.** *"US presidents during the Cold War"* — Cold War
   start/end years vary by historian (1947–1991 vs 1945–1989). Whatever the
   model picks, a ground-truth labeler may have picked differently, and both
   are defensible.

5. **Schema-dependent aggregation semantics.** *"Average lifespan of Roman
   emperors"* — does "Roman emperor" include co-emperors, usurpers, the
   Tetrarchy? Does lifespan use abdication or death? These are schema
   decisions, not language decisions; the question doesn't actually have a
   single answer.

6. **Cross-language label asymmetry.** Wikidata's English label coverage
   dominates; many entities have no `zh-tw` or `ja` label, so a Chinese-only
   query for a niche entity may simply not match. We mitigate by retrying
   `wbsearchentities` in English using a transliterated form, but this is
   lossy.

7. **Tool-loop is not a free lunch.** Adding more tool calls increases cost,
   latency, and the surface for compounding errors — a wrong intermediate Q-id
   propagates into the next call. Capping at 8 iterations is a tradeoff.

---

## Project layout

```
src/sparqlgen/
  cli.py            # Typer entry — interactive | ask | models
  repl.py           # Read-Eval-Print loop
  agent.py          # Tool-use agent loop (≤8 iterations)
  providers.py      # Claude / OpenAI / OpenAI-compatible providers
  tools.py          # Tool schema + dispatch
  wikidata.py       # MediaWiki API + SPARQL wrappers
  hardening.py      # Phase 3 guards (assert_safe, auto_limit, conflict, lang)
  rendering.py      # rich tables, panels, banners
  commands.py       # Slash command handlers
  state.py          # SessionState
  config.py         # pydantic-settings
  prompts.py        # System prompt with schema hints + few-shot
docs/
  failures.md       # Phase 2 break-it catalog (20+ adversarial cases)
tests/
  test_hardening.py # Phase 3 guard tests
```

---

## Roadmap

- [ ] Streaming-token output (currently buffered per turn)
- [ ] `--resume <session>` to reload a saved REPL transcript
- [ ] sqlite cache eviction policy
- [ ] Asciinema demo

---

## Connection to Part 2

`sparqlgen ask --json --dry-run "..."` produces SPARQL without execution,
which is exactly what the Part-2 evaluation pipeline needs to compare model
outputs against ground truth. Part 2 requires an open-weight + closed-source
mix; the `Provider` base class lets us bolt on Anthropic / Gemini / Groq-hosted
Llama next to the existing OpenAI provider.
