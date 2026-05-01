# SKILL: fictional / mythological refusal

If a candidate's description contains "fictional", "mythological", "legendary", "mythical", "literary", or "imaginary", OR a `fictional_warning` annotation is present, DO NOT call `run_sparql` on this turn.

Reply with text that:

(a) explicitly identifies the entity as fictional / mythological / legendary / literary, AND
(b) says Wikidata does not record real-world facts (date of birth, population, biography, location, measurements) for it.

The reply MUST contain at least one of: `fictional`, `mythological`, `legendary`, `literary`, `fiction`, `not real`, `character`, `no recorded population`.

Do NOT follow up with an in-fiction relation query (creator, first appearance, author) on the same turn — the user can ask in a separate turn.
