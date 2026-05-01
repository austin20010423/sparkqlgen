# SKILL: verify the runtime-resolved entity

The runtime has injected a `[resolved: <phrase> → <Q-id> (<label>): <description>]` annotation. This means an upstream search has picked a top hit for the phrase. **Verify it before trusting it:**

- If the description contains "former", "historical", "abolished", "extinct", "subdivision", "ward", "district", "predecessor", "deprecated" — AND the user asked about a *current / present-day* fact (population, capital, current leader) — the resolved Q-id is probably the wrong sense.
- If the description's class doesn't match the user's domain cue (user asked for population but the entity is a person; user asked for director but the entity is a country) — the resolution is wrong.

When the resolution looks wrong:

1. Call `search_entity(<phrase>)` once with `lang="en"`.
2. Pick the candidate whose description best matches the user's domain cue and is *not* marked former/historical/extinct.
3. Use that Q-id and proceed.

When the resolution looks right (matches domain cue, no historical marker), use the Q-id directly without re-searching. Do not ask the user to clarify — the dominant entity has already been identified.
