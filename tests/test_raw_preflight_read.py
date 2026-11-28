"""The raw pre-flight's call check (`r4/raw_preflight.call_read_ok`): the raw reply must be read faithfully as a call
to the same tool; a different argument value chosen by the model (the Phase 2 smoke pre-flight) is not a failure."""
from harness.experiments.r4.raw_preflight import call_read_ok


def test_same_tool_and_faithful_read():
    raw = 'prism_read_code{"symbol": "src/a.ts"}'
    assert call_read_ok(["prism_read_code", {"symbol": "src/a.object"}], ["prism_read_code", {"symbol": "src/a.ts"}], raw)
    assert call_read_ok(None, None, "text")


def test_failures():
    raw = 'prism_read_code{"symbol": "src/a.ts"}'
    assert not call_read_ok(["prism_read_code", {"symbol": "x"}], None, raw)                     # not read as a call
    assert not call_read_ok(["read_file", {"path": "x"}], ["prism_read_code", {"symbol": "src/a.ts"}], raw)
    assert not call_read_ok(["prism_read_code", {"symbol": "x"}], ["prism_read_code", {"symbol": "garbled"}], raw)


def test_escaped_values():
    raw = '{"tool": "edit_file", "args": {"path": "a.py", "old": "x = \\"y\\"\\n", "new": ""}}'
    assert call_read_ok(["edit_file", {}], ["edit_file", {"path": "a.py", "old": 'x = "y"\n', "new": ""}], raw)


def test_cases_cover_the_arms_asked_with_their_own_tools():
    """Phase 3: the pre-flight renders the cell's own arms (their system prompts and native tool lists)."""
    from harness.experiments.r3.loop import native_tools, system_prompt
    from harness.experiments.r4.raw_preflight import cases
    got = cases(("BASELINE_LSP", "PRISM_LSP", "PRISM_ONLY"))
    assert [n.split(":", 1)[0] for n, _ in got] == ["BASELINE_LSP"] * 2 + ["PRISM_LSP"] * 2 + ["PRISM_ONLY"] * 2
    for name, msgs in got:
        arm = name.split(":", 1)[0]
        assert msgs[0]["content"] == system_prompt(arm)
        names = {t["function"]["name"] for t in native_tools(arm)}
        assert ("find_references" in names) == (arm == "BASELINE_LSP")
        assert ("prism_find_references" in names) == (arm != "BASELINE_LSP")
    assert [n for n, _ in cases()][0].startswith("PRISM:")                    # the default is unchanged
