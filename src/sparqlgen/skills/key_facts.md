# SKILL: key-facts default for vague entity asks

After ambiguity is resolved (or when the user gives a bare entity name), default to a query that returns the most informative properties for that entity type. Use `OPTIONAL` blocks so missing properties don't drop rows:

- **Place**: label, P31 (instance of), P17 (country), P1082 (population), P625 (coordinates), P2046 (area).
- **Person**: label, P106 (occupation), P569 (birth), P570 (death), P27 (citizenship).
- **Organization**: label, P31, P17, P571 (inception), P112 (founder).
- **Creative work**: label, P50/P57/P170 (creator), P577 (publication), P136 (genre).
