"""Thin wrappers around the Wikidata MediaWiki API + SPARQL endpoint.

These are the *primitives* the agent calls through tool-use. Each function
returns plain dicts/lists so they serialize cleanly back to the LLM.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from SPARQLWrapper import JSON, SPARQLWrapper

from . import cache
from .config import settings


_API = "https://www.wikidata.org/w/api.php"


def _http_get(params: dict[str, Any]) -> dict[str, Any]:
    headers = {"User-Agent": settings.wikidata_user_agent}
    with httpx.Client(timeout=20.0, headers=headers) as client:
        r = client.get(_API, params=params)
        r.raise_for_status()
        return r.json()


def search_entity(query: str, lang: str = "en", limit: int = 5) -> list[dict[str, Any]]:
    """wbsearchentities for entities (Q-ids)."""
    key = f"ent::{lang}::{query.lower()}::{limit}"
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    data = _http_get(
        {
            "action": "wbsearchentities",
            "search": query,
            "language": lang,
            "uselang": lang,
            "type": "item",
            "limit": limit,
            "format": "json",
        }
    )
    from . import hardening as _h
    out = []
    for item in data.get("search", []):
        desc = item.get("description", "")
        entry = {
            "id": item["id"],
            "label": item.get("label", ""),
            "description": desc,
            "aliases": item.get("aliases", []),
        }
        if _h.is_fictional(desc):
            entry["fictional_warning"] = (
                "This entity is fictional/mythological. Wikidata records in-fiction "
                "attributes (creator, first appearance, in-universe relations) but NOT "
                "real-world data such as biographical dates, real-world location, or "
                "physical measurements. Do not fabricate factual data; if the user is "
                "asking for real-world facts about a fictional entity, tell them so."
            )
        out.append(entry)
    cache.put(key, out)
    return out


def search_property(query: str, lang: str = "en", limit: int = 5) -> list[dict[str, Any]]:
    """wbsearchentities for properties (P-ids)."""
    key = f"prop::{lang}::{query.lower()}::{limit}"
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    data = _http_get(
        {
            "action": "wbsearchentities",
            "search": query,
            "language": lang,
            "uselang": lang,
            "type": "property",
            "limit": limit,
            "format": "json",
        }
    )
    out = [
        {
            "id": item["id"],
            "label": item.get("label", ""),
            "description": item.get("description", ""),
        }
        for item in data.get("search", [])
    ]
    cache.put(key, out)
    return out


def get_entity(qid: str, lang: str = "en") -> dict[str, Any]:
    """wbgetentities — schema discovery only.

    Returns the list of property ids this entity has claims for, with their
    counts. Deliberately does NOT return the label or description — the agent
    must use `run_sparql` to fetch any user-visible content. Without this
    constraint, models will use this tool's output as a shortcut answer and
    skip generating a SPARQL query.
    """
    key = f"get::v2::{lang}::{qid}"
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    data = _http_get(
        {
            "action": "wbgetentities",
            "ids": qid,
            "languages": lang,
            "props": "claims",
            "format": "json",
        }
    )
    ents = data.get("entities", {})
    if qid not in ents:
        return {"id": qid, "exists": False}
    e = ents[qid]
    if "missing" in e:
        return {"id": qid, "exists": False}
    claims = e.get("claims", {}) or {}
    out = {
        "id": qid,
        "exists": True,
        "property_ids_with_counts": {pid: len(vals) for pid, vals in claims.items()},
        "note": "Schema only — no label/description returned. Use run_sparql for user-visible data.",
    }
    cache.put(key, out)
    return out


class UnsafeQueryError(ValueError):
    pass


def classify_error(err: str) -> tuple[str, str | None]:
    """Map a raw Wikidata/Blazegraph error blob to (short_summary, llm_hint).

    The raw stack traces from Wikidata's Blazegraph engine can be hundreds of
    lines long and trash the LLM context window without telling it anything
    actionable. This collapses them into a one-line cause + a concrete fix
    suggestion the model can react to.
    """
    low = err.lower()

    if (
        "stackoverflow" in low
        or "collectvarsfromexpressions" in low
        or "java.lang.stackoverflow" in low
    ):
        return (
            "wikidata sparql engine stack-overflowed (Blazegraph bug, not your query semantics)",
            (
                "Simplify the query and retry. Most common triggers: "
                "(a) open property paths like `wdt:P31/wdt:P279*` — replace with a single `wdt:P31` "
                "or a small explicit `VALUES` list of subclass Q-ids; "
                "(b) huge `VALUES` blocks — split into multiple smaller queries; "
                "(c) deeply nested `FILTER`/`IF`/`BIND` expressions — flatten them; "
                "(d) too many `OPTIONAL` blocks — remove the ones you don't actually need."
            ),
        )

    if "timeout" in low or "timeoutexception" in low or "java.util.concurrent.timeout" in low:
        return (
            "query timed out at the Wikidata endpoint (60s hard limit)",
            (
                "Add a tighter LIMIT, narrow filters earlier in the WHERE clause, "
                "remove expensive joins, or replace property paths with a single hop."
            ),
        )

    if "malformedqueryexception" in low or "parse error" in low or "encountered " in low:
        # Truncate, but keep enough for the model to see where the parser choked
        snip = err[:300]
        return (f"SPARQL parse error: {snip}", "Fix the syntax and retry.")

    # Generic — truncate to keep context budget under control
    snip = err if len(err) <= 300 else err[:280] + "…(truncated)"
    return (snip, None)


def run_sparql(query: str, timeout_s: int = 60) -> dict[str, Any]:
    """Execute a SPARQL query against the Wikidata endpoint with retry.

    Goes through Phase 3 hardening: rejects writes, auto-adds LIMIT,
    runs a cheap syntax sanity check first.
    """
    from . import hardening

    try:
        hardening.assert_safe(query)
    except hardening.QueryError as e:
        raise UnsafeQueryError(str(e)) from e

    err = hardening.basic_sparql_validate(query)
    if err:
        return {"ok": False, "error": f"syntax: {err}", "rows": [], "columns": []}

    query = hardening.auto_limit(query, default=100)

    sparql = SPARQLWrapper(
        settings.wikidata_sparql_endpoint,
        agent=settings.wikidata_user_agent,
    )
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    sparql.setTimeout(timeout_s)

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            t0 = time.time()
            res = sparql.query().convert()
            elapsed = time.time() - t0
            bindings = res.get("results", {}).get("bindings", [])
            head = res.get("head", {}).get("vars", [])
            rows = []
            for b in bindings:
                rows.append({k: v.get("value") for k, v in b.items()})
            from . import hardening as _h
            warning = _h.detect_quality_issue(rows, head)
            out = {
                "ok": True,
                "columns": head,
                "rows": rows,
                "elapsed_s": round(elapsed, 3),
            }
            if warning:
                out["quality_warning"] = warning
            return out
        except Exception as e:  # broad: SPARQLWrapper raises many flavors
            last_error = e
            time.sleep(1.5 * (attempt + 1))
    short, hint = classify_error(str(last_error))
    out: dict[str, Any] = {"ok": False, "error": short, "rows": [], "columns": []}
    if hint:
        out["hint"] = hint
    return out
