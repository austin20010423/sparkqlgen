# SKILL: temporal qualifier pattern

For "X during YEAR / between Y1 and Y2 / as of YEAR" questions about positions, memberships, or leadership, use the qualifier pattern (NOT direct `wdt:`):

```sparql
?person p:P39 ?stmt .
?stmt ps:P39 wd:<position> .
?stmt pq:P580 ?start .
OPTIONAL { ?stmt pq:P582 ?end . }
FILTER(?start <= "<YEAR-END>"^^xsd:dateTime)
FILTER(!BOUND(?end) || ?end >= "<YEAR-START>"^^xsd:dateTime)
```

Same pattern with `p:P463` / `ps:P463` for "member of organization during YEAR".

NEVER write `wdt:P580` — start/end are qualifiers, not direct properties. Always include all holders whose tenure overlaps the window.
