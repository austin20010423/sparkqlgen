"""Tests for the on-demand prompt-skills system.

Covers:
- Markdown files load on disk for every named skill, with non-empty bodies.
- CORE_PROMPT is always present and identifies the agent.
- Hardening flags are authoritative — each one loads its dedicated skill.
- Regex sniffs trigger the right input-driven skills (and DON'T trigger when
  the input doesn't match).
- `top_n_global` is suppressed by an explicit geographic scope.
- Skill list is deduped and order-preserved.
- `build_system_prompt` composes core + selected bodies; size grows with
  more skills; empty skill list returns just the core.
- Legacy `SYSTEM_PROMPT` alias still points at `CORE_PROMPT`.
- The agent imports the skills loader without error and uses it per-turn.
"""

from pathlib import Path

import pytest

from sparqlgen import prompts, skills as skills_pkg
from sparqlgen.skills import (
    CORE_PROMPT,
    SKILLS,
    build_system_prompt,
    select_skills,
)


EXPECTED_SKILLS = {
    "clarification",
    "typo_recovery",
    "temporal",
    "negation",
    "fictional",
    "top_n_global",
    "injection",
    "aggregation_quality",
    "join_direction",
    "aggregation_pattern",
    "multilingual",
    "key_facts",
    "verify_resolved_entity",
    "engine_error_recovery",
}


# ─── 1. Files on disk ────────────────────────────────────────────────────────

def test_every_skill_has_a_markdown_file():
    skills_dir = Path(skills_pkg.__file__).parent
    for name in EXPECTED_SKILLS | {"core"}:
        path = skills_dir / f"{name}.md"
        assert path.exists(), f"missing skill file: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"empty skill file: {path}"


def test_no_orphan_markdown_files():
    """Every .md file under skills/ should be loaded by SKILLS or be `core.md`."""
    skills_dir = Path(skills_pkg.__file__).parent
    on_disk = {p.stem for p in skills_dir.glob("*.md")}
    referenced = EXPECTED_SKILLS | {"core"}
    orphans = on_disk - referenced
    assert not orphans, f"orphan markdown files (not in SKILLS): {orphans}"


# ─── 2. Loaded content ──────────────────────────────────────────────────────

def test_core_prompt_loaded_and_identifies_agent():
    assert CORE_PROMPT, "CORE_PROMPT is empty"
    assert "SPARQLGen" in CORE_PROMPT
    assert "search_entity" in CORE_PROMPT  # core mentions tool name
    assert "READ-ONLY" in CORE_PROMPT


def test_skills_dict_has_all_expected_skills():
    assert set(SKILLS.keys()) == EXPECTED_SKILLS


@pytest.mark.parametrize("name", sorted(EXPECTED_SKILLS))
def test_skill_body_is_non_trivial(name):
    body = SKILLS[name]
    assert len(body) >= 100, f"skill {name!r} body too short ({len(body)} chars)"
    assert name.replace("_", " ") in body.lower() or "skill" in body.lower(), (
        f"skill {name!r} body doesn't appear to be the right content"
    )


def test_legacy_system_prompt_alias_points_at_core():
    assert prompts.SYSTEM_PROMPT is CORE_PROMPT


# ─── 3. Hardening flags are authoritative ───────────────────────────────────

def test_typo_flag_loads_typo_skill():
    out = select_skills("anything", has_typo_hint=True)
    assert "typo_recovery" in out


def test_temporal_flag_loads_temporal_skill():
    out = select_skills("anything", has_temporal_hint=True)
    assert "temporal" in out


def test_negation_flag_loads_negation_skill():
    out = select_skills("anything", has_negation_hint=True)
    assert "negation" in out


def test_injection_flag_loads_injection_skill():
    out = select_skills("anything", has_injection_hint=True)
    assert "injection" in out


def test_lang_flag_loads_multilingual_skill():
    out = select_skills("anything", has_lang_hint=True)
    assert "multilingual" in out


def test_no_flags_no_input_match_loads_only_fictional():
    out = select_skills("What is the capital of France?")
    assert out == ["fictional"]


# ─── 4. Regex-driven triggers (positive cases) ──────────────────────────────

def test_ambiguous_name_loads_clarification_and_key_facts():
    out = select_skills("Tell me about Mercury.")
    assert "clarification" in out
    assert "key_facts" in out


def test_aggregation_loads_pattern_quality_and_engine_recovery():
    out = select_skills("How many countries are in the EU?")
    assert "aggregation_pattern" in out
    assert "aggregation_quality" in out
    assert "engine_error_recovery" in out


def test_resolved_entity_marker_loads_verification_skill():
    out = select_skills("[resolved: Tokyo → Q1490 (Tokyo)] population of Tokyo")
    assert "verify_resolved_entity" in out


def test_resolved_entity_flag_loads_verification_skill():
    out = select_skills("anything", has_resolved_entity=True)
    assert "verify_resolved_entity" in out


def test_no_resolved_marker_no_verification_skill():
    out = select_skills("What is the capital of France?")
    assert "verify_resolved_entity" not in out


def test_top_n_in_world_loads_top_n_global():
    out = select_skills("List the 10 tallest mountains in the world.")
    assert "top_n_global" in out


def test_top_n_with_no_scope_loads_top_n_global():
    out = select_skills("Who is the richest person?")
    assert "top_n_global" in out


def test_join_relation_loads_join_skill():
    out = select_skills(
        "What films did Leonardo DiCaprio star in that were directed by Scorsese?"
    )
    assert "join_direction" in out


# ─── 5. Regex-driven triggers (negative cases) ──────────────────────────────

def test_explicit_country_scope_suppresses_top_n_global():
    """`tallest mountains in Japan` should NOT load the global-scope skill —
    the user named a country."""
    out = select_skills("List the 10 tallest mountains in Japan.")
    assert "top_n_global" not in out


def test_simple_question_does_not_load_aggregation():
    out = select_skills("What is the capital of France?")
    assert "aggregation_pattern" not in out
    assert "aggregation_quality" not in out


def test_unambiguous_name_does_not_load_clarification():
    out = select_skills("When was Albert Einstein born?")
    assert "clarification" not in out


def test_no_join_words_does_not_load_join_direction():
    out = select_skills("What is the population of Tokyo?")
    assert "join_direction" not in out


# ─── 6. Dedup + ordering ────────────────────────────────────────────────────

def test_skills_are_deduped():
    """If both a hardening flag and a regex match would add the same skill,
    it should appear only once."""
    out = select_skills(
        "How many countries during 1980s have no coastline?",
        has_temporal_hint=True,
        has_negation_hint=True,
    )
    assert out.count("temporal") == 1
    assert out.count("negation") == 1
    assert out.count("fictional") == 1


def test_hardening_skills_come_before_regex_skills():
    """Order: hardening-driven first (injection, multilingual, temporal,
    negation, typo_recovery), then input-driven, then fictional last."""
    out = select_skills(
        "Tell me about Mercury during the 1980s.",
        has_temporal_hint=True,
    )
    assert out.index("temporal") < out.index("clarification")
    assert out.index("clarification") < out.index("fictional")


# ─── 7. build_system_prompt ─────────────────────────────────────────────────

def test_build_with_empty_skills_returns_core_only():
    assert build_system_prompt([]) == CORE_PROMPT


def test_build_includes_core_and_each_selected_skill_body():
    out = build_system_prompt(["temporal", "negation"])
    assert CORE_PROMPT in out
    assert SKILLS["temporal"] in out
    assert SKILLS["negation"] in out


def test_build_skips_unknown_skill_names_silently():
    out = build_system_prompt(["temporal", "does_not_exist"])
    assert SKILLS["temporal"] in out
    # Unknown skill names should be skipped, not raise.
    assert "does_not_exist" not in out


def test_build_size_grows_with_more_skills():
    base = len(build_system_prompt([]))
    one = len(build_system_prompt(["temporal"]))
    two = len(build_system_prompt(["temporal", "negation"]))
    assert base < one < two


def test_built_prompt_is_smaller_than_legacy_monolithic():
    """The whole point of the refactor: simple cases should send much less
    text than the old monolithic prompt, which was ~10 kB."""
    simple = build_system_prompt(select_skills("What is the capital of France?"))
    assert len(simple) < 5000, (
        f"simple-case prompt grew to {len(simple)} chars — "
        f"refactor should keep it under 5 kB"
    )


# ─── 8. Agent integration smoke test ────────────────────────────────────────

def test_agent_module_imports_skills_loader():
    """Confirm the agent uses the skills package (not the deleted skills.py)."""
    from sparqlgen import agent
    assert hasattr(agent, "build_system_prompt")
    assert hasattr(agent, "select_skills")


def test_full_pipeline_short_input_yields_core_plus_fictional():
    """End-to-end: simulate the agent's flag-extraction step on a plain
    input, run select_skills, and confirm the composed prompt is core +
    fictional only."""
    user_input = "What is the capital of France?"
    selected = select_skills(
        user_input,
        has_typo_hint=False,
        has_temporal_hint=False,
        has_negation_hint=False,
        has_injection_hint=False,
        has_lang_hint=False,
    )
    prompt = build_system_prompt(selected)
    assert selected == ["fictional"]
    assert prompt.startswith(CORE_PROMPT)
    assert SKILLS["fictional"] in prompt
    # No other skills leaked in
    for other in EXPECTED_SKILLS - {"fictional"}:
        assert SKILLS[other] not in prompt, f"{other} skill leaked into simple prompt"
