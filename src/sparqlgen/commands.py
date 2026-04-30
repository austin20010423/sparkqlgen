"""Slash commands for the REPL.

Each handler returns one of:
  - "continue"  — keep the REPL running
  - "exit"      — quit the REPL
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table

from . import providers as prov
from .state import SessionState


HelpEntry = tuple[str, str]
_HELP: list[HelpEntry] = [
    ("/help", "Show this help."),
    ("/clear", "Reset conversation context (keeps you in the session)."),
    ("/exit, /quit", "Leave the REPL."),
    ("/model <id>", "Switch OpenAI model (gpt-5.4, gpt-4o, gpt-4o-mini)."),
    ("/sparql <query>", "Run a raw SPARQL query, skipping the LLM."),
    ("/explain", "Ask the LLM to explain the most recent SPARQL it produced."),
    ("/export <file>", "Save the most recent results to .csv or .json."),
    ("/compact", "Manually summarize older conversation to free up context."),
]


def _help(_args, _state, console):
    t = Table(title="Slash commands", show_lines=False, header_style="bold cyan")
    t.add_column("command", no_wrap=True)
    t.add_column("description")
    for cmd, desc in _HELP:
        t.add_row(cmd, desc)
    console.print(t)
    return "continue"


def _clear(_args, state: SessionState, console):
    state.reset()
    console.print("[dim]conversation cleared[/dim]")
    return "continue"


def _exit(_args, _state, _console):
    return "exit"


def _model(args: str, state: SessionState, console: Console):
    model_id = args.strip()
    if not model_id:
        console.print(f"current model: [bold]{state.provider.model_id}[/]")
        console.print(f"allowed: {', '.join(prov.list_providers())}")
        return "continue"
    try:
        state.provider = prov.make_provider(model_id)
        state.reset()
        console.print(
            f"[green]✓[/] switched to [bold]{state.provider.model_id}[/]; conversation reset."
        )
    except Exception as e:
        console.print(f"[red]error:[/] {e}")
    return "continue"


def _sparql(args: str, state: SessionState, console: Console):
    from . import wikidata
    from .rendering import render_results, render_sparql

    query = args.strip()
    if not query:
        console.print("[red]usage:[/] /sparql SELECT ?x WHERE { ... }")
        return "continue"
    render_sparql(console, query)
    try:
        result = wikidata.run_sparql(query)
    except wikidata.UnsafeQueryError as e:
        console.print(f"[red]blocked:[/] {e}")
        return "continue"
    if not result["ok"]:
        console.print(f"[red]✗[/] {result['error']}")
        return "continue"
    state.last_sparql = query
    state.last_rows = result["rows"]
    state.last_columns = result["columns"]
    render_results(console, result["columns"], result["rows"])
    return "continue"


def _explain(_args, state: SessionState, console: Console):
    from .agent import run_agent
    if not state.last_sparql:
        console.print("[dim]no recent SPARQL to explain[/dim]")
        return "continue"
    prompt = (
        "Briefly (3-5 sentences) explain what the following SPARQL query does and why each "
        "clause is there. Do not run any tools.\n\n```sparql\n"
        + state.last_sparql
        + "\n```"
    )
    result = run_agent(state.provider, prompt, state.history, console)
    if result.text:
        console.print(result.text)
    return "continue"


def _compact(_args, state: SessionState, console: Console):
    from . import compaction
    before = compaction.estimate_tokens(state.history)
    summary = compaction.compact(state.history, state.provider)
    if summary is None:
        console.print(
            f"[dim]nothing to compact ({before} tokens, "
            f"need ≥{compaction.KEEP_LAST_USER_TURNS + 1} user turns)[/dim]"
        )
        return "continue"
    after = compaction.estimate_tokens(state.history)
    console.print(
        f"[green]✓[/] compacted: {before} → {after} tokens. Summary kept as context."
    )
    return "continue"


def _export(args: str, state: SessionState, console: Console):
    if not state.last_rows:
        console.print("[dim]no results to export[/dim]")
        return "continue"
    path = Path(args.strip())
    if not path.suffix:
        console.print("[red]usage:[/] /export results.csv  (or .json)")
        return "continue"
    cols = state.last_columns or list(state.last_rows[0].keys())
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(state.last_rows, ensure_ascii=False, indent=2))
    else:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for r in state.last_rows:
            w.writerow({c: r.get(c, "") for c in cols})
        path.write_text(buf.getvalue())
    console.print(f"[green]✓[/] wrote {len(state.last_rows)} rows to {path}")
    return "continue"


HANDLERS: dict[str, Callable] = {
    "/help": _help,
    "/clear": _clear,
    "/exit": _exit,
    "/quit": _exit,
    "/model": _model,
    "/sparql": _sparql,
    "/explain": _explain,
    "/export": _export,
    "/compact": _compact,
}


def dispatch(line: str, state: SessionState, console: Console) -> str | None:
    """Returns 'continue' / 'exit' if the line was a slash command, else None."""
    if not line.startswith("/"):
        return None
    head, _, rest = line.partition(" ")
    handler = HANDLERS.get(head)
    if handler is None:
        console.print(f"[red]unknown command:[/] {head}  (try /help)")
        return "continue"
    return handler(rest, state, console)
