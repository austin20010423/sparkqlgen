from sparqlgen.wikidata import classify_error


def test_blazegraph_stack_overflow_collapsed():
    raw = (
        "java.lang.StackOverflowError\n"
        + "\tat com.bigdata.rdf.sparql.ast.StaticAnalysis.collectVarsFromExpressions(StaticAnalysis.java:2101)\n" * 200
    )
    short, hint = classify_error(raw)
    assert "engine" in short.lower() or "blazegraph" in short.lower()
    assert hint is not None
    assert "wdt:P31/wdt:P279*" in hint  # specific actionable advice
    # The summary itself must NOT be the 200-line trace
    assert len(short) < 200


def test_timeout_classified():
    raw = "java.util.concurrent.TimeoutException: query took too long"
    short, hint = classify_error(raw)
    assert "timed out" in short.lower() or "timeout" in short.lower()
    assert hint is not None and "LIMIT" in hint


def test_parse_error_classified():
    raw = "MalformedQueryException: Encountered \"WHERE\" at line 3 column 8"
    short, hint = classify_error(raw)
    assert "parse" in short.lower()
    assert hint is not None


def test_unknown_error_truncated():
    raw = "x" * 5000
    short, hint = classify_error(raw)
    assert len(short) <= 300
    # No hint for unknown errors
    assert hint is None


def test_short_unknown_error_passes_through():
    raw = "connection refused"
    short, hint = classify_error(raw)
    assert short == "connection refused"
    assert hint is None
