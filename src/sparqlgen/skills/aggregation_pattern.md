# SKILL: aggregation pattern

For "top N by metric" questions, aggregate to one row per entity — otherwise the same entity repeats once per historical statement (population over time, multiple coordinates, etc.):

```sparql
SELECT ?x ?xLabel (MAX(?val) AS ?value) WHERE {
  ?x wdt:P31/wdt:P279* wd:<class> .
  ?x wdt:<metric_pid> ?val .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
GROUP BY ?x ?xLabel
ORDER BY DESC(?value)
LIMIT N
```

## Always add ORDER BY for "list N"

For "list N <entities>" requests where N is small and the candidate pool is large (e.g. "list 5 presidents of <country>", "list 10 films directed by <director>", "list 3 works by <author>"), you MUST add an `ORDER BY` clause. Without one, SPARQL returns an arbitrary slice that varies between query plans and rarely overlaps with the prominent N the user expects.

Pick a deterministic, prominence-correlated ordering:

- People → `ORDER BY DESC(?dob)` for recent figures (most recent first); `ORDER BY ?dob` for historical-only sets.
- Places / works / things → `ORDER BY DESC(?metric)` when a numeric metric fits (population, area, box office, citations).
- Films / books / albums → `ORDER BY DESC(?publication_date)`.
- When no metric or date applies → `ORDER BY ?xLabel` (alphabetical) as a deterministic fallback.

Add the ordering even when the user didn't ask for "top" or "most" — the goal is a deterministic, recognizable slice, not an arbitrary one. Bind the ordering variable with `OPTIONAL` so entities missing the field still appear at the end rather than dropping out.
