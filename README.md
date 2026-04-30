# sparqlgen — A cli Wikidata Agent

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
| `/model <id>` | Switch OpenAI model (`gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, ...) |
| `/sparql <query>` | Run raw SPARQL, skip the LLM |
| `/explain` | Have the LLM explain the most recent SPARQL it produced |
| `/export <file>` | Save last results as `.csv` or `.json` |

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

- Streaming-token output (currently buffered per turn)
- `--resume <session>` to reload a saved REPL transcript
- sqlite cache eviction policy
- Asciinema demo

---

