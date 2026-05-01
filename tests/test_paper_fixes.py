"""Tests for the three improvements derived from the FIRESPARQL paper:

1. `normalize_sparql` — strip markdown fences, fix LLM-output cosmetics.
2. `check_aggregation_grouping` — surface aggregate-without-GROUP-BY warnings.
3. `detect_dominant_entity` — domain-cue ↔ candidate-class consistency
   (refuse to pre-resolve when the description doesn't match the asked-for
   class).
"""

from sparqlgen import hardening


# ─── 1. normalize_sparql ────────────────────────────────────────────────────


def test_normalize_strips_sparql_fence():
    raw = "```sparql\nSELECT ?x WHERE { ?x ?p ?o } LIMIT 10\n```"
    out = hardening.normalize_sparql(raw)
    assert "```" not in out
    assert out.startswith("SELECT")
    assert out.endswith("LIMIT 10")


def test_normalize_strips_plain_fence():
    raw = "```\nSELECT ?x WHERE { ?x ?p ?o }\n```"
    out = hardening.normalize_sparql(raw)
    assert "```" not in out


def test_normalize_strips_sql_fence_synonym():
    raw = "```sql\nASK { ?x ?p ?o }\n```"
    out = hardening.normalize_sparql(raw)
    assert "```" not in out
    assert "ASK" in out


def test_normalize_inserts_space_between_two_vars():
    raw = "SELECT ?x?y WHERE { ?x ?p ?y }"
    out = hardening.normalize_sparql(raw)
    assert "?x ?y" in out


def test_normalize_inserts_space_before_keyword_after_var():
    raw = "SELECT ?xLIMIT 10 WHERE { ?x ?p ?o }"
    out = hardening.normalize_sparql(raw)
    assert "?x LIMIT" in out


def test_normalize_inserts_space_before_prefix_after_var():
    raw = "SELECT ?x WHERE { ?xwdt:P17 wd:Q142 }"
    out = hardening.normalize_sparql(raw)
    assert "?x wdt:P17" in out


def test_normalize_inserts_space_after_close_brace():
    raw = "SELECT ?x WHERE { ?x ?p ?o }LIMIT 10"
    out = hardening.normalize_sparql(raw)
    assert "} LIMIT" in out


def test_normalize_idempotent_on_clean_query():
    clean = "SELECT ?x WHERE { ?x wdt:P31 wd:Q5 . } LIMIT 100"
    assert hardening.normalize_sparql(clean) == clean


def test_normalize_handles_empty_input():
    assert hardening.normalize_sparql("") == ""
    assert hardening.normalize_sparql(None) is None


# ─── 2. check_aggregation_grouping ──────────────────────────────────────────


def test_no_aggregate_returns_none():
    q = "SELECT ?x WHERE { ?x ?p ?o } LIMIT 10"
    assert hardening.check_aggregation_grouping(q) is None


def test_pure_aggregate_count_returns_none():
    """`SELECT (COUNT(*) AS ?n)` has no plain vars in SELECT — fine."""
    q = "SELECT (COUNT(DISTINCT ?country) AS ?n) WHERE { ?country wdt:P463 wd:Q458 . }"
    assert hardening.check_aggregation_grouping(q) is None


def test_aggregate_with_proper_group_by_returns_none():
    q = """SELECT ?city (MAX(?pop) AS ?population) WHERE {
      ?city wdt:P31 wd:Q515 . ?city wdt:P1082 ?pop .
    } GROUP BY ?city ORDER BY DESC(?population) LIMIT 5"""
    assert hardening.check_aggregation_grouping(q) is None


def test_aggregate_missing_group_by_warns():
    q = """SELECT ?city (MAX(?pop) AS ?population) WHERE {
      ?city wdt:P31 wd:Q515 . ?city wdt:P1082 ?pop .
    } ORDER BY DESC(?population) LIMIT 5"""
    out = hardening.check_aggregation_grouping(q)
    assert out is not None
    assert "?city" in out
    assert "GROUP BY" in out


def test_aggregate_partial_group_by_warns():
    """SELECT has ?city and ?country plain, GROUP BY only has ?city → warn."""
    q = """SELECT ?city ?country (MAX(?pop) AS ?p) WHERE {
      ?city wdt:P17 ?country . ?city wdt:P1082 ?pop .
    } GROUP BY ?city LIMIT 10"""
    out = hardening.check_aggregation_grouping(q)
    assert out is not None
    assert "?country" in out


def test_aliased_vars_not_required_in_group_by():
    """`(MAX(?x) AS ?xMax)` — ?xMax is output-only, must NOT be required."""
    q = """SELECT ?city (MAX(?pop) AS ?popMax) WHERE {
      ?city wdt:P1082 ?pop .
    } GROUP BY ?city LIMIT 5"""
    assert hardening.check_aggregation_grouping(q) is None


def test_aggregate_with_label_var_passes_when_grouped():
    """Common pattern: GROUP BY ?city ?cityLabel."""
    q = """SELECT ?city ?cityLabel (MAX(?pop) AS ?p) WHERE {
      ?city wdt:P1082 ?pop .
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } GROUP BY ?city ?cityLabel LIMIT 5"""
    assert hardening.check_aggregation_grouping(q) is None


def test_count_star_no_group_by_passes():
    q = "SELECT (COUNT(*) AS ?n) WHERE { ?film wdt:P57 wd:Q25191 . }"
    assert hardening.check_aggregation_grouping(q) is None


# ─── 3. Backstop 1 — domain-cue ↔ class consistency ─────────────────────────


def test_extract_domain_cue():
    assert hardening._domain_cue_for_input("population of Tokyo") == "population"
    # "directed" is recognized as a verb form of the director cue.
    assert hardening._domain_cue_for_input("Who directed Inception?") == "directed"
    assert hardening._domain_cue_for_input("When was X born?") == "born"
    assert hardening._domain_cue_for_input("capital of France") == "capital"
    # Verb form of `founder`.
    assert hardening._domain_cue_for_input("Who founded Microsoft?") == "founded"


def test_no_cue_when_no_match():
    assert hardening._domain_cue_for_input("List landlocked countries.") is None


def test_description_matches_cue_positive():
    assert hardening._description_matches_cue(
        "capital of Japan; largest city", "population"
    )
    assert hardening._description_matches_cue(
        "American film director", "director"
    )
    assert hardening._description_matches_cue(
        "German theoretical physicist", "born"
    )


def test_description_matches_cue_negative():
    # User asked for population, but the candidate is a "ward area" — no
    # city / town / country / capital keywords.
    assert not hardening._description_matches_cue(
        "ward area of Tokyo Metropolis", "population"
    )
    # User asked for director, but the top hit is a country.
    assert not hardening._description_matches_cue(
        "country in East Asia", "director"
    )


def test_dominant_entity_picks_cue_matching_candidate(monkeypatch):
    """Given two candidates where the top hit is a 'ward area' and the
    second is a 'capital', the picker must prefer the second when the user
    asked about population."""
    def fake_search(query, lang="en", limit=5):
        return [
            {"id": "Q308891", "label": "Tokyo", "description": "ward area of Tokyo Metropolis"},
            {"id": "Q1490",   "label": "Tokyo", "description": "capital and largest city of Japan"},
        ]
    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    out = hardening.detect_dominant_entity("What is the population of Tokyo?")
    assert out is not None
    assert out[1] == "Q1490"


def test_dominant_entity_skips_when_no_candidate_matches_cue(monkeypatch):
    """If every candidate's description fails the cue check, return None
    rather than guessing — let the LLM decide."""
    def fake_search(query, lang="en", limit=5):
        return [
            {"id": "Q1", "label": "Foo", "description": "ward area of Foo Metropolis"},
            {"id": "Q2", "label": "Foo", "description": "subdivision of Foo region"},
        ]
    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    # Note: subdivision/ward fail BOTH the historical filter and the cue
    # filter, so the picker drops to the raw top hit; then the final guard
    # blocks it because the description doesn't match "population" class.
    out = hardening.detect_dominant_entity("What is the population of Foo?")
    assert out is None


def test_dominant_entity_passes_when_description_matches_cue(monkeypatch):
    """When the description contains a class keyword for the cue, the
    resolution must succeed (no false-block)."""
    def fake_search(query, lang="en", limit=5):
        return [
            {"id": "Q42", "label": "Foobar", "description": "American technology company"}
        ]
    monkeypatch.setattr("sparqlgen.wikidata.search_entity", fake_search)
    # cue = "founded", description has "company" → matches.
    out = hardening.detect_dominant_entity("Who founded Foobar?")
    assert out is not None
    assert out[1] == "Q42"


# ─── 4. Integration: run_sparql wires normalization + agg check ─────────────


def test_run_sparql_normalizes_before_validate(monkeypatch):
    """A markdown-wrapped query should pass validation after normalization."""
    from sparqlgen import wikidata

    captured: dict = {}

    class FakeSparql:
        def __init__(self, *a, **kw): pass
        def setQuery(self, q): captured["q"] = q
        def setReturnFormat(self, f): pass
        def setTimeout(self, t): pass
        def query(self):
            class R:
                def convert(self_inner):
                    return {"head": {"vars": ["x"]}, "results": {"bindings": []}}
            return R()

    monkeypatch.setattr(wikidata, "SPARQLWrapper", FakeSparql)

    out = wikidata.run_sparql("```sparql\nSELECT ?x WHERE { ?x ?p ?o }\n```")
    assert out["ok"] is True
    # The query that hit the wrapper has no fences left
    assert "```" not in captured["q"]
    assert captured["q"].lstrip().startswith("SELECT")


def test_run_sparql_surfaces_aggregation_warning(monkeypatch):
    """A query with COUNT but no GROUP BY for ?city must come back with a
    quality_warning, even if rows is empty / non-empty."""
    from sparqlgen import wikidata

    class FakeSparql:
        def __init__(self, *a, **kw): pass
        def setQuery(self, q): pass
        def setReturnFormat(self, f): pass
        def setTimeout(self, t): pass
        def query(self):
            class R:
                def convert(self_inner):
                    return {
                        "head": {"vars": ["city", "n"]},
                        "results": {
                            "bindings": [
                                {"city": {"value": "Tokyo"}, "n": {"value": "1"}}
                            ]
                        },
                    }
            return R()

    monkeypatch.setattr(wikidata, "SPARQLWrapper", FakeSparql)

    out = wikidata.run_sparql(
        "SELECT ?city (COUNT(?p) AS ?n) WHERE { ?city wdt:P1082 ?p . } LIMIT 5"
    )
    assert out["ok"] is True
    assert "quality_warning" in out
    assert "GROUP BY" in out["quality_warning"]
