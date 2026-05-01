# SKILL: open-world negation

Wikidata is open-world — missing data does NOT prove a negative. `FILTER NOT EXISTS { ... }` only excludes entities for which the positive fact is recorded as absent.

For "X without Y": enumerate the full set of X and exclude those with Y. Warn the user the answer is necessarily incomplete — Wikidata may simply lack the positive fact for some entities.
