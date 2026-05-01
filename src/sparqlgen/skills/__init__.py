"""On-demand prompt-skills loader.

Each skill lives in its own markdown file (`*.md`) inside this package. The
loader reads them all once at import time and exposes:

- `CORE_PROMPT`     — sent on every LLM call.
- `SKILLS`          — dict of skill_name → markdown body.
- `select_skills()` — picks which skill names apply for a given turn.
- `build_system_prompt()` — composes core + selected skill bodies.

Triggers stay in Python (regex over the user input + hardening flags). The
prompt content is plain markdown, edit-friendly without touching code.
"""

from __future__ import annotations

import re
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent


def _read(name: str) -> str:
    return (_SKILLS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


# ─── Load all markdown bodies once ──────────────────────────────────────────

CORE_PROMPT: str = _read("core")

SKILLS: dict[str, str] = {
    "clarification": _read("clarification"),
    "typo_recovery": _read("typo_recovery"),
    "temporal": _read("temporal"),
    "negation": _read("negation"),
    "fictional": _read("fictional"),
    "top_n_global": _read("top_n_global"),
    "injection": _read("injection"),
    "aggregation_quality": _read("aggregation_quality"),
    "join_direction": _read("join_direction"),
    "aggregation_pattern": _read("aggregation_pattern"),
    "multilingual": _read("multilingual"),
    "key_facts": _read("key_facts"),
    "verify_resolved_entity": _read("verify_resolved_entity"),
    "engine_error_recovery": _read("engine_error_recovery"),
}


# ─── Triggers ────────────────────────────────────────────────────────────────

# Names that genuinely span multiple classes / domains. Used to load the
# clarification skill when the input is short and contains one of these.
_AMBIGUOUS_NAMES = re.compile(
    r"\b(apple|mercury|java|paris|saturn|cambridge|springfield|amazon|orion|"
    r"phoenix|nike|atlas|venus|jordan|georgia|jersey|columbia|memphis|"
    r"alexandria)\b",
    re.IGNORECASE,
)
_TOP_N_GLOBAL = re.compile(
    r"\b(tallest|largest|oldest|youngest|most\s+populous|deepest|longest|"
    r"richest|biggest|smallest|highest|fastest|heaviest|widest)\b",
    re.IGNORECASE,
)
# Explicit "global" cue — load top_n_global even if a generic "in …" phrase
# also appears.
_GLOBAL_CUE = re.compile(
    r"\b(world|worldwide|globally|on\s+earth|universe|globe)\b", re.IGNORECASE
)
# Geographic scope marker — `in <noun>` (any noun). Used to suppress
# top_n_global only when no global cue is present.
_GEO_SCOPE = re.compile(r"\bin\s+(the\s+)?[a-z]+", re.IGNORECASE)
_JOIN_RELATION = re.compile(
    r"\b(starr(ing|ed)?|directed|wrote|painted|composed|founded|cast(\s+in)?|"
    r"acted|authored)\b",
    re.IGNORECASE,
)
_AGGREGATION = re.compile(
    r"\b(how\s+many|count|list\s+(the\s+)?\d+|top\s+\d+|first\s+\d+|"
    r"\d+\s+most\b)",
    re.IGNORECASE,
)


def select_skills(
    user_input: str,
    *,
    has_typo_hint: bool = False,
    has_temporal_hint: bool = False,
    has_negation_hint: bool = False,
    has_injection_hint: bool = False,
    has_lang_hint: bool = False,
    has_resolved_entity: bool = False,
) -> list[str]:
    """Return the ordered list of skill names that should be appended for
    this turn.

    Hardening flags from the agent's interceptor chain are authoritative;
    regex sniffs cover what hardening doesn't catch.
    """
    out: list[str] = []
    low = user_input.lower()

    # Hardening-driven (authoritative)
    if has_injection_hint:
        out.append("injection")
    if has_lang_hint:
        out.append("multilingual")
    if has_temporal_hint:
        out.append("temporal")
    if has_negation_hint:
        out.append("negation")
    if has_typo_hint:
        out.append("typo_recovery")
    if has_resolved_entity or "[resolved:" in user_input:
        out.append("verify_resolved_entity")

    # Input-driven
    if _AMBIGUOUS_NAMES.search(low):
        out.append("clarification")
        out.append("key_facts")
    if _TOP_N_GLOBAL.search(low):
        # Load when the input has an explicit global cue, OR has no `in <X>`
        # geographic scope at all. Suppress only when a country / region is
        # named without a global qualifier.
        if _GLOBAL_CUE.search(low) or not _GEO_SCOPE.search(low):
            out.append("top_n_global")
    if _JOIN_RELATION.search(low):
        out.append("join_direction")
    if _AGGREGATION.search(low):
        out.append("aggregation_pattern")
        out.append("aggregation_quality")
        # Aggregation queries are the ones that hit Blazegraph engine
        # limits; load the engine-error self-repair skill alongside.
        out.append("engine_error_recovery")

    # The fictional skill is cheap and the model needs it whenever it might
    # encounter a fictional candidate via search_entity that wasn't caught by
    # the hardening short-circuit.
    out.append("fictional")

    # Dedupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for name in out:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique


def build_system_prompt(skill_names: list[str]) -> str:
    """Compose CORE_PROMPT + selected skill bodies."""
    if not skill_names:
        return CORE_PROMPT
    bodies = [SKILLS[n] for n in skill_names if n in SKILLS]
    return CORE_PROMPT + "\n\n" + "\n\n".join(bodies)
