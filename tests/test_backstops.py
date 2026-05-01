"""Tests for the two deterministic backstops in hardening:

1. `detect_dominant_entity` — pre-resolves a Q-id for `<cue> of <name>` /
   `when was <name> born` / `who <verb> <name>` questions, skipping the
   cross-class ambiguity allowlist. Calls Wikidata's search API but is
   cached, so test runs are fast on repeat.
2. `detect_global_intent` + `strip_implicit_geo_filters` — when the user's
   intent is global, removes any country / continent narrowing the LLM
   added to the generated SPARQL.
"""

import pytest

from sparqlgen import hardening


# ─── Backstop 1: dominant entity pre-resolution ─────────────────────────────


def test_extract_phrase_of_pattern():
    assert hardening._extract_entity_phrase("What is the population of Tokyo?") == "Tokyo"
    assert hardening._extract_entity_phrase("capital of France?") == "France"
    assert hardening._extract_entity_phrase("director of Inception") == "Inception"


def test_extract_phrase_when_pattern():
    assert hardening._extract_entity_phrase("When was Albert Einstein born?") == "Albert Einstein"
    assert hardening._extract_entity_phrase("when was OpenAI founded") == "OpenAI"


def test_extract_phrase_who_pattern():
    assert hardening._extract_entity_phrase("Who directed Inception?") == "Inception"
    assert hardening._extract_entity_phrase("Who founded Microsoft") == "Microsoft"


def test_extract_phrase_returns_none_for_unrelated_input():
    assert hardening._extract_entity_phrase("List landlocked countries in Africa.") is None
    assert hardening._extract_entity_phrase("Tell me about Mercury.") is None
    assert hardening._extract_entity_phrase("How many films did he direct?") is None


def test_extract_phrase_strips_articles_and_punctuation():
    out = hardening._extract_entity_phrase("Who composed The Magic Flute?")
    assert out == "Magic Flute"


def test_dominant_entity_skipped_for_ambiguous_names(monkeypatch):
    """Apple / Mercury / Java etc. are in the cross-class ambiguity
    allowlist — never pre-resolve, even though the question shape matches."""
    called = []

    def fake_search(*a, **kw):
        called.append(a)
        return [{"id": "Q312", "label": "Apple Inc."}]

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    assert hardening.detect_dominant_entity("What is the revenue of Apple?") is None
    assert hardening.detect_dominant_entity("Tell me the size of Mercury") is None
    assert called == [], "search_entity must not be called for ambiguous names"


def test_dominant_entity_returns_top_hit_for_unambiguous(monkeypatch):
    def fake_search(query, lang="en", limit=5):
        assert query == "Tokyo"
        return [
            {"id": "Q1490", "label": "Tokyo", "description": "capital of Japan"},
            {"id": "Q7473516", "label": "Tokyo", "description": "former city"},
        ]

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    out = hardening.detect_dominant_entity("What is the population of Tokyo?")
    assert out is not None
    phrase, qid, label, desc = out
    assert phrase == "Tokyo"
    assert qid == "Q1490"
    assert label == "Tokyo"
    assert "capital" in desc


def test_dominant_entity_skips_former_in_favor_of_canonical(monkeypatch):
    """When Wikidata's search ranks a 'former / historical' candidate first,
    the picker must fall through to the next non-historical candidate."""
    def fake_search(query, lang="en", limit=5):
        return [
            {"id": "Q7473516", "label": "Tokyo", "description": "former city in Japan"},
            {"id": "Q1490", "label": "Tokyo", "description": "capital of Japan"},
        ]

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    out = hardening.detect_dominant_entity("What is the population of Tokyo?")
    assert out is not None
    _, qid, _, desc = out
    assert qid == "Q1490"
    assert "capital" in desc


def test_dominant_entity_skips_subdivision(monkeypatch):
    def fake_search(query, lang="en", limit=5):
        return [
            {"id": "Q308891", "label": "Tokyo", "description": "ward area of Tokyo"},
            {"id": "Q1490", "label": "Tokyo", "description": "capital of Japan"},
        ]

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    out = hardening.detect_dominant_entity("population of Tokyo")
    assert out is not None
    assert out[1] == "Q1490"


def test_dominant_entity_falls_back_when_all_candidates_historical(monkeypatch):
    """If every candidate looks historical, return the raw top hit rather
    than None — no candidate is "perfect" but we still want a resolution."""
    def fake_search(query, lang="en", limit=5):
        return [
            {"id": "Q1", "label": "X", "description": "former kingdom"},
            {"id": "Q2", "label": "X", "description": "abolished district"},
        ]

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    out = hardening.detect_dominant_entity("capital of Carthage")
    assert out is not None
    assert out[1] == "Q1"  # raw top hit fallback


def test_dominant_entity_skipped_when_top_hit_is_fictional(monkeypatch):
    def fake_search(query, lang="en", limit=5):
        return [{"id": "Q1234", "label": "Foo", "description": "fictional character"}]

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    assert hardening.detect_dominant_entity("When was Foo born?") is None


def test_dominant_entity_handles_search_failure(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", boom)
    # Should swallow the exception and return None instead of crashing.
    assert hardening.detect_dominant_entity("capital of Brazil") is None


def test_dominant_entity_no_pattern_means_no_call(monkeypatch):
    called = []

    def fake_search(*a, **kw):
        called.append(a)
        return []

    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    assert hardening.detect_dominant_entity("Tell me about life") is None
    assert called == [], "no pattern → no upstream call"


# ─── Backstop 2: global intent + geo-filter stripping ───────────────────────


def test_global_intent_explicit_world_cue():
    assert hardening.detect_global_intent("List the 10 tallest mountains in the world.")
    assert hardening.detect_global_intent("most populous cities globally")
    assert hardening.detect_global_intent("the largest deserts on earth")


def test_global_intent_implicit_no_region():
    assert hardening.detect_global_intent("Who is the richest person?")
    assert hardening.detect_global_intent("the oldest universities?")


def test_global_intent_suppressed_by_named_region():
    assert not hardening.detect_global_intent("tallest mountains in Japan")
    assert not hardening.detect_global_intent("most populous cities in Africa")
    assert not hardening.detect_global_intent("the largest lakes in Europe")


def test_global_intent_requires_superlative():
    """No superlative → never global. 'How many countries in the EU?' must
    not trigger geo-filter stripping."""
    assert not hardening.detect_global_intent("How many countries are in the EU?")
    assert not hardening.detect_global_intent("List landlocked countries in Africa.")


def test_strip_geo_literal_triple():
    q = """SELECT ?m WHERE {
  ?m wdt:P31 wd:Q8502 .
  ?m wdt:P17 wd:Q17 .
  ?m wdt:P2044 ?elev .
} ORDER BY DESC(?elev) LIMIT 10"""
    out = hardening.strip_implicit_geo_filters(q)
    assert "wdt:P17" not in out
    assert "wd:Q17" not in out
    # Everything else preserved
    assert "wdt:P31 wd:Q8502" in out
    assert "wdt:P2044" in out
    assert "ORDER BY DESC(?elev)" in out


def test_strip_continent_triple():
    q = """SELECT ?c WHERE {
  ?c wdt:P31 wd:Q5 .
  ?c wdt:P30 wd:Q15 .
}"""
    out = hardening.strip_implicit_geo_filters(q)
    assert "wdt:P30" not in out
    assert "wdt:P31 wd:Q5" in out


def test_strip_values_country_block():
    q = """SELECT ?m WHERE {
  VALUES ?country { wd:Q837 wd:Q38 }
  ?m wdt:P31 wd:Q8502 .
  ?m wdt:P17 ?country .
} LIMIT 10"""
    out = hardening.strip_implicit_geo_filters(q)
    assert "VALUES ?country" not in out
    # Triple referencing ?country survives — and now matches all countries.
    assert "?m wdt:P17 ?country" in out
    assert "wdt:P31 wd:Q8502" in out


def test_strip_values_continent_block():
    q = "VALUES ?continent { wd:Q15 wd:Q46 } ?x wdt:P30 ?continent ."
    out = hardening.strip_implicit_geo_filters(q)
    assert "VALUES ?continent" not in out
    assert "?x wdt:P30 ?continent" in out


def test_strip_leaves_non_geo_values_blocks_alone():
    """A VALUES block bound to a non-geographic variable name must not be
    stripped — could be e.g. `?genre`, `?language`, `?award`."""
    q = """SELECT ?m WHERE {
  VALUES ?genre { wd:Q188473 wd:Q1054574 }
  ?m wdt:P136 ?genre .
}"""
    out = hardening.strip_implicit_geo_filters(q)
    assert "VALUES ?genre" in out


def test_strip_idempotent_when_no_geo_filters():
    q = "SELECT ?x WHERE { ?x wdt:P31 wd:Q5 . } LIMIT 100"
    assert hardening.strip_implicit_geo_filters(q).strip() == q.strip()


def test_strip_handles_multiple_triples_and_blocks():
    q = """SELECT ?m WHERE {
  VALUES ?country { wd:Q1 wd:Q2 }
  ?m wdt:P31 wd:Q8502 .
  ?m wdt:P17 wd:Q17 .
  ?m wdt:P30 wd:Q15 .
  ?m wdt:P17 ?country .
}"""
    out = hardening.strip_implicit_geo_filters(q)
    assert "VALUES ?country" not in out
    # Every literal P17 / P30 narrowing gone
    assert "wdt:P17 wd:Q17" not in out
    assert "wdt:P30 wd:Q15" not in out
    # Class triple preserved
    assert "wdt:P31 wd:Q8502" in out


# ─── Integration: end-to-end SPARQL rewrite path ────────────────────────────


def test_full_global_rewrite_path():
    """Simulate the agent's flow: user asks global, model emits a SPARQL
    with country narrowing, hardening rewrites before execution."""
    user_input = "List the 10 tallest mountains in the world by elevation."
    assert hardening.detect_global_intent(user_input) is True

    bad_sparql = """SELECT DISTINCT ?mountain ?elevation WHERE {
  VALUES ?country { wd:Q837 wd:Q38 }
  ?mountain wdt:P31/wdt:P279* wd:Q8502 .
  ?mountain wdt:P2044 ?elevation .
  ?mountain wdt:P17 ?country .
} ORDER BY DESC(?elevation) LIMIT 10"""

    fixed = hardening.strip_implicit_geo_filters(bad_sparql)
    # Geographic narrowing gone
    assert "VALUES ?country" not in fixed
    # Core query preserved
    assert "wdt:P31/wdt:P279* wd:Q8502" in fixed
    assert "wdt:P2044" in fixed
    assert "ORDER BY DESC(?elevation)" in fixed
    assert "LIMIT 10" in fixed


def test_no_rewrite_when_user_named_a_region():
    """If the user asked for tallest mountains in Japan, we must NOT strip
    the country narrowing — that's the user's intent."""
    user_input = "List the tallest mountains in Japan."
    assert hardening.detect_global_intent(user_input) is False
    # The agent's run_sparql gate only invokes strip_implicit_geo_filters
    # when global_intent is True, so the SPARQL is left alone here.
