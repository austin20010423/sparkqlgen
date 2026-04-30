"""sparqlgen CLI — Typer entrypoint.

Usage:
    sparqlgen                  # interactive REPL
    sparqlgen ask "..."        # one-shot
    sparqlgen ask --json "..." # machine-readable
    sparqlgen ask --dry-run "..." # produce SPARQL but don't execute
    sparqlgen models           # list available providers
"""

from __future__ import annotations

import json as jsonlib
import sys

import typer
from rich.console import Console

from . import providers as prov
from .agent import run_agent
from .config import settings
from .rendering import render_results, render_sparql, render_tool_call
from .repl import run as run_repl
from .state import SessionState

app = typer.Typer(
    name="sparqlgen",
    help="Claude Code-style NL→SPARQL agent for Wikidata.",
    no_args_is_help=False,
)
console = Console()


def _make_state(model: str, auto: bool) -> SessionState:
    try:
        provider = prov.make_provider(model)
    except Exception as e:
        console.print(f"[red]failed to init model {model!r}:[/] {e}")
        raise typer.Exit(1)
    return SessionState(provider=provider, auto_approve=auto)


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    model: str = typer.Option(
        settings.sparqlgen_default_model, "--model", "-m", help="LLM alias"
    ),
    auto: bool = typer.Option(False, "--auto", help="Auto-approve query execution"),
):
    """Entry: with no subcommand, drop into the interactive REPL."""
    if ctx.invoked_subcommand is not None:
        return
    state = _make_state(model, auto)
    run_repl(state, console)


@app.command()
def ask(
    query: str = typer.Argument(..., help="Natural language query"),
    model: str = typer.Option(settings.sparqlgen_default_model, "--model", "-m"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON to stdout"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate SPARQL but skip execution"),
    auto: bool = typer.Option(True, "--auto/--confirm", help="Auto-approve queries"),
    show_trace: bool = typer.Option(False, "--trace", help="Print tool call trace"),
):
    """One-shot mode — useful for the Part 2 evaluation pipeline."""
    state = _make_state(model, auto)

    permission_check = (lambda q: False) if dry_run else (lambda q: True)
    on_tool = (lambda n, a, r: render_tool_call(console, n, a, r)) if (not json and show_trace) else None

    result = run_agent(
        provider=state.provider,
        user_input=query,
        history=state.history,
        console=console,
        permission_check=permission_check,
        on_tool_call=on_tool,
    )

    if json:
        out = {
            "model": state.provider.model_id,
            "answer": result.text,
            "sparql": result.last_sparql,
            "columns": result.last_columns,
            "rows": result.last_rows,
            "trace": result.tool_trace if show_trace else None,
        }
        sys.stdout.write(jsonlib.dumps(out, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return

    if result.last_sparql:
        render_sparql(console, result.last_sparql)
    if result.last_rows is not None and not dry_run:
        render_results(console, result.last_columns or [], result.last_rows)
    if result.text:
        console.print()
        console.print(result.text)


@app.command()
def models():
    """List the allowed OpenAI models."""
    for name in prov.list_providers():
        console.print(f"  • {name}")


if __name__ == "__main__":
    app()
