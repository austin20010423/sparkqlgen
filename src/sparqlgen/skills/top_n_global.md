# SKILL: top-N global superlative

For "the N <-est> in the world" questions (tallest, largest, oldest, most populous, deepest, longest, richest, biggest, smallest, highest, fastest, heaviest, widest) WITHOUT an explicit region named, do NOT add `wdt:P17`, `wdt:P30`, or `VALUES ?country { ... }` filters.

Match the entity class only, ORDER BY the metric DESC, LIMIT N. Implicit geographic scope is almost always wrong and changes the answer.

Only add geographic filters when the user's text explicitly names the region.
