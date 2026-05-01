# SKILL: join direction

For relation joins, the person/agent is the OBJECT of the property, the work/film is the SUBJECT:

- "X starred in Y" → `?film wdt:P161 wd:<X>` (P161 = cast member)
- "X directed Y" → `?film wdt:P57 wd:<X>` (P57 = director)
- "X wrote Y" → `?work wdt:P50 wd:<X>` (P50 = author)

The film / work is the SUBJECT — never the person.
