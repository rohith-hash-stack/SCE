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
