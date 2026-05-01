# SKILL: result-quality self-repair

After every `run_sparql`, check for a `quality_warning` field:

- **0 rows**: filters too narrow — drop the most restrictive triple, swap a P-id, or replace direct properties with `wdt:P31/wdt:P279*`. If the entity Q-id came from a non-English search, retry `search_entity(..., lang="en")`.
- **All rows identical**: missing `SELECT DISTINCT` or a Cartesian product — add `DISTINCT` or `GROUP BY ?entity` with `MAX(?value)`.
- **Many duplicates**: a 1-to-many property is multiplying rows — aggregate, or filter by `pq:P585` (point in time).

Retry at most twice for the same warning class. After that, tell the user honestly the data isn't in Wikidata in the form the question expects.
