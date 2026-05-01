# SKILL: ambiguity clarification

When `search_entity` returns 2+ candidates whose senses span DIFFERENT classes or domains (company vs fruit, planet vs element vs deity, language vs island, namesake cities) AND the user's text alone does not pick one, STOP and ask which sense — without calling `run_sparql`.

## Strict-ask rule for cross-class names

For names that have well-known senses across DIFFERENT classes (the cross-class ambiguity allowlist — e.g. Apple, Mercury, Java, Paris, Saturn, Cambridge, Springfield, Amazon, Phoenix, Atlas, Venus, Jordan, Georgia, Columbia, Memphis, Alexandria), ALWAYS ASK when ALL of the following hold:

- The question is short (≤ ~8 words).
- The user provided no fiscal year, time window, version, or domain modifier (e.g. "annual revenue 2023", "Mercury the planet", "Java island").
- The question is bare-bones — just `<cue> of <name>` or `tell me about <name>`.

Even if a domain cue (revenue, size, born, population) plausibly picks one sense, ASK in this case. The user's intent is under-specified and the answer changes drastically by sense. The runtime's dominance shortcut does NOT apply to cross-class ambiguous names.

## Reply format

> Your query isn't specific enough yet. I found N possible interpretations:
>
> 1. `<Q-id>` — `<label>`: `<description>`
> 2. `<Q-id>` — `<label>`: `<description>`
>
> Which one? (Or rephrase with more detail.)

## When NOT to clarify

- Cross-class name absent: one candidate is dominant for the asked-for domain (see core rule 7) and the name is not in the cross-class list.
- A `[resolved: …]` annotation is present and the resolution looks valid (see `verify_resolved_entity` skill).
- Other candidates are minor / historical / sub-area / lesser-known namesakes.
