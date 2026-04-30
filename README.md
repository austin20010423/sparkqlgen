# sparqlgen — A cli Wikidata Agent

![sparqlgen banner](docs/assets/banner.png)

`sparqlgen` is an interactive CLI agent that takes a natural-language question,
plans a Wikidata SPARQL query through a tool-use loop (entity search →
property search → query → execute), and returns results.

---

## Quick start

```bash
git clone <this repo> && cd <this repo>
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
| `/model <id>` | Switch OpenAI model (`gpt-5.4`, `gpt-4o`, `gpt-4o-mini`) |
| `/sparql <query>` | Run raw SPARQL, skip the LLM |
| `/explain` | Have the LLM explain the most recent SPARQL it produced |
| `/export <file>` | Save last results as `.csv` or `.json` |
| `/compact` | Manually summarize older conversation to free up context |

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

## System architecture

### Request flow (one user turn)

```
user line
   │
   ▼
cli.py / repl.py        ── parse one-shot vs REPL, capture stdin
   │
   ▼
commands.py             ── handle /help /clear /model /sparql /explain /export /compact
   │ (only non-slash inputs continue)
   ▼
agent.py: interceptor   ── deterministic pre-checks (no LLM yet)
   │   ├─ K3 no-context coreference  → short-circuit reply
   │   ├─ B  conflict detection      → short-circuit reply
   │   ├─ D  language detection      → annotate prompt
   │   ├─ I  injection prefilter     → annotate prompt
   │   ├─ G  temporal qualifier hint → annotate prompt
   │   └─ H  open-world negation hint→ annotate prompt
   │
   ▼
agent.py: tool-use loop (≤ 8 iterations)
   │
   ├──► providers.py.chat(history, tools, SYSTEM_PROMPT)
   │       └─ OpenAI / OpenAI-compatible chat completion w/ tools
   │
   ├──► tools.py dispatch
   │       ├─ search_entity      → wikidata.py wbsearchentities
   │       ├─ search_property    → wikidata.py wbsearchentities (P-id)
   │       ├─ get_entity         → wikidata.py wbgetentities (schema only)
   │       └─ run_sparql ─┐
   │                      │
   │                      ▼
   │            hardening.py     ── assert_safe → auto_limit → validate
   │                      │
   │                      ▼
   │            permission_check (REPL) ── show query, ask Y/n/edit
   │                      │
   │                      ▼
   │            wikidata.py      ── SPARQLWrapper → query.wikidata.org
   │                      │
   │                      ▼
   │            quality_warning  ── 0-rows / all-identical / many-dup detector
   │                      │
   │                      └────► result fed back to LLM in next iteration
   │
   └──► loop exits when model returns text with no tool_calls
              │
              ▼
state.py    ── persist last_sparql, last_rows, last_columns, history
              │
              ▼
rendering.py── rich.Table / Panel → terminal
```

### Key design choices

- **Tool-use loop, not single-shot.** Direct NL → SPARQL hallucinates Q-ids. Forcing `search_entity` / `search_property` first cuts entity-link errors from "frequent" to "rare on common entities." Cost: latency and token spend.
- **Hardening as a separate layer, not prompt-only.** The LLM is asked nicely to add `LIMIT` and avoid writes — and we still enforce both deterministically in `hardening.py` before any HTTP. The prompt is best-effort; the guard is the contract.
- **Interceptor chain runs before the LLM.** Conflict detection and no-context coreference are cheap regex checks. Catching them pre-LLM saves a 15s tool loop and gives the user a clear message instead of a confused query.
- **Provider abstraction.** `Provider` ABC isolates tool-call message-shape per vendor. Switching models is `/model gpt-4o` at runtime; adding Anthropic is one subclass.
- **Quality-warning feedback channel.** `run_sparql` returns warnings (0 rows, all rows identical, many duplicates) inline with results. The system prompt instructs the model to read them and rewrite — this is where most semantic recovery happens, since SPARQL "succeeds" but means the wrong thing.
- **Sqlite TTL cache for `wbsearchentities`.** The hottest call by far. 7-day TTL collapses repeat-eval latency to ~0 and makes Part-2 multi-model evals tractable.
- **Read-only by default + permission prompt.** Even though Wikidata's endpoint is read-only, we still show the SPARQL and ask `Y/n/edit` before execution in REPL mode. `--auto` skips the prompt for batch eval.

---

## Failure cases that remain (and why they are fundamentally hard)

After Phase-3 hardening, every category in `docs/failures.md` passes on `gpt-5.4`.
On the GPT-4 series (`gpt-4o`, `gpt-4o-mini`) **only type E — multi-hop / nested
joins (E1, E2, E3) — still fails**.

### Failure shape

- SPARQL is syntactically valid, runs under the timeout, returns rows.
- It just answers a different question.
- Two patterns:
  - **Join collapse** — the bridging variable (e.g. `?country`) is dropped.
  - **Property-path shortcut** — a multi-hop chain is fused into one wrong triple.

### Why deterministic guards can't catch it

- `assert_safe`, `auto_limit`, syntax/prefix check, empty/duplicate detector — all green.
- A semantically-wrong-but-valid query parses and returns rows by definition.
- Catching it = comparing produced query to intended graph pattern = same problem as generating it.

### Why it's hard at the model level

- **Compositional planning budget.** 3+ hop plans must stay coherent across generation. Reasoning-tuned models (`gpt-5.4`) externalize the plan first; GPT-4-class models lose the bridge variable mid-emit.
- **Wikidata is open-world + reified.** "Country won the World Cup" is not `wdt:Pxxx`; it lives on a statement node with `p:` / `ps:` / `pq:`. Smaller models default to direct `wdt:` because direct dominates training data — exactly wrong here.
- **Join-direction ambiguity.** "Born in X, country won Y" admits ≥3 readings (`P19/P17` vs `P27+pq:P580` vs current-borders). The NL is genuinely underdetermined; our type-A clarifier only catches *entity* ambiguity, not *join* ambiguity.
- **Training-data sparsity.** SPARQL corpora are 2–3 orders of magnitude smaller than SQL. Multi-hop SPARQL with qualifiers is the rare slice of the rare slice.
- **No repair signal.** Self-repair works on syntax errors and empty results. For semantic-wrong queries, there is no error to feed back.
- **Latency budget runs out.** Each attempt is 3–5 tool calls (~15–30s). Two retries already brush the endpoint timeout.

### What would actually move the needle

- **Plan-then-execute with an explicit IR.** Force a JSON query plan (entities, joins, qualifiers) before SPARQL; validate plan structurally; template the SPARQL.
- **Schema-grounded retrieval.** Cache legal join paths between Q-ids and retrieve before generation (text-to-SQL-style schema linking).
- **Execution-guided beam search.** Generate K candidates, run all, pick the one whose result shape best matches the NL.

Bottom line: type E is not a missing guard. It sits at the intersection of compositional planning and an open-world reified graph, where a deterministic guard has no signal. It goes away when the model is strong enough to plan the join graph before writing — which is why `gpt-5.4` passes all three.

---

## Roadmap

- Streaming-token output (currently buffered per turn)
- `--resume <session>` to reload a saved REPL transcript
- sqlite cache eviction policy
- Asciinema demo

---

