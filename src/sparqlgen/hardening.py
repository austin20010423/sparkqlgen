"""Phase 3 hardening utilities.

These run *between* the LLM's tool call and the actual SPARQL endpoint. They
either fix a query in place, reject it, or return a structured error the agent
can react to in its self-repair loop.
"""

from __future__ import annotations

import re
import unicodedata


_FORBIDDEN = re.compile(
    r"\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|COPY|MOVE|ADD)\b", re.IGNORECASE
)
_HAS_LIMIT = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)


class QueryError(ValueError):
    """Raised when a query cannot be safely executed."""


def assert_safe(query: str) -> None:
    """Reject write operations. Read-only is the only allowed mode."""
    if _FORBIDDEN.search(query):
        m = _FORBIDDEN.search(query)
        raise QueryError(f"forbidden write keyword: {m.group(0)}")


def auto_limit(query: str, default: int = 100) -> str:
    """Append `LIMIT N` to SELECT queries that don't already have one.

    Skipped for ASK / DESCRIBE / CONSTRUCT — those don't take LIMIT in the
    same way, and ASK is naturally bounded.
    """
    head = query.lstrip()[:20].upper()
    if head.startswith(("ASK", "DESCRIBE", "CONSTRUCT")):
        return query
    if _HAS_LIMIT.search(query):
        return query
    # Append before any trailing whitespace
    return query.rstrip().rstrip(";") + f"\nLIMIT {default}"


def basic_sparql_validate(query: str) -> str | None:
    """Lightweight syntax sanity check — returns None on success, else error message.

    A full parse via rdflib is expensive and rejects valid Wikidata-flavored
    syntax, so we use cheap heuristics instead.
    """
    if not query.strip():
        return "empty query"
    upper = query.upper()
    if not any(kw in upper for kw in ("SELECT", "ASK", "DESCRIBE", "CONSTRUCT")):
        return "missing SELECT / ASK / DESCRIBE / CONSTRUCT"
    # Brace balance
    if query.count("{") != query.count("}"):
        return f"brace imbalance: {{ x{query.count('{')} vs }} x{query.count('}')}"
    if query.count("(") != query.count(")"):
        return "paren imbalance"
    return None


# --- Conflict detection (cheap pass before agent runs) -----------------------

_CONFLICT_PATTERNS = [
    (r"before\s+(\d{4}).*after\s+(\d{4})", "year_before_after"),
    (r"after\s+(\d{4}).*before\s+(\d{4})", "year_after_before"),
    (r"living.*died", "living_and_died"),
    (r"alive.*died\s+in", "alive_and_died"),
]


def detect_conflict(text: str) -> str | None:
    """Return a short reason if user input contains an obvious contradiction."""
    norm = text.lower()
    for pattern, tag in _CONFLICT_PATTERNS:
        m = re.search(pattern, norm)
        if not m:
            continue
        if tag == "year_before_after":
            y1, y2 = int(m.group(1)), int(m.group(2))
            if y1 <= y2:
                return f"asks for both 'before {y1}' and 'after {y2}'"
        if tag == "year_after_before":
            y1, y2 = int(m.group(1)), int(m.group(2))
            if y1 >= y2:
                return f"asks for both 'after {y1}' and 'before {y2}'"
        if tag in ("living_and_died", "alive_and_died"):
            return "asks for people who are both living and dead"
    return None


# --- Language detection (cheap heuristic) -----------------------------------

def detect_lang(text: str) -> str:
    """Return a Wikidata-compatible language code based on character sets.

    Heuristic only — good enough to switch search-entity language. A real LLM
    detection step is one extra call but rarely changes the outcome here.
    """
    cjk = ja = hangul = arabic = cyrillic = latin_accented = 0
    for ch in text:
        if "぀" <= ch <= "ヿ":
            ja += 1
        elif "一" <= ch <= "鿿":
            cjk += 1
        elif "가" <= ch <= "힯":
            hangul += 1
        elif "؀" <= ch <= "ۿ":
            arabic += 1
        elif "Ѐ" <= ch <= "ӿ":
            cyrillic += 1
        elif unicodedata.category(ch).startswith("L") and ord(ch) > 127:
            latin_accented += 1
    if ja > 0:
        return "ja"
    if cjk > 0:
        return "zh"
    if hangul > 0:
        return "ko"
    if arabic > 0:
        return "ar"
    if cyrillic > 0:
        return "ru"
    if latin_accented > 2 and re.search(r"\b(de|el|los|las|por|para|que)\b", text.lower()):
        return "es"
    if latin_accented > 2 and re.search(r"\b(le|la|les|des|que|pour)\b", text.lower()):
        return "fr"
    return "en"


# --- Prompt injection patterns (cheap pre-check; not the only line of defense)

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"disregard\s+(the\s+)?system\s+prompt", re.I),
    re.compile(r"reveal\s+(the\s+)?system\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
]


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


# --- K3: no-context coreference (block) -------------------------------------

_COREF_PATTERNS = [
    re.compile(
        r"\b(show me more|tell me more|give me more|more of those|those ones|"
        r"that one|do it again|same as|filter those|of those)\b",
        re.I,
    ),
    re.compile(r"(更多|上面|剛剛|那個|那些|再來|再給我|同樣的|那邊)"),
    re.compile(r"(それ|あれ|もっと|同じ)"),
]


def detect_no_context_coreference(user_input: str, history: list[dict]) -> str | None:
    """If user references prior turns but there is no history yet, refuse early."""
    if history:
        return None
    if any(p.search(user_input) for p in _COREF_PATTERNS):
        return (
            "I don't have prior context yet — your message refers to something earlier, but "
            "this is the start of our conversation. What would you like me to look up?"
        )
    return None


# --- G: temporal qualifier hint (annotate) -----------------------------------

_TEMPORAL_PATTERNS = [
    re.compile(r"\b(during|as of|since|until)\b", re.I),
    re.compile(r"\bbetween\s+\d{3,4}\s+(and|to|-)\s+\d{3,4}\b", re.I),
    re.compile(r"\b(members?|presidents?|ceos?|leaders?|chairmen?|kings?)\s+of\b.{0,40}\b(in|during|as of)\s+\d{3,4}\b", re.I),
    re.compile(r"在\s*\d{3,4}\s*年"),
    re.compile(r"\d{3,4}\s*年(時|当時|以前|以後|的時候)"),
]


def detect_temporal(user_input: str) -> str | None:
    """Add a hint about Wikidata's temporal-qualifier model."""
    if not any(p.search(user_input) for p in _TEMPORAL_PATTERNS):
        return None
    return (
        "Temporal scope detected. For officeholder/membership questions tied to a specific "
        "time window, use qualifiers on the statement: `pq:P580` (start time), `pq:P582` "
        "(end time), or `pq:P585` (point in time). Direct properties like `wdt:Pxxx` only "
        "give the current value and miss historical state."
    )


# --- H: open-world negation hint (annotate) ----------------------------------

_NEGATION_PATTERNS = [
    re.compile(r"\bwithout\b", re.I),
    re.compile(r"\bnever\b", re.I),
    re.compile(r"\b(did\s+not|didn[''’]t|do\s+not|don[''’]t|has\s+not|hasn[''’]t)\b", re.I),
    re.compile(
        r"\bnot\s+(win|won|have|has|hold|held|been|include|included|"
        r"member|nominee|nominated|selected|chosen)\b",
        re.I,
    ),
    re.compile(r"\bno\s+(coastline|borders?|members?|children|spouse|degree|award)\b", re.I),
    re.compile(r"(沒有|未曾|從不|從未|不曾)"),
]


def detect_negation(user_input: str) -> str | None:
    """Add a hint about Wikidata's open-world model when negation is requested."""
    if not any(p.search(user_input) for p in _NEGATION_PATTERNS):
        return None
    return (
        "Negation detected. Wikidata is an OPEN-WORLD knowledge graph: missing data does "
        "NOT mean the negative fact is true. `FILTER NOT EXISTS { ... }` only catches "
        "entities for which the *positive* fact is recorded as absent. For 'X without Y', "
        "prefer enumerating the full set of X and excluding those with Y, and warn the user "
        "that the answer is necessarily incomplete."
    )


# --- J: fictional entity flag (annotate tool result) -------------------------

_FICTIONAL_KEYWORDS = (
    "fictional",
    "mythological",
    "legendary",
    "fictional character",
    "imaginary",
    "mythical",
    "虚構",
    "虛構",
    "神話",
    "傳說",
    "传说",
    "架空",
)


def is_fictional(description: str) -> bool:
    """Cheap check on a Wikidata description string."""
    if not description:
        return False
    low = description.lower()
    return any(kw in low for kw in _FICTIONAL_KEYWORDS)


# --- Result-quality sanity check ---------------------------------------------

def detect_quality_issue(rows: list[dict], columns: list[str]) -> str | None:
    """Spot pathological result sets that almost always mean the query is wrong.

    Triggers in three cases:
    0. Empty result — 0 rows for a question that should have data, almost always
       means filters are too narrow or the wrong P-id was used.
    1. Cartesian-product blow-up — same row repeated N times because the join
       multiplied through a 1-to-many property (typical for population, where
       a city has many P1082 statements with different qualifiers).
    2. Strict duplicates — distinct row count is far below the total.
    """
    if not rows:
        return (
            "0 rows returned. If this question should have an answer, your filters "
            "are likely too narrow or wrong. Try, in order: "
            "(a) remove the most restrictive triple in the WHERE clause and re-run; "
            "(b) swap a qualifier — e.g. for officeholder questions try `wdt:P39` "
            "(position held) directly on the person, or `pq:P642` (of) instead of "
            "`pq:P17` (country); "
            "(c) replace direct properties with property paths to capture subclasses, "
            "e.g. `wdt:P31/wdt:P279*` instead of `wdt:P31`; "
            "(d) if the entity Q-id was resolved from a non-English label, search again "
            "in English (`search_entity(..., lang=\"en\")`) — non-English labels often "
            "miss aliases. Rewrite the SPARQL and call run_sparql again."
        )

    if len(rows) < 3:
        return None

    cols = columns or list(rows[0].keys())
    keys = [tuple(r.get(c, "") for c in cols) for r in rows]
    distinct = len(set(keys))

    if distinct == 1:
        return (
            "all returned rows are identical — the query is missing DISTINCT "
            "or a join is producing a Cartesian product. Rewrite with "
            "SELECT DISTINCT, or GROUP BY the entity with MAX(?value) so each "
            "entity appears once."
        )

    duplicate_count = len(rows) - distinct
    if duplicate_count >= max(2, int(len(rows) * 0.4)):
        return (
            f"{duplicate_count}/{len(rows)} rows are duplicates — likely a "
            "many-valued property (e.g. multiple population statements over time, "
            "multiple coordinates) is multiplying the result set. Consider "
            "SELECT DISTINCT, GROUP BY with MAX(?value), or filter the property "
            "by its most recent qualifier (pq:P585 point in time)."
        )

    return None
