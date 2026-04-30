from sparqlgen.repl import _looks_like_clarification, _parse_options


def test_clarification_did_you_mean():
    assert _looks_like_clarification(
        "Did you mean Apple Inc. or the fruit? Which one?"
    )


def test_clarification_not_specific():
    assert _looks_like_clarification(
        "Your query isn't specific enough — 'Java' has multiple matches. "
        "Could you rephrase?"
    )


def test_normal_answer_not_flagged():
    assert not _looks_like_clarification(
        "Tokyo is the most populous city in Japan with 14M residents."
    )


def test_question_alone_not_enough():
    # A regular answer that happens to contain a "?" but no clarification cue
    assert not _looks_like_clarification("Did you know Tokyo has 14M people?")


def test_parse_options_basic():
    text = (
        "Your query isn't specific enough — 'Apple' has multiple matches:\n"
        "1. Apple Inc. (Q312) — American technology company\n"
        "2. apple (Q89) — fruit\n"
        "3. Apple Records (Q6498542) — record label\n"
        "Which one?"
    )
    opts = _parse_options(text)
    assert len(opts) == 3
    assert opts[0].startswith("Apple Inc. (Q312)")
    assert opts[1].startswith("apple (Q89)")
    assert opts[2].startswith("Apple Records (Q6498542)")


def test_parse_options_paren_format():
    text = "1) Tokyo (Q1490)\n2) Kyoto (Q34600)"
    opts = _parse_options(text)
    assert len(opts) == 2


def test_parse_options_single_option_ignored():
    # A single numbered point is not a multi-choice clarification
    text = "1. Some heading\nthen prose"
    assert _parse_options(text) == []


def test_parse_options_unordered_input():
    text = "3. Apple Records\n1. Apple Inc.\n2. apple fruit"
    opts = _parse_options(text)
    assert opts[0] == "Apple Inc."
    assert opts[1] == "apple fruit"
    assert opts[2] == "Apple Records"


def test_parse_options_no_match_in_normal_text():
    text = "Tokyo is the capital of Japan. It has 14 million residents."
    assert _parse_options(text) == []
