# SKILL: non-English input

- Translate the question to English internally before planning.
- Prefer `search_entity(..., lang="en")` for Q-id resolution. If 0 results in English, retry in the user's language.
- Set `SERVICE wikibase:label` to `"<user_lang>,en"` so labels render in the user's language with English fallback.
- Use English variable names in SPARQL (e.g. `?president` not `?總統`).
- Reply in the user's input language.
