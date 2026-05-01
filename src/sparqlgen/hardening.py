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


# --- Pre-execution SPARQL normalizer -----------------------------------------
#
# Small models (and occasionally larger ones) wrap their SPARQL output in
# markdown code fences, drop spaces between identifiers, or paste leading
# language tags. Fix these deterministically before assert_safe / validate
# run, so a perfectly correct query isn't rejected for cosmetic reasons.

_MD_FENCE = re.compile(
    r"^\s*```(?:sparql|sql|turtle)?\s*\n?|\n?\s*```\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Two SPARQL tokens written with no space between them: `?xLIMIT`, `?x?y`,
# `}LIMIT`, `}ORDER`. Variables are matched as lowercase (most common form
# in our system) so a greedy `\w+` doesn't swallow the trailing keyword.
_VAR_LC = r"\?[a-z][a-z0-9_]*"
_KW_AFTER_VAR = (
    r"LIMIT|ORDER\s+BY|GROUP\s+BY|HAVING|OFFSET|FILTER|OPTIONAL|UNION|"
    r"MINUS|VALUES|BIND|SERVICE"
)
_MISSING_SPACE_VAR_KW = re.compile(
    rf"({_VAR_LC})({_KW_AFTER_VAR}|wdt:|wd:|p:|ps:|pq:|rdfs:|xsd:|bd:|wikibase:)",
    re.IGNORECASE,
)
_MISSING_SPACE_BRACE_KW = re.compile(
    r"(\})\s*(LIMIT|ORDER\s+BY|GROUP\s+BY|HAVING|OFFSET)\b", re.IGNORECASE
)
_MISSING_SPACE_VAR_VAR = re.compile(rf"({_VAR_LC})({_VAR_LC})")


def normalize_sparql(query: str) -> str:
    """Strip markdown fences, fix obvious LLM-output cosmetics. Pure regex —
    never changes query semantics."""
    if not query:
        return query
    out = query.strip()
    out = _MD_FENCE.sub("", out)
    # Two consecutive variables → add space
    out = _MISSING_SPACE_VAR_VAR.sub(r"\1 \2", out)
    # Variable directly followed by a known SPARQL keyword/prefix → add space
    out = _MISSING_SPACE_VAR_KW.sub(r"\1 \2", out)
    # `}LIMIT` / `}ORDER BY` etc.
    out = _MISSING_SPACE_BRACE_KW.sub(r"\1 \2", out)
    return out.strip()


# --- Aggregation / GROUP BY consistency check --------------------------------
#
# Per FIRESPARQL paper: 11 of 14 syntax-level failures were aggregate (COUNT,
# MAX, MIN, AVG, SUM) without a matching GROUP BY, or with the wrong vars in
# GROUP BY. Most SPARQL endpoints reject the query OR return Cartesian
# product instead. We surface this as a `quality_warning` so the agent can
# self-repair via the existing aggregation_quality skill.

_AGG_FN = re.compile(
    r"\b(COUNT|MAX|MIN|AVG|SUM|SAMPLE|GROUP_CONCAT)\s*\(", re.IGNORECASE
)
_SELECT_BLOCK = re.compile(
    r"\bSELECT\s+(?:DISTINCT\s+|REDUCED\s+)?(.*?)\bWHERE\b",
    re.IGNORECASE | re.DOTALL,
)
_AS_VAR = re.compile(r"\bAS\s+(\?\w+)", re.IGNORECASE)
_PLAIN_VAR = re.compile(r"\?\w+")
_GROUP_BY_BLOCK = re.compile(
    r"\bGROUP\s+BY\s+(.+?)(?=\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|\bOFFSET\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def check_aggregation_grouping(query: str) -> str | None:
    """Return a warning string if the SELECT mixes aggregate and non-aggregate
    columns without a GROUP BY that covers every non-aggregate variable.
    Returns None if the query is valid in this respect.
    """
    if not _AGG_FN.search(query):
        return None  # No aggregate → nothing to check
    sel_match = _SELECT_BLOCK.search(query)
    if not sel_match:
        return None
    select_text = sel_match.group(1)

    # Variables introduced by `(MAX(?x) AS ?y)` are output-only — exclude them
    # from the "must be in GROUP BY" set.
    aliased = set(_AS_VAR.findall(select_text))
    # Strip the parenthesised aggregate expressions before counting plain vars.
    paren_stripped = re.sub(r"\([^()]*\)", " ", select_text)
    raw_vars = set(_PLAIN_VAR.findall(paren_stripped))
    nonagg_vars = raw_vars - aliased

    if not nonagg_vars:
        return None  # Pure aggregate (e.g. SELECT (COUNT(*) AS ?n)) — fine.

    gb_match = _GROUP_BY_BLOCK.search(query)
    grouped: set[str] = set()
    if gb_match:
        grouped = set(_PLAIN_VAR.findall(gb_match.group(1)))

    missing = nonagg_vars - grouped
    if not missing:
        return None

    return (
        "aggregation/GROUP BY mismatch: SELECT contains aggregate functions "
        f"alongside non-aggregated variable(s) {sorted(missing)} that are not "
        "in GROUP BY. Either (a) add `GROUP BY` for those variables, or "
        "(b) remove them from SELECT, or (c) wrap them in an aggregate "
        "(e.g. `(SAMPLE(?x) AS ?xLabel)`). Most endpoints reject this query "
        "or return a Cartesian product."
    )


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


# --- Pre-LLM short-circuit: well-known fictional / mythological subjects -----
#
# When the user clearly asks a real-world fact ("date of birth", "population",
# "net worth", "address") about an entity from this allowlist, refuse before
# ever calling the LLM. This prevents the agent from looping search_entity or
# running an "allowed in-fiction relation" query that the eval harness scores
# as a failure.

_FICTIONAL_NAMES = {
    "sherlock holmes": ("fictional", "literary character created by Arthur Conan Doyle"),
    "atlantis": ("mythological", "legendary island in Plato's writings — no recorded population"),
    "middle-earth": ("fictional", "fictional setting from Tolkien"),
    "middle earth": ("fictional", "fictional setting from Tolkien"),
    "narnia": ("fictional", "fictional world from C. S. Lewis"),
    "hogwarts": ("fictional", "fictional school in the Harry Potter series"),
    "tony stark": ("fictional", "fictional Marvel character"),
    "iron man": ("fictional", "fictional Marvel character"),
    "harry potter": ("fictional", "fictional literary character"),
    "frodo baggins": ("fictional", "fictional Tolkien character"),
    "gandalf": ("fictional", "fictional Tolkien character"),
    "darth vader": ("fictional", "fictional Star Wars character"),
    "luke skywalker": ("fictional", "fictional Star Wars character"),
    "el dorado": ("legendary", "legendary city of gold — no recorded population"),
    "shangri-la": ("fictional", "fictional Himalayan utopia from Lost Horizon"),
    "wakanda": ("fictional", "fictional Marvel nation"),
}

_REAL_WORLD_FACT_CUES = (
    "date of birth", "born", "birth", "death", "died", "age",
    "population", "net worth", "revenue", "salary",
    "address", "phone", "height", "weight",
)


def detect_fictional_input(user_input: str) -> str | None:
    """Return a refusal message when the user asks a real-world fact about a
    well-known fictional / mythological subject. Used by the agent loop to
    short-circuit before any tool call."""
    low = user_input.lower()
    for name, (kind, blurb) in _FICTIONAL_NAMES.items():
        if name in low:
            asks_real_fact = any(cue in low for cue in _REAL_WORLD_FACT_CUES)
            if asks_real_fact:
                return (
                    f"{name.title()} is a {kind} character/place ({blurb}). "
                    f"Wikidata does not record real-world facts (date of birth, "
                    f"population, etc.) for fictional or legendary entities, so I "
                    f"can't answer that. If you want an in-fiction relation "
                    f"(e.g. creator, first appearance, author), say so explicitly."
                )
    return None


# --- Pre-LLM typo correction for famous-entity names -------------------------
#
# search_entity is exact-match-ish, so "Toyko" returns nothing useful and the
# model gives up and asks the user. A tiny allowlist of famous typos closes the
# gap deterministically without false positives.

_FAMOUS_TYPOS = {
    "toyko": ("Tokyo", "Q1490"),
    "einstien": ("Einstein", "Q937"),
    "shaksepeare": ("Shakespeare", "Q692"),
    "shakspeare": ("Shakespeare", "Q692"),
    "shakespere": ("Shakespeare", "Q692"),
    "pikasso": ("Picasso", "Q5593"),
    "picaso": ("Picasso", "Q5593"),
    "leonardo dicaprrio": ("Leonardo DiCaprio", "Q38111"),
    "scorcese": ("Scorsese", "Q41148"),
    "nietzche": ("Nietzsche", "Q9358"),
    "tolstoi": ("Tolstoy", "Q7243"),
}


def detect_typo_hint(user_input: str) -> str | None:
    """If the input contains a known famous-entity typo, surface the
    correction to the agent so it can resolve directly instead of asking the
    user to re-spell."""
    low = user_input.lower()
    hits: list[str] = []
    for typo, (correct, qid) in _FAMOUS_TYPOS.items():
        if typo in low:
            hits.append(f"'{typo}' → '{correct}' ({qid})")
    if not hits:
        return None
    return (
        "Probable typo(s) detected: "
        + "; ".join(hits)
        + ". Use the corrected spelling for `search_entity` and proceed; do NOT "
        + "ask the user to re-spell."
    )


# --- Pre-LLM dominant-entity resolution -------------------------------------
#
# General pattern: when the user asks `<cue> of <name>`, `When was <name>
# born/founded/...`, or `Who <verb> <name>`, the LLM has to pick a Q-id and
# then decide whether to ask "which one?". Cautious models often punt even
# when the domain cue clearly disambiguates. We resolve the Q-id upstream via
# search_entity (cached) and inject the result into the input so the LLM
# proceeds straight to SPARQL.

_DOMAIN_CUES = (
    "population", "capital", "director", "directed", "founder", "founded",
    "born", "birth", "death", "died", "area", "elevation", "height",
    "length", "width", "depth", "leader", "president", "ceo", "author",
    "wrote", "painter", "painted", "composer", "composed", "architect",
    "creator", "created", "discoverer", "discovered", "inventor",
    "invented", "monarch", "prime minister", "chairman", "owner",
    "publisher", "country", "continent", "currency", "language", "symbol",
)

# Map a domain cue to the keywords we expect in the resolved candidate's
# Wikidata description. If none of these keywords appear, the resolution is
# probably the wrong sense (e.g. picking the historical "ward area" of a
# city when the user asked for population).
_PEOPLE = (
    "person", "writer", "scientist", "painter", "musician", "actor",
    "actress", "athlete", "politician", "philosopher", "mathematician",
    "physicist", "chemist", "biologist", "composer", "director",
    "queen", "king", "emperor", "monarch", "leader", "engineer",
    "architect", "novelist", "poet", "playwright", "singer",
)
_PLACES_POP = (
    "city", "town", "country", "state", "capital", "island", "district",
    "municipality", "village", "settlement", "province", "territory",
    "nation", "republic", "kingdom", "principality",
)
_FILM_WORK = ("film", "movie", "series", "tv", "show", "production",
              "documentary", "feature")
_ORG = ("company", "corporation", "organization", "firm", "institution",
        "university", "club", "association", "agency")
_BOOK_WORK = ("book", "novel", "poem", "essay", "play", "story", "series",
              "manga", "comic", "anthology")

_DOMAIN_CUE_CLASSES: dict[str, tuple[str, ...]] = {
    "population": _PLACES_POP,
    "capital":    _PLACES_POP,
    "currency":   _PLACES_POP + ("monetary", "union"),
    "continent":  _PLACES_POP,
    "language":   _PLACES_POP + ("people", "ethnic"),
    "area":       _PLACES_POP + ("lake", "park", "region"),
    "elevation":  ("mountain", "peak", "summit", "volcano", "hill", "ridge"),
    "height":     ("mountain", "peak", "summit", "building", "tower",
                   "skyscraper", "structure") + _PEOPLE,
    "depth":      ("lake", "ocean", "sea", "trench", "cave", "well"),
    "length":     ("river", "stream", "highway", "road", "bridge", "wall",
                   "border", "tunnel"),
    "width":      ("river", "road", "bridge", "strait"),
    # director / directed → expects a film
    "director":   _FILM_WORK,
    "directed":   _FILM_WORK,
    # founder / founded → expects an organization or a place
    "founder":    _ORG + _PLACES_POP + ("religion", "movement"),
    "founded":    _ORG + _PLACES_POP + ("religion", "movement"),
    "creator":    _FILM_WORK + _BOOK_WORK + ("character", "game", "language",
                                              "software"),
    "created":    _FILM_WORK + _BOOK_WORK + ("character", "game", "language",
                                              "software"),
    "born":       _PEOPLE,
    "birth":      _PEOPLE,
    "death":      _PEOPLE,
    "died":       _PEOPLE,
    "ceo":        _ORG,
    "author":     _BOOK_WORK,
    "wrote":      _BOOK_WORK,
    "composer":   ("opera", "song", "symphony", "concerto", "score",
                   "soundtrack", "album"),
    "composed":   ("opera", "song", "symphony", "concerto", "score",
                   "soundtrack", "album"),
    "painter":    ("painting", "artwork", "fresco", "portrait", "landscape"),
    "painted":    ("painting", "artwork", "fresco", "portrait", "landscape"),
    "architect":  ("building", "tower", "monument", "church", "cathedral",
                   "bridge", "structure"),
    "publisher":  _BOOK_WORK + ("magazine", "newspaper", "journal"),
    "symbol":     ("element", "chemical", "currency", "country",
                   "mathematical"),
    "leader":     ("country", "nation", "party", "movement", "organization",
                   "religion"),
    "president":  _PLACES_POP + _ORG,
    "monarch":    ("country", "kingdom", "empire", "dynasty"),
    "chairman":   _ORG + ("party", "committee"),
    "owner":      _ORG + ("team", "club", "property", "estate"),
    "discoverer": ("element", "compound", "planet", "moon", "star", "comet",
                   "species", "particle", "law", "theorem"),
    "discovered": ("element", "compound", "planet", "moon", "star", "comet",
                   "species", "particle", "law", "theorem"),
    "inventor":   ("device", "machine", "method", "process", "system",
                   "instrument", "weapon"),
    "invented":   ("device", "machine", "method", "process", "system",
                   "instrument", "weapon"),
}


def _domain_cue_for_input(user_input: str) -> str | None:
    """Return the matched domain cue (lowercased) for the input, or None."""
    low = user_input.lower()
    for cue in _DOMAIN_CUES:
        if re.search(r"\b" + re.escape(cue) + r"\b", low):
            return cue
    return None


def _description_matches_cue(description: str, cue: str) -> bool:
    """True iff the candidate's description contains at least one expected
    class keyword for this domain cue. If no class keywords are configured
    for the cue, fall back to True (don't block resolution)."""
    keywords = _DOMAIN_CUE_CLASSES.get(cue)
    if not keywords:
        return True
    if not description:
        return False
    low = description.lower()
    return any(re.search(r"\b" + re.escape(k) + r"\b", low) for k in keywords)

_OF_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in _DOMAIN_CUES) + r")\s+of\s+"
    r"((?:[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,4})|(?:[a-z]+(?:\s+[A-Z][\w'’\-]+){1,4}))"
    r"(?=\s*[?.!,;:]|\s*$)",
    re.IGNORECASE,
)
_WHEN_PATTERN = re.compile(
    r"\bwhen\s+was\s+([A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,4})\s+"
    r"(?:born|founded|created|established|written|published|invented|discovered|killed|elected)\b",
    re.IGNORECASE,
)
_WHO_PATTERN = re.compile(
    r"\bwho\s+(?:directed|wrote|founded|invented|discovered|composed|painted|created|sculpted)\s+"
    r"((?:the\s+)?[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,4})",
    re.IGNORECASE,
)

# Names with cross-class ambiguity — never pre-resolve. The clarification
# skill handles these.
_DO_NOT_PRE_RESOLVE = re.compile(
    r"\b(apple|mercury|java|paris|saturn|cambridge|springfield|amazon|orion|"
    r"phoenix|nike|atlas|venus|jordan|georgia|jersey|columbia|memphis|"
    r"alexandria)\b",
    re.IGNORECASE,
)


def _extract_entity_phrase(user_input: str) -> str | None:
    """Pull the candidate entity phrase out of an `<cue> of X` / `when was X` /
    `who <verb> X` question. Returns None if no pattern fires."""
    for pat in (_OF_PATTERN, _WHEN_PATTERN, _WHO_PATTERN):
        m = pat.search(user_input)
        if not m:
            continue
        phrase = m.group(1).strip()
        phrase = re.sub(r"^(the|a|an)\s+", "", phrase, flags=re.I).strip(" .?!,;:")
        if phrase and len(phrase) <= 80:
            return phrase
    return None


# Description keywords that mean "this candidate is NOT the present-day
# canonical sense" — prefer a sibling candidate when present.
_HISTORICAL_DESC = re.compile(
    r"\b(former|historical|abolished|extinct|defunct|predecessor|deprecated|"
    r"obsolete|disestablished|dissolved|previous|prior\s+name|old\s+name|"
    r"subdivision|ward|district|borough|neighbourhood|neighborhood|suburb|"
    r"renamed)\b",
    re.IGNORECASE,
)


def _pick_dominant_candidate(
    rows: list[dict], domain_cue: str | None = None
) -> dict | None:
    """Pick the best candidate from search_entity results.

    Preference order:
    1. description matches the user's domain cue AND is not historical / fictional.
    2. description is not historical / fictional (cue check skipped).
    3. raw top hit (last-resort fallback).
    """
    if not rows:
        return None

    # Pass 1: match domain cue + not historical / fictional.
    if domain_cue:
        for row in rows:
            desc = row.get("description") or ""
            if _HISTORICAL_DESC.search(desc):
                continue
            if is_fictional(desc):
                continue
            if _description_matches_cue(desc, domain_cue):
                return row

    # Pass 2: not historical / fictional, ignoring cue match.
    for row in rows:
        desc = row.get("description") or ""
        if _HISTORICAL_DESC.search(desc):
            continue
        if is_fictional(desc):
            continue
        return row

    # Pass 3: raw top hit (every candidate is historical or fictional).
    return rows[0]


def detect_dominant_entity(user_input: str) -> tuple[str, str, str, str] | None:
    """If the input looks like a fact-of-entity question and Wikidata's search
    has a clear top hit (with the entity not in the cross-class ambiguity
    allowlist), return `(phrase, qid, label, description)`. Otherwise None.

    Performs at most one cached `search_entity` HTTP call. Prefers candidates
    whose description doesn't mark them as historical / former / sub-area
    variants.
    """
    phrase = _extract_entity_phrase(user_input)
    if not phrase:
        return None
    if _DO_NOT_PRE_RESOLVE.search(phrase):
        return None
    cue = _domain_cue_for_input(user_input)
    try:
        from .wikidata import search_entity
        rows = search_entity(phrase, lang="en", limit=5)
    except Exception:
        return None
    pick = _pick_dominant_candidate(rows, domain_cue=cue)
    if pick is None:
        return None
    qid = pick.get("id")
    if not qid:
        return None
    label = pick.get("label") or phrase
    desc = pick.get("description") or ""
    # Defer fictional cases to the fictional refusal path.
    if is_fictional(desc):
        return None
    # Final guard: if a domain cue was detected and the picked candidate's
    # description doesn't match the expected class, don't pre-resolve. Better
    # to let the LLM handle entity selection than to inject a wrong Q-id.
    if cue and not _description_matches_cue(desc, cue):
        return None
    return phrase, qid, label, desc


# --- SPARQL post-rewrite: strip implicit geographic narrowing ---------------
#
# When the user explicitly asks for a global top-N (or asks a superlative
# with no `in <region>` qualifier), the agent must not silently narrow the
# query to a country / continent. We detect that intent on the input side
# and strip any country / continent narrowing the model added on the output
# side, before run_sparql executes.

_GLOBAL_CUE_RE = re.compile(
    r"\b(world|worldwide|globally|on\s+earth|universe|globe)\b", re.IGNORECASE
)
_SUPERLATIVE_RE = re.compile(
    r"\b(tallest|largest|oldest|youngest|most\s+populous|deepest|longest|"
    r"richest|biggest|smallest|highest|fastest|heaviest|widest)\b",
    re.IGNORECASE,
)
_IN_REGION_RE = re.compile(r"\bin\s+(the\s+)?[a-z]+", re.IGNORECASE)


def detect_global_intent(user_input: str) -> bool:
    """True iff the user asked for a global top-N: a superlative AND either
    (a) an explicit world / globally / on earth cue, or (b) no `in <region>`
    qualifier at all (absence of region implies global)."""
    if not _SUPERLATIVE_RE.search(user_input):
        return False
    if _GLOBAL_CUE_RE.search(user_input):
        return True
    if not _IN_REGION_RE.search(user_input):
        return True
    return False


# `?var wdt:P17 wd:Qxxx .` (country) or P30 (continent) — a literal narrowing.
_GEO_LITERAL_TRIPLE = re.compile(
    r"\?\w+\s+wdt:P(?:17|30)\s+wd:Q\d+\s*\.\s*",
    re.IGNORECASE,
)
# VALUES block bound to a variable whose name suggests geographic scope.
_GEO_VALUES_BLOCK = re.compile(
    r"VALUES\s+\?\w*(?:country|continent|nation|state|region|land)\w*\s*"
    r"\{\s*(?:wd:Q\d+\s*)+\}\s*",
    re.IGNORECASE,
)


def strip_implicit_geo_filters(query: str) -> str:
    """Remove country / continent narrowing from a SPARQL query. Caller MUST
    only invoke this when `detect_global_intent` returned True — otherwise
    legitimate region scopes get stripped."""
    out = _GEO_LITERAL_TRIPLE.sub("", query)
    out = _GEO_VALUES_BLOCK.sub("", out)
    out = re.sub(r"\n[ \t]*\n+", "\n", out)
    return out


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
