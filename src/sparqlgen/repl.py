"""Interactive Read-Eval-Print Loop.

Runs prompt_toolkit for input, dispatches slash commands, otherwise hands the
line to the agent loop. Ctrl+C cancels the in-flight operation but does not
exit the REPL; Ctrl+D / /exit quit cleanly.
"""

from __future__ import annotations

import re

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console

from . import commands, compaction
from .agent import run_agent
from .config import settings
from .rendering import render_results, render_sparql, render_tool_call, show_banner
from .state import SessionState


_COMPLETIONS = [
    "/help", "/clear", "/exit", "/quit", "/model",
    "/sparql", "/explain", "/export", "/compact",
]


_OPTION_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+?)\s*$", re.MULTILINE)


def _parse_options(text: str) -> list[str]:
    """Extract '1. ...', '2. ...' lines from a clarification message."""
    matches = _OPTION_RE.findall(text)
    if len(matches) < 2:
        return []
    matches_sorted = sorted(matches, key=lambda m: int(m[0]))
    return [m[1] for m in matches_sorted]


def _looks_like_clarification(text: str) -> bool:
    """Heuristic: agent text that asks the user something rather than answering."""
    low = text.lower()
    if "?" not in text:
        return False
    cues = (
        "did you mean",
        "which one",
        "which of these",
        "could you clarify",
        "can you clarify",
        "not specific enough",
        "isn't specific enough",
        "isn't precise",
        "not precise enough",
        "ambiguous",
        "rephrase",
        "more detail",
    )
    return any(c in low for c in cues)


def _ask_permission(console: Console, query: str, auto: bool) -> bool:
    if auto:
        return True
    render_sparql(console, query)
    try:
        ans = console.input("[yellow]Run this query? [Y/n][/] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("", "y", "yes")


def run(state: SessionState, console: Console) -> None:
    show_banner(console, state.provider.model_id)

    session: PromptSession = PromptSession(
        history=FileHistory(str(settings.history_file)),
        completer=WordCompleter(_COMPLETIONS, ignore_case=True),
        multiline=False,
    )

    while True:
        try:
            line = session.prompt("sparqlgen ❯ ").strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            console.print("\n[dim]bye[/dim]")
            return

        if not line:
            continue

        # If the agent surfaced numbered choices on the previous turn and the
        # user typed just a number, expand it into the full option text so the
        # agent gets unambiguous context. Anything non-numeric clears the
        # pending choices and falls through as a normal new query.
        if state.pending_choices:
            stripped = line.strip()
            if stripped.isdigit():
                idx = int(stripped)
                if 1 <= idx <= len(state.pending_choices):
                    chosen = state.pending_choices[idx - 1]
                    console.print(f"[dim]→ selected #{idx}: {chosen}[/dim]")
                    line = f"I meant option {idx}: {chosen}. Please proceed."
                    state.pending_choices = []
                else:
                    console.print(
                        f"[red]choose 1-{len(state.pending_choices)}, "
                        f"or rephrase[/red]"
                    )
                    continue
            else:
                # User rephrased instead of picking — that's fine, drop choices
                state.pending_choices = []

        cmd_result = commands.dispatch(line, state, console)
        if cmd_result == "exit":
            console.print("[dim]bye[/dim]")
            return
        if cmd_result == "continue":
            continue

        # Auto-compact if context is getting long
        try:
            summary = compaction.maybe_compact(state.history, state.provider)
            if summary:
                console.print(
                    f"[dim]↻ context compacted "
                    f"(~{compaction.estimate_tokens(state.history)} tokens left)[/dim]"
                )
        except Exception as e:
            console.print(f"[dim yellow]compaction skipped: {e}[/dim yellow]")

        # Hand to agent
        try:
            result = run_agent(
                provider=state.provider,
                user_input=line,
                history=state.history,
                console=console,
                permission_check=lambda q: _ask_permission(console, q, state.auto_approve),
                on_tool_call=lambda n, a, r: render_tool_call(console, n, a, r),
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]cancelled[/yellow]")
            continue
        except Exception as e:
            console.print(f"[red]error:[/] {e}")
            continue

        # Persist last SPARQL/results into state for /explain /export
        if result.last_sparql:
            state.last_sparql = result.last_sparql
            state.last_rows = result.last_rows
            state.last_columns = result.last_columns
            render_results(console, result.last_columns or [], result.last_rows or [])

        if result.text:
            console.print()
            # If the agent returned text but never executed a SPARQL query, it's
            # almost certainly asking for clarification — call it out visually
            # and capture any numbered options so the user can pick by typing a
            # digit on the next turn.
            if result.last_sparql is None and _looks_like_clarification(result.text):
                from rich.panel import Panel
                options = _parse_options(result.text)
                state.pending_choices = options
                hint = (
                    f"\n[dim]Type 1-{len(options)} to pick, or rephrase.[/dim]"
                    if options
                    else ""
                )
                console.print(
                    Panel(
                        result.text + hint,
                        title="[yellow]✦ needs clarification[/yellow]",
                        border_style="yellow",
                    )
                )
            else:
                console.print(result.text)
