from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table


BANNER = r"""
  ____                       _  ___
 / ___| _ __  __ _ _ __ __ _| |/ _ \  ___ _ __
 \___ \| '_ \/ _` | '__/ _` | | | | |/ _ \ '_ \
  ___) | |_) \__,_| | | \___| | |_| |  __/ | | |
 |____/| .__/\____|_|  \___\_|_|\___/ \___|_| |_|
       |_|     Wikidata NL -> SPARQL agent
"""


def show_banner(console: Console, model_name: str) -> None:
    console.print(BANNER, style="cyan")
    console.print(
        f"  model: [bold]{model_name}[/]    "
        f"type [bold]/help[/] for commands, [bold]/exit[/] to quit\n"
    )


def render_sparql(console: Console, query: str) -> None:
    console.print(
        Panel(
            Syntax(query, "sparql", theme="ansi_dark", word_wrap=True),
            title="SPARQL",
            border_style="cyan",
        )
    )


def render_results(console: Console, columns: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        console.print("[dim]no rows[/dim]")
        return
    cols = columns or list(rows[0].keys())
    table = Table(show_lines=False, header_style="bold magenta")
    for c in cols:
        table.add_column(c, overflow="fold")
    for r in rows[:50]:
        table.add_row(*[_truncate(r.get(c, "")) for c in cols])
    console.print(table)
    if len(rows) > 50:
        console.print(f"[dim]({len(rows)} rows total, showing first 50)[/dim]")


def _truncate(v: Any, maxlen: int = 80) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


def render_tool_call(console: Console, name: str, args: dict, result: Any) -> None:
    head = f"[bold green]⏺[/] {name}({_fmt_args(args)})"
    console.print(head)
    console.print(f"  └─ {_fmt_result(name, result)}", style="dim")


def _fmt_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s!r}" if not isinstance(v, str) else f'{k}="{s}"')
    return ", ".join(parts)


def _fmt_result(name: str, result: Any) -> str:
    if isinstance(result, dict) and result.get("error"):
        return f"[red]error: {result['error']}[/]"
    if name in ("search_entity", "search_property") and isinstance(result, list):
        head = " | ".join(f"{r['id']} ({r.get('label', '?')})" for r in result[:3])
        more = "" if len(result) <= 3 else f" + {len(result) - 3} more"
        return head + more
    if name == "run_sparql" and isinstance(result, dict):
        if not result.get("ok"):
            err = result.get("error", "failed")
            err_short = err if len(err) <= 120 else err[:117] + "…"
            line = f"[red]✗ {err_short}[/]"
            if result.get("hint"):
                line += f"\n     [dim]hint:[/] {result['hint'][:160]}…"
            return line
        head = f"✓ {len(result.get('rows', []))} rows ({result.get('elapsed_s', '?')}s)"
        if result.get("quality_warning"):
            head += f"  [yellow]⚠ {result['quality_warning'][:90]}…[/]"
        return head
    if name == "get_entity" and isinstance(result, dict):
        if not result.get("exists"):
            return "[red]not found[/red]"
        n = len(result.get("property_ids_with_counts", {}))
        return f"schema only · {n} property ids found (no label/description by design)"
    return str(result)[:120]
