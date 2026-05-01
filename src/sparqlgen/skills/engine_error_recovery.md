# SKILL: engine-error self-repair

When `run_sparql` returns an error matching `java.lang.StackOverflowError`, `QueryTimeoutException`, `503`, `engine_error`, or any "stack overflow" / "timeout" message, the SPARQL is too expensive for the Blazegraph endpoint. Do NOT retry the same query — rewrite it. Apply changes one at a time across the (at most 2) self-repair attempts:

1. Replace property paths like `wdt:P31/wdt:P279*` with the single direct hop `wdt:P31`. Property-path expansion is the most common cause of stack overflows.
2. Drop `OPTIONAL` blocks for non-essential properties. Keep only those required to answer the question.
3. Remove `SERVICE wikibase:label { ... }` from the inner `WHERE`. Labels can be fetched with a separate query or skipped — they're never load-bearing.
4. Lower `LIMIT` (e.g. from 100 to 30 to 10) and split large `VALUES { wd:Q… wd:Q… wd:Q… }` blocks into multiple smaller queries.
5. For aggregation queries, drop `(MAX(?val) AS ?value)` + `GROUP BY` and use the plain `?val` with `ORDER BY DESC(?val)` instead. The result will have duplicates, but it executes in a fraction of the cost and the user's answer is the top N anyway.

If the error persists after 2 rewrites, return a short message saying the data set is too large for Wikidata's public endpoint to materialize for this question, and suggest the user narrow the scope (a specific country, a tighter time window, a smaller class).
