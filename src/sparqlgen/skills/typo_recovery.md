# SKILL: typo recovery

The input looks like a misspelling of a well-known entity. If `search_entity` on the literal spelling returns no strong hit, retry ONCE with the corrected spelling and proceed. Do NOT ask the user to re-spell.

If a `[hint: Probable typo(s) detected: …]` annotation is present in the input, trust it and use the corrected name directly.
