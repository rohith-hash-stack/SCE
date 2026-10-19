"""JS/TS methods bound to more than one property name (Fix A, Layer 4
recall diagnostic): `res.set = res.header = function header(...)` - Express's
own shape - is one function reachable as `header` and `set`, and `res.type =
function contentType(...)` is `contentType` reachable as `type`. Before, the
outer `res.set` was never registered, so every `this.set(...)` in the file
resolved to the only other `set` in the repo (`app.set`) and `res.header` had
no callers at all."""
from __future__ import annotations

from prism.cli import build_pipeline

RESPONSE = """\
var res = module.exports = {};

res.set =
res.header = function header(field, val) {
  if (typeof field === 'object') {
    for (var key in field) { this.set(key, field[key]); }
    return this;
  }
  this.setHeader(field, val);
  return this;
};

res.contentType =
res.type = function contentType(type) {
  return this.set('Content-Type', type);
};

res.json = function json(obj) {
  this.set('Content-Type', 'application/json');
  return this.type('json');
};
"""

APPLICATION = """\
var app = module.exports = {};

app.set = function set(setting, val) {
  this.settings[setting] = val;
  return this;
};

app.enable = function enable(setting) {
  return this.set(setting, true);
};
"""


def _build(tmp_path):
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "response.js").write_text(RESPONSE)
    (repo / "lib" / "application.js").write_text(APPLICATION)
    builder, _tags = build_pipeline(str(repo))
    return builder


def _callers(builder, qname):
    g = builder.graph
    return sorted(p for p in g.predecessors(qname) if (g.get_edge_data(p, qname) or {}).get("relation") == "CALLS")


def test_every_property_in_a_chained_assignment_names_the_same_function(tmp_path):
    b = _build(tmp_path)
    assert b.symbol_table.get("lib.response.header").aliases == ("set",)
    assert b.symbol_table.get("lib.response.contentType").aliases == ("type",)  # `res.contentType = res.type = ...`
    assert b.symbol_table.get("lib.response.set") is None  # an alias, not a second symbol
    assert b.symbol_table.get("lib.response.json").aliases == ()  # `res.json = function json`: no extra name


def test_this_calls_resolve_to_the_same_objects_method_not_a_same_named_one_elsewhere(tmp_path):
    b = _build(tmp_path)
    assert _callers(b, "lib.response.header") == ["lib.response.contentType", "lib.response.header", "lib.response.json"]
    assert _callers(b, "lib.response.contentType") == ["lib.response.json"]  # `this.type(...)` via the `type` alias
    assert _callers(b, "lib.application.set") == ["lib.application.enable"]  # app's own `this.set`, and only that
    assert not [n for n in b.graph if str(n).startswith("<ambiguous:set@")]


def test_aliases_survive_the_index_cache(tmp_path):
    from prism.graph.symbol_table import GlobalSymbolTable, SymbolInfo
    table = GlobalSymbolTable()
    table.add(SymbolInfo("lib.response.header", "function", "lib/response.js", (3, 11), "javascript", "lib.response",
                         aliases=("set",)))
    assert table.resolve_in_module("lib.response", "set") == "lib.response.header"
    assert [s.qualified_name for s in table.candidates_for_simple_name("set")] == ["lib.response.header"]
    table.add(SymbolInfo("lib.response.set", "function", "lib/response.js", (20, 22), "javascript", "lib.response"))
    assert table.resolve_in_module("lib.response", "set") == "lib.response.set"  # a real definition wins the slot
