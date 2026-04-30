from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .providers import Provider


@dataclass
class SessionState:
    provider: Provider
    history: list[dict[str, Any]] = field(default_factory=list)
    last_sparql: str | None = None
    last_rows: list[dict[str, Any]] | None = None
    last_columns: list[str] | None = None
    auto_approve: bool = False
    # Numbered choices the agent surfaced in its last clarification turn.
    # The user can pick by typing the index instead of retyping the option.
    pending_choices: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.history.clear()
        self.last_sparql = None
        self.last_rows = None
        self.last_columns = None
        self.pending_choices = []
