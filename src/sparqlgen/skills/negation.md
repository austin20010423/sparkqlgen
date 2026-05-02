# SKILL: open-world negation

Wikidata is open-world — missing data does NOT prove a negative. `FILTER NOT EXISTS { ... }` only excludes entities for which the positive fact is recorded as absent.

For "X without Y": enumerate the full set of X and exclude those with Y. Warn the user the answer is necessarily incomplete — Wikidata may simply lack the positive fact for some entities.

## Prefer dedicated negation classes

Wikidata often models a "negative" property as a dedicated class — e.g. `landlocked country`, `stateless person`, `extinct language`, `dwarf planet`, `atheist`, `unrecognized state`, `non-binding referendum`. Before composing a `FILTER NOT EXISTS`, call `search_entity` on the negative phrase itself ("landlocked country", "stateless person", "extinct language", …). If a class with that meaning exists, instantiate it directly:

```sparql
?x wdt:P31/wdt:P279* wd:<dedicated-negative-class> .
```

This is far more reliable than deriving via `FILTER NOT EXISTS`, because Wikidata's open-world incompleteness doesn't bite the dedicated-class path: the data is curated to mean exactly what the class name says.

## Two-level FILTER NOT EXISTS pattern

When negation is structural ("X without any Y of type Z", "X with no related entity that is a Z"), the inner block needs TWO triples — the first binds the related entity, the second asserts what makes it undesired:

```sparql
# WRONG — excludes every entity that has the relation at all:
FILTER NOT EXISTS { ?x wdt:Pxxx ?related . }

# RIGHT — excludes only entities whose related-entity matches the bad class:
FILTER NOT EXISTS {
  ?x wdt:Pxxx ?related .
  ?related wdt:P31/wdt:P279* wd:<bad-class> .
}
```

The first triple binds the variable so the inner block has something to filter on. The second triple defines the property that makes the related entity "bad". Drop the second triple and you exclude every entity that has the relation at all — almost never what the user means.

This pattern is general — applies to "countries without coastline" (no neighbor that is a sea), "people without children of citizenship X" (no child whose P27 is X), "cities without restaurants of cuisine Y", etc.
