from sparqlgen import compaction


def _user(text):
    return {"role": "user", "content": text}


def _assistant(text=None, tool_calls=None):
    msg = {"role": "assistant", "content": text or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _tool_call(call_id, name, args):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def test_estimate_tokens_basic():
    h = [_user("a" * 400), _assistant("b" * 400)]
    # ~800 chars / 4 = ~200 tokens
    assert 150 < compaction.estimate_tokens(h) < 250


def test_estimate_tokens_includes_tool_calls():
    h = [
        _assistant(
            None,
            tool_calls=[_tool_call("c1", "search_entity", '{"query":"' + "x" * 400 + '"}')],
        ),
    ]
    assert compaction.estimate_tokens(h) > 50


def test_find_cut_point_too_few_turns():
    # Only one real user turn — nothing to cut
    h = [_user("hi"), _assistant("hello")]
    assert compaction.find_cut_point(h, keep_last_turns=2) is None


def test_find_cut_point_skips_tool_messages():
    # Three user turns with tool calls in between; keep_last_turns=2
    h = [
        _user("turn 1"),
        _assistant(
            None, tool_calls=[_tool_call("c1", "search_entity", "{}")]
        ),
        _tool("c1", "[]"),
        _assistant("answer 1"),
        _user("turn 2"),
        _assistant(
            None, tool_calls=[_tool_call("c2", "run_sparql", "{}")]
        ),
        _tool("c2", "{}"),
        _assistant("answer 2"),
        _user("turn 3"),
    ]
    cut = compaction.find_cut_point(h, keep_last_turns=2)
    # Should land at the start of turn 2
    assert cut == 4
    assert h[cut]["content"] == "turn 2"


def test_find_cut_point_keep_one_turn():
    h = [
        _user("turn 1"),
        _assistant("a1"),
        _user("turn 2"),
        _assistant("a2"),
        _user("turn 3"),
    ]
    cut = compaction.find_cut_point(h, keep_last_turns=1)
    # Last user turn is index 4 → everything before should be cut
    assert cut == 4


def test_find_cut_point_does_not_split_tool_group():
    # A tool message must always have its parent assistant message before it.
    # Verify the cut never sits between an assistant tool_calls and its tool replies.
    h = [
        _user("turn 1"),
        _assistant(None, tool_calls=[_tool_call("c1", "search_entity", "{}")]),
        _tool("c1", "[]"),
        _assistant("answer 1"),
        _user("turn 2"),
        _assistant("answer 2"),
        _user("turn 3"),
    ]
    cut = compaction.find_cut_point(h, keep_last_turns=2)
    # The cut must be at a `role: user` index
    assert h[cut]["role"] == "user"
    # Everything before the cut should still be a complete unit
    before = h[:cut]
    # No dangling tool message at the end of the cut prefix
    if before:
        assert before[-1]["role"] != "tool"
