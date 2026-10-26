"""PRISM digest (`prism.summaries.digest`): line-cited facts about each function's own code, with the
conditions they run under and a certainty level - every supported language, dynamic code, no false facts.

Built from small, self-contained repos (no network, no model). The golden files under
`tests/fixtures/digest/` pin the full rendered digest of every function in one fixture per language;
regenerate them with `PRISM_UPDATE_DIGEST_GOLDEN=1 pytest tests/test_digest.py` only when a change to the
digest is intended, and review the diff.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from prism.cli import build_pipeline
from prism.graph.entry_points import find_entry_points
from prism.summaries.digest import (
    CERTAIN,
    CERTAINTIES,
    KINDS,
    POSSIBLE,
    RUNTIME,
    UNKNOWN,
    DigestBuilder,
    digest_function,
    load_runtime_edges,
)

GOLDEN = Path(__file__).parent / "fixtures" / "digest"


def build(tmp_path, files: dict[str, str]):
    root = tmp_path / "repo"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return build_pipeline(str(root), use_cache=False)[0]


def digests(tmp_path, files, runtime_edges=None, max_facts=40):
    b = build(tmp_path, files)
    db = DigestBuilder(b, find_entry_points(b), runtime_edges=runtime_edges, max_facts=max_facts)
    return b, {q: db.digest(q) for q in b._def_nodes
               if b.symbol_table.get(q) is not None and b.symbol_table.get(q).kind in ("function", "method")}


def facts(d, kind=None):
    return [f for f in d.facts if kind is None or f.kind == kind]


def one(d, kind, contains="", exact=None):
    found = [f for f in facts(d, kind) if (f.text == exact if exact is not None else contains in f.text)]
    assert len(found) == 1, (kind, contains, d.render())
    return found[0]


# --------------------------------------------------------------------- #
# Python: conditions, exits, state, calls
# --------------------------------------------------------------------- #
PY = {
    "app/ports.py": '''import os
from json import loads


def normalize_port(port, scheme):
    if port is None or port == "":
        return None
    try:
        value = int(port)
    except ValueError:
        raise ValueError("bad port")
    return None if value == {"http": 80}.get(scheme) else value


def classify(n):
    if n < 0:
        return "negative"
    elif n == 0:
        return "zero"
    else:
        return "positive"


def quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass
    finally:
        print("done")


def scan(items, seen):
    for item in items:
        if not item:
            continue
        if item == "stop":
            break
        seen.append(item)
    while seen:
        seen.pop()


def with_lock(lock, data):
    with lock:
        data["k"] = 1
    assert data, "empty"
    yield data


def settings_user(settings, request):
    debug = os.environ.get("DEBUG")
    key = os.environ["SECRET"]
    agent = request.headers["User-Agent"]
    return settings.TIMEOUT, debug, key, agent


def parse(raw):
    return loads(raw)


def match_kind(k):
    match k:
        case "a":
            return 1
        case _:
            return 0
''',
    "app/store.py": '''COUNTER = 0
REGISTRY = {}


class Store:
    def __init__(self):
        self.items = []
        self.total = 0

    def add(self, item):
        self.items.append(item)
        self.total += 1
        REGISTRY[item] = self

    def reset(self):
        global COUNTER
        COUNTER = 0
        del self.items[:]

    def helper(self):
        raise RuntimeError("helper fails")

    def caller(self):
        self.helper()
        return self.total

    @property
    def size(self):
        return len(self.items)

    @staticmethod
    def make():
        return Store()

    @unknown_wrapper
    def wrapped(self):
        return 1
''',
}


def test_python_conditions_attach_to_their_facts(tmp_path):
    _b, ds = digests(tmp_path, PY)
    d = ds["app.ports.normalize_port"]
    ret = [f for f in facts(d, "return") if f.text == "None"]
    assert ret and ret[0].when == ('port is None or port == ""',) and ret[0].lines == (7, 7)
    raised = one(d, "raise", "bad port")
    assert raised.when == ("on ValueError",) and raised.text == 'raise ValueError("bad port")'
    assert one(d, "catch").text == "catches ValueError: raises"
    assert one(d, "assign").text == "value = int(port)"
    assert not facts(d, "call")  # nested builtin / unknown calls are already visible in their statement
    assert any(f.kind == "return" and "else value" in f.text and f.when == () for f in d.facts)


def test_python_elif_else_negate_earlier_conditions(tmp_path):
    _b, ds = digests(tmp_path, PY)
    d = ds["app.ports.classify"]
    whens = {f.text: f.when for f in facts(d, "return")}
    assert whens['"negative"'] == ("n < 0",)
    assert whens['"zero"'] == ("not (n < 0)", "n == 0")
    assert whens['"positive"'] == ("not (n < 0)", "not (n == 0)")


def test_python_swallowed_error_finally_and_loops(tmp_path):
    _b, ds = digests(tmp_path, PY)
    q = ds["app.ports.quiet"]
    assert one(q, "catch").text == "catches OSError: ignores the error"
    assert one(q, "call", "print").when == ("finally (always)",)
    s = ds["app.ports.scan"]
    skip = one(s, "flow", "next iteration")
    assert skip.when == ("for each item in items", "not item")
    assert one(s, "flow", "leave").when == ("for each item in items", 'item == "stop"')
    assert one(s, "state", "append").when == ("for each item in items",)
    assert one(s, "state", "pop").when == ("while seen",)


def test_python_with_assert_yield_and_match(tmp_path):
    _b, ds = digests(tmp_path, PY)
    d = ds["app.ports.with_lock"]
    assert one(d, "state", 'data["k"]').when == ("within with lock",)
    assert one(d, "raise").text.startswith("assert data")
    assert one(d, "yield").text == "data"
    m = ds["app.ports.match_kind"]
    assert {f.text: f.when for f in facts(m, "return")} == {"1": ('k matches "a"',), "0": ("k matches no other case",)}


def test_python_reads_settings_env_headers(tmp_path):
    _b, ds = digests(tmp_path, PY)
    reads = {f.text for f in facts(ds["app.ports.settings_user"], "read")}
    assert reads == {"environment DEBUG", "environment SECRET", "headers User-Agent", "setting TIMEOUT"}


def test_python_statement_calls_name_their_targets(tmp_path):
    files = {"m.py": "import json\nfrom os import path\n\n\ndef helper():\n    return 1\n\n\n"
                     "def go(raw):\n    json.dumps(raw)\n    path.exists(raw)\n    print(raw)\n    helper()\n"
                     "    raw.strip()\n    return helper()\n"}
    _b, ds = digests(tmp_path, files)
    d = ds["m.go"]
    targets = {f.text: (f.certainty, f.targets) for f in facts(d, "call")}
    assert targets["json.dumps(raw)"] == (CERTAIN, ("library json.dumps",))
    assert targets["path.exists(raw)"] == (CERTAIN, ("library os.path.exists",))
    assert targets["print(raw)"] == (CERTAIN, ("builtin print",))
    assert targets["helper()"] == (CERTAIN, ("m.helper",))
    assert targets["raw.strip()"] == (UNKNOWN, ("method on argument raw, type not resolved",))
    nested = one(d, "calls")  # `return helper()`: the return shows the code, the nested fact its target
    assert nested.targets == ("m.helper",) and "return helper() [calls: helper() -> m.helper]" in d.render()
    assert d.render().count("m.helper") == 2  # the nested fact is attached to its statement, not repeated


def test_python_state_owners(tmp_path):
    _b, ds = digests(tmp_path, PY)
    add = ds["app.store.Store.add"]
    texts = [f.text for f in facts(add, "state")]
    assert "self.items.append(item) changes self.items (own state)" in texts
    assert "self.total += 1 (own state)" in texts
    assert 'REGISTRY[item] = self (module constant REGISTRY)' in texts
    reset = ds["app.store.Store.reset"]
    assert any(f.text.startswith("deletes self.items[:]") for f in facts(reset, "state"))
    init = ds["app.store.Store.__init__"]
    assert {f.text for f in facts(init, "state")} == {"self.items = [] (own state)", "self.total = 0 (own state)"}


def test_python_decorators_known_and_unknown(tmp_path):
    _b, ds = digests(tmp_path, PY)
    assert one(ds["app.store.Store.size"], "decorator").certainty == CERTAIN
    assert "static method" in one(ds["app.store.Store.make"], "decorator").text
    unknown = one(ds["app.store.Store.wrapped"], "decorator")
    assert unknown.certainty == POSSIBLE and "defined by the decorator" in unknown.text


# --------------------------------------------------------------------- #
# No false facts
# --------------------------------------------------------------------- #
def test_callee_behaviour_is_not_attributed_to_the_caller(tmp_path):
    _b, ds = digests(tmp_path, PY)
    caller = ds["app.store.Store.caller"]
    assert not facts(caller, "raise") and not facts(caller, "state")  # the raise lives in `helper`
    call = one(caller, "call", "self.helper")
    assert call.certainty == CERTAIN and call.targets == ("app.store.Store.helper",)
    assert facts(ds["app.store.Store.helper"], "raise")


def test_test_expectations_stay_in_the_test(tmp_path):
    files = {"pkg/calc.py": "def double(x):\n    return x * 2\n",
             "tests/test_calc.py": "from pkg.calc import double\n\n\ndef test_double():\n    assert double(2) == 4\n"}
    _b, ds = digests(tmp_path, files)
    prod = ds["pkg.calc.double"].render()
    assert "4" not in prod and "assert" not in prod
    test = ds["tests.test_calc.test_double"]
    assert one(test, "raise").text == "assert double(2) == 4"


def test_every_fact_is_inside_its_function_and_well_formed(tmp_path):
    b, ds = digests(tmp_path, PY)
    for q, d in ds.items():
        lo, hi = d.lines
        for f in d.facts:
            assert f.kind in KINDS and f.certainty in CERTAINTIES and f.text.strip(), (q, f)
            assert lo <= f.lines[0] <= f.lines[1] <= hi, (q, f)
            if f.kind in ("call", "calls") and f.certainty == CERTAIN:
                for t in f.targets:  # a certain target is a real index symbol or a labelled library name
                    assert t in b.symbol_table or t in b.graph or t.split(" ", 1)[0] in ("builtin", "standard", "library"), (q, t)


# --------------------------------------------------------------------- #
# Dynamic code: patterns stated, candidates only from the code, never invented
# --------------------------------------------------------------------- #
DYN = {
    "app/dyn.py": '''import importlib


class Visitor:
    def visit(self, node, kind):
        handler = getattr(self, "visit_" + kind)
        return handler(node)

    def visit_a(self, node):
        return 1

    def visit_b(self, node):
        return 2

    def named(self):
        return getattr(self, "visit_a")

    def opaque(self, obj, name):
        return getattr(obj, name)

    def poke(self, name, value):
        setattr(self, name, value)


def on_a():
    return "a"


def on_b():
    return "b"


def route(key):
    table = {"a": on_a, "b": on_b}
    return table[key]()


def apply(fn, value):
    return fn(value)


def use_apply():
    return apply(on_a, 3)


def run_code(src):
    exec(src)


def load(name):
    return importlib.import_module(name)


def load_fixed():
    return importlib.import_module("json")
''',
}


def test_reflection_with_prefix_lists_matching_methods_only(tmp_path):
    _b, ds = digests(tmp_path, DYN)
    f = one(ds["app.dyn.Visitor.visit"], "dynamic", "looks up")
    assert f.certainty == POSSIBLE
    assert f.targets == ("app.dyn.Visitor.visit_a", "app.dyn.Visitor.visit_b")
    assert f.raw == 'handler = getattr(self, "visit_" + kind)'
    call = one(ds["app.dyn.Visitor.visit"], "dynamic", "calls handler")  # the bound result keeps the candidates
    assert call.certainty == POSSIBLE and call.targets == f.targets


def test_reflection_with_a_literal_name_is_resolved(tmp_path):
    _b, ds = digests(tmp_path, DYN)
    f = one(ds["app.dyn.Visitor.named"], "dynamic")
    assert f.certainty == CERTAIN and f.targets == ("app.dyn.Visitor.visit_a",) and f.raw is None


def test_unknowable_targets_are_unknown_with_the_raw_line(tmp_path):
    _b, ds = digests(tmp_path, DYN)
    f = one(ds["app.dyn.Visitor.opaque"], "dynamic")
    assert f.certainty == UNKNOWN and f.targets == () and f.raw == "return getattr(obj, name)"
    e = one(ds["app.dyn.run_code"], "dynamic")
    assert e.certainty == UNKNOWN and "evaluates code" in e.text
    imp = one(ds["app.dyn.load"], "dynamic")
    assert imp.certainty == UNKNOWN and imp.text == "imports a module named at runtime"
    fixed = one(ds["app.dyn.load_fixed"], "dynamic")
    assert fixed.certainty == CERTAIN and fixed.targets == ("json",)


def test_setattr_is_a_dynamic_state_write(tmp_path):
    _b, ds = digests(tmp_path, DYN)
    d = ds["app.dyn.Visitor.poke"]
    assert one(d, "dynamic").certainty == UNKNOWN
    assert one(d, "state").text == "sets an attribute of self by name (own state)"


def test_dispatch_table_candidates_come_from_the_table(tmp_path):
    _b, ds = digests(tmp_path, DYN)
    f = one(ds["app.dyn.route"], "dynamic")
    assert f.certainty == POSSIBLE and f.targets == ("app.dyn.on_a", "app.dyn.on_b")


def test_callback_candidates_come_from_callers(tmp_path):
    _b, ds = digests(tmp_path, DYN)
    f = one(ds["app.dyn.apply"], "dynamic")
    assert f.text == "calls the function passed as argument fn"
    assert f.certainty == POSSIBLE and f.targets == ("app.dyn.on_a",)


def test_callback_without_callers_is_unknown(tmp_path):
    _b, ds = digests(tmp_path, {"m.py": "def apply(fn, v):\n    return fn(v)\n"})
    f = one(ds["m.apply"], "dynamic")
    assert f.certainty == UNKNOWN and f.targets == ()


def test_runtime_trace_confirms_a_dynamic_target(tmp_path):
    edges = {("app.dyn.Visitor.opaque", "app.dyn.Visitor.visit_b"), ("app.dyn.apply", "app.dyn.on_b")}
    _b, ds = digests(tmp_path, DYN, runtime_edges=edges)
    cb = one(ds["app.dyn.apply"], "dynamic")
    assert cb.certainty == POSSIBLE  # on_b was observed, but it is not among the code's candidates
    extra = one(ds["app.dyn.apply"], "runtime")
    assert extra.certainty == RUNTIME and extra.targets == ("app.dyn.on_b",)
    edges = {("app.dyn.apply", "app.dyn.on_a")}
    _b, ds = digests(tmp_path / "again", DYN, runtime_edges=edges)
    cb = one(ds["app.dyn.apply"], "dynamic")
    assert cb.certainty == RUNTIME and cb.targets == ("app.dyn.on_a",)
    assert not facts(ds["app.dyn.apply"], "runtime")


def test_load_runtime_edges_reads_trace_files(tmp_path):
    path = tmp_path / "run_1.jsonl"
    path.write_text("\n".join([json.dumps({"caller": "a.f", "callee": "a.g", "source": "pytest_tracer"}),
                               json.dumps({"caller": None, "callee": None, "source": "otel", "sink_tag": "DB"}),
                               "not json"]) + "\n")
    assert load_runtime_edges([path]) == {("a.f", "a.g")}


# --------------------------------------------------------------------- #
# JavaScript / TypeScript
# --------------------------------------------------------------------- #
TS = {
    "src/a.ts": '''export function f(xs: number[], key: string, cb: (x: number) => void) {
  if (!xs) { throw new Error("none"); } else if (key) { this.k = 1; } else { return 2; }
  for (const x of xs) { cb(x); }
  switch (key) { case "a": obj[key](); break; default: return 3; }
  try { JSON.parse(key); } catch (e) { console.log(e); }
  xs.map(x => { if (x > 1) { throw new RangeError("big"); } return x * 2; });
  const url = process.env.API_URL;
  Reflect.apply(cb, null, [1]);
  return key ? 1 : 0;
}
''',
}


def test_typescript_constructs(tmp_path):
    _b, ds = digests(tmp_path, TS)
    d = ds["src.a.f"]
    assert one(d, "raise", "none").when == ("!xs",)
    assert one(d, "state").when == ("not (!xs)", "key")
    assert one(d, "return", exact="2").when == ("not (!xs)", "not (key)")
    cb = [f for f in facts(d, "dynamic") if "argument cb" in f.text]
    assert cb and cb[0].when == ("for each x in xs",)
    sub = one(d, "dynamic", "selected by key")
    assert sub.when == ('key matches "a"',) and sub.certainty == UNKNOWN
    assert one(d, "return", exact="3").when == ("key matches no other case",)
    assert one(d, "call", "console.log").when == ("on any error (e)",)
    assert one(d, "raise", "big").when == ("in a callback passed to xs.map", "x > 1")
    assert one(d, "read").text == "environment API_URL"
    assert one(d, "dynamic", "Reflect").certainty == UNKNOWN


# --------------------------------------------------------------------- #
# Java, C#, Go
# --------------------------------------------------------------------- #
JAVA = {
    "src/A.java": '''class A {
  int n;
  int f(List<String> xs, String k) {
    if (xs == null) throw new IllegalArgumentException("x"); else { this.n = 2; }
    for (String x : xs) { System.out.println(x); }
    switch (k) { case "a": return 1; default: break; }
    try { Class.forName(k).getMethod("m").invoke(null); } catch (IOException | RuntimeException e) { throw e; }
    xs.forEach(x -> log(x));
    return 0;
  }
  @Override
  public String toString() { return "A"; }
}
''',
}


def test_java_constructs(tmp_path):
    _b, ds = digests(tmp_path, JAVA)
    d = ds["src.A.A.f"]
    assert one(d, "raise", "IllegalArgumentException").when == ("xs == null",)
    assert one(d, "state").text == "this.n = 2 (own state)" and one(d, "state").when == ("not (xs == null)",)
    assert one(d, "call", "println").when == ("for each x in xs",)
    assert one(d, "return", exact="1").when == ('k matches "a"',)
    reflect = facts(d, "dynamic")
    assert len(reflect) == 1 and reflect[0].certainty == UNKNOWN and "forName" in reflect[0].raw
    assert one(d, "catch").text == "catches IOException | RuntimeException: raises"
    assert one(d, "raise", "throw e").when == ("on IOException | RuntimeException",)
    assert "overrides a base method" in one(ds["src.A.A.toString"], "decorator").text


CS = {
    "src/A.cs": '''class A {
  int n;
  int F(List<string> xs, string k) {
    if (xs == null) throw new ArgumentException("x"); else { this.n = 2; }
    foreach (var x in xs) { Console.WriteLine(x); }
    switch (k) { case "a": return 1; default: break; }
    try { var t = Type.GetType(k); } catch (IOException e) { throw; }
    var me = this.GetType();
    return 0;
  }
}
''',
}


def test_csharp_constructs(tmp_path):
    _b, ds = digests(tmp_path, CS)
    d = ds["src.A.A.F"]
    assert one(d, "raise", "ArgumentException").when == ("xs == null",)
    assert one(d, "state").text == "this.n = 2 (own state)"
    assert one(d, "call", "WriteLine").targets == ("standard library Console.WriteLine",)
    assert one(d, "return", exact="1").when == ('k matches "a"',)
    reflect = facts(d, "dynamic")
    assert len(reflect) == 1 and "Type.GetType" in reflect[0].text  # `this.GetType()` is not a lookup by name
    assert one(d, "raise", "re-raises").when == ("on IOException",)


GO = {
    "go.mod": "module example.com/a\n\ngo 1.21\n",
    "a.go": '''package a

import "reflect"

type Server struct {
	handlers []func(*Server)
	index    int
	count    int
}

func (s *Server) Next() {
	s.index++
	for s.index < len(s.handlers) {
		if s.handlers[s.index] != nil {
			s.handlers[s.index](s)
		}
		s.index++
	}
}

func F(xs []string, k string, cb func(string)) (int, error) {
	if xs == nil {
		return 0, nil
	}
	for _, x := range xs {
		cb(x)
	}
	switch k {
	case "a":
		return 1, nil
	default:
		panic("bad")
	}
}

func Kind(v interface{}) string {
	defer cleanup()
	return reflect.TypeOf(v).String()
}

func cleanup() {}
''',
}


def test_go_constructs(tmp_path):
    _b, ds = digests(tmp_path, GO)
    nxt = ds["a.Server.Next"]
    assert [f.text for f in facts(nxt, "state")] == ["s.index++ (own state)", "s.index++ (own state)"]
    dyn = one(nxt, "dynamic")
    assert dyn.when == ("while s.index < len(s.handlers)", "s.handlers[s.index] != nil")
    assert dyn.certainty == UNKNOWN and dyn.raw == "s.handlers[s.index](s)"
    f = ds["a.F"]
    assert one(f, "dynamic", "argument cb").when == ("for each _, x in xs",)
    assert one(f, "raise", "panic").when == ("k matches no other case",)
    assert one(f, "return", "1, nil").when == ('k matches "a"',)
    kind = ds["a.Kind"]
    assert one(kind, "call", "cleanup").when == ("deferred until the function returns",)
    assert one(kind, "call", "cleanup").targets == ("a.cleanup",)


# --------------------------------------------------------------------- #
# Whole-index guarantees
# --------------------------------------------------------------------- #
def test_every_function_gets_a_deterministic_digest(tmp_path):
    files = {**PY, **DYN, **TS, **JAVA, **CS, **GO,
             "src/types.ts": "export interface Shape { area(): number; }\nexport function noop() {}\n"}
    b = build(tmp_path, files)
    entries = find_entry_points(b)
    fns = [q for q in b._def_nodes if b.symbol_table.get(q) and b.symbol_table.get(q).kind in ("function", "method")]
    first = {q: DigestBuilder(b, entries).digest(q).render() for q in fns}
    second = DigestBuilder(b, entries)
    assert all(second.digest(q).render() == first[q] for q in fns)
    assert len(first) == len(fns) >= 35
    assert digest_function(b, "src.types.noop").note == "empty body"
    json.dumps(DigestBuilder(b).digest("app.ports.normalize_port").to_dict())


def test_budget_keeps_high_rank_facts_and_counts_the_rest(tmp_path):
    body = "".join(f"    v{i} = {i}\n" for i in range(60))
    _b, ds = digests(tmp_path, {"big.py": f"def big(x):\n{body}    if x:\n        raise ValueError(x)\n    return x\n"},
                     max_facts=10)
    d = ds["big.big"]
    assert len(d.facts) == 10 and d.omitted == {"assign": 52}
    assert one(d, "raise").when == ("x",) and one(d, "return").text == "x"
    assert d.render().endswith("omitted: 52 assign")


def test_route_entry_point_is_a_fact(tmp_path):
    files = {"app/web.py": "from flask import Flask\napp = Flask(__name__)\n\n\n@app.route('/users', methods=['GET'])\n"
                           "def users():\n    return []\n"}
    _b, ds = digests(tmp_path, files)
    d = ds["app.web.users"]
    assert facts(d, "entry") and "/users" in one(d, "entry").text


# --------------------------------------------------------------------- #
# Golden digests, one fixture per language
# --------------------------------------------------------------------- #
GOLDEN_SETS = {"python": {**PY, **DYN}, "typescript": TS, "java": JAVA, "csharp": CS, "go": GO}


@pytest.mark.parametrize("name", sorted(GOLDEN_SETS))
def test_golden_digests(tmp_path, name):
    _b, ds = digests(tmp_path, GOLDEN_SETS[name])
    rendered = "\n\n".join(f"## {q}\n{ds[q].render()}" for q in sorted(ds)) + "\n"
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("PRISM_UPDATE_DIGEST_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
    assert path.read_text() == rendered


def test_module_function_calls_are_not_state_changes(tmp_path):
    files = {"app/registry.py": "ITEMS = []\n\n\ndef add(x):\n    return x\n",
             "app/use.py": "import os\nfrom app import registry\nfrom app.registry import ITEMS\n\n\n"
                           "def go(p):\n    os.remove(p)\n    registry.add(p)\n    ITEMS.append(p)\n"}
    _b, ds = digests(tmp_path, files)
    states = [f.text for f in facts(ds["app.use.go"], "state")]
    assert states == ["ITEMS.append(p) changes ITEMS (imported ITEMS)"]


def test_global_declaration_makes_writes_state(tmp_path):
    _b, ds = digests(tmp_path, PY)
    reset = ds["app.store.Store.reset"]
    assert [f.text for f in facts(reset, "state")] == ["COUNTER = 0 (global COUNTER)", "deletes self.items[:] (own state)"]
    assert not facts(reset, "assign")


def test_break_that_ends_a_switch_case_is_not_a_loop_exit(tmp_path):
    _b, ds = digests(tmp_path, JAVA)
    assert not facts(ds["src.A.A.f"], "flow")
    _b, ds = digests(tmp_path / "go", GO)
    assert [f.text for f in facts(ds["a.F"], "raise")] == ['panic("bad") (panics)']
    assert not facts(ds["a.F"], "call")  # the panic is stated once


# --------------------------------------------------------------------- #
# Call targets: a graph match by method name alone is never presented as certain
# --------------------------------------------------------------------- #
TYPED = {
    "src/Writer.java": "class Writer { void name(String s) {} void close() {} }\n",
    "src/Reader.java": "class Reader { void name(String s) {} void close() {} }\n",
    "src/Use.java": '''class Use {
  void f(Writer out, java.util.List<String> keys, Object any) {
    out.name("k");
    Reader r = new Reader();
    r.close();
    keys.add("x");
  }
}
''',
    "app/io.py": '''class Writer:
    def close(self):
        return 1


class Reader:
    def close(self):
        return 2


def typed(w: Writer, maybe: "Reader | None"):
    w.close()
    local = Reader()
    local.close()


def untyped(thing):
    thing.close()
''',
}


def test_declared_receiver_type_picks_the_target(tmp_path):
    _b, ds = digests(tmp_path, TYPED)
    f = ds["src.Use.Use.f"]
    targets = {c.text: (c.certainty, c.targets) for c in facts(f, "call")}
    assert targets['out.name("k")'] == (CERTAIN, ("src.Writer.Writer.name",))
    assert targets["r.close()"] == (CERTAIN, ("src.Reader.Reader.close",))
    assert targets['keys.add("x")'] == (CERTAIN, ("method of List, a type outside the index",))
    py = ds["app.io.typed"]
    targets = {c.text: (c.certainty, c.targets) for c in facts(py, "call")}
    assert targets["w.close()"] == (CERTAIN, ("app.io.Writer.close",))
    assert targets["local.close()"] == (CERTAIN, ("app.io.Reader.close",))


def test_untyped_receiver_matched_by_name_is_only_possible(tmp_path):
    b, ds = digests(tmp_path, TYPED)
    call = one(ds["app.io.untyped"], "call")
    assert call.certainty in (POSSIBLE, UNKNOWN)
    assert set(call.targets) <= {"app.io.Writer.close", "app.io.Reader.close", "method on argument thing, type not resolved"}


def test_closure_and_inline_callback_parameters_are_dynamic(tmp_path):
    files = {"app/deco.py": "import functools\n\n\ndef login_required(view):\n    @functools.wraps(view)\n"
                            "    def wrapped_view(**kwargs):\n        return view(**kwargs)\n    return wrapped_view\n",
             "src/run.ts": "export function runAll(hooks: any[]) {\n  hooks.forEach((fn) => { fn(1) })\n}\n"}
    _b, ds = digests(tmp_path, files)
    closure = one(ds["app.deco.wrapped_view"], "dynamic")
    assert closure.certainty == UNKNOWN and "captured from the enclosing function" in closure.text
    inline = one(ds["src.run.runAll"], "dynamic")
    assert inline.text == "calls the function passed to an inline callback as fn"
    assert inline.when == ("in a callback passed to hooks.forEach",)


def test_dunder_import_is_a_dynamic_import(tmp_path):
    _b, ds = digests(tmp_path, {"m.py": "def load(name):\n    __import__(name)\n    __import__('json')\n"})
    texts = {f.text: f.certainty for f in facts(ds["m.load"], "dynamic")}
    assert texts == {"imports a module named at runtime": UNKNOWN, "imports module 'json' at runtime": CERTAIN}


def test_fall_through_and_multi_label_cases_list_every_value(tmp_path):
    files = {"src/s.ts": "export function f(x: any) {\n  switch (typeof x) {\n    case 'boolean':\n    case 'number':\n"
                         "      return 1\n    default:\n      return 0\n  }\n}\n",
             "src/S.java": "class S { int g(String k) { switch (k) { case \"a\": case \"b\": return 1; default: return 0; } } }\n",
             "m.py": "def h(k):\n    match k:\n        case 1 | 2:\n            return 'low'\n        case _:\n            return 'other'\n"}
    _b, ds = digests(tmp_path, files)
    assert one(ds["src.s.f"], "return", exact="1").when == ("typeof x matches 'boolean' or 'number'",)
    assert one(ds["src.S.S.g"], "return", exact="1").when == ('k matches "a" or "b"',)
    assert one(ds["src.S.S.g"], "return", exact="0").when == ("k matches no other case",)
    assert one(ds["m.h"], "return", exact="'low'").when == ("k matches 1 | 2",)


def test_calls_of_local_function_values(tmp_path):
    files = {"src/l.js": "function go(app, x) {\n  var fn = app.get('etag fn')\n  var twice = (v) => v * 2\n"
                         "  twice(x)\n  return fn(x)\n}\nmodule.exports = go\n"}
    _b, ds = digests(tmp_path, files)
    d = ds["src.l.go"]
    assert one(d, "dynamic").text == "calls the function held in local variable fn"
    assert one(d, "call", "twice").targets in (("inline function twice",), ("src.l.twice",))


def test_annotation_wording_differs_from_decorators(tmp_path):
    files = {"src/A.java": "class A { @CanIgnoreReturnValue String f() { return \"\"; } }\n"}
    _b, ds = digests(tmp_path, files)
    assert "comes from whatever processes it" in one(ds["src.A.A.f"], "decorator").text


def test_static_calls_on_library_types(tmp_path):
    files = {"src/P.java": "class Parser { static int Parse(String s) { return 1; } }\n",
             "src/U.java": "class U { void f(String s) {\n  BigInteger.Parse(s);\n  Parser.Parse(s);\n} }\n",
             "src/C.cs": "class C {\n  public Repo Store { get; set; }\n  void F(string s) {\n"
                         "    Store.Save(s);\n    System.Convert.ToInt32(s);\n  }\n}\n"}
    _b, ds = digests(tmp_path, files)
    java = {f.text: f.targets for f in facts(ds["src.U.U.f"], "call")}
    assert java["BigInteger.Parse(s)"] == ("method of BigInteger, a type outside the index",)
    assert java["Parser.Parse(s)"] == ("src.P.Parser.Parse",)
    cs = {f.text: f.targets for f in facts(ds["src.C.C.F"], "call")}
    assert cs["System.Convert.ToInt32(s)"] == ("method of System.Convert, a type outside the index",)
    assert cs["Store.Save(s)"] != ("method of Store, a type outside the index",)  # `Store` is this class's property


# --------------------------------------------------------------------- #
# Regressions from the second fresh sample (see reports/digest/REPORT.md)
# --------------------------------------------------------------------- #
def test_separate_blocks_with_the_same_condition_are_not_merged(tmp_path):
    files = {"src/f.ts": "export function f(t: string, c: string) {\n  let i = g(t)\n  if (i === -1) i = h(t)\n"
                         "  if (i === -1) {\n    k(c)\n  }\n  return i\n}\n"}
    _b, ds = digests(tmp_path, files)
    rendered = ds["src.f.f"].render(header=False)
    assert rendered.count("when i === -1:") == 2, rendered


def test_chained_callback_labels_name_the_right_call(tmp_path):
    files = {"src/c.ts": "export function f(xs: any[]) {\n  return xs\n    .filter((c) => c.ok)\n"
                         "    .map((c) => { if (c.a) { throw new Error('x') } return c })\n}\n"}
    _b, ds = digests(tmp_path, files)
    assert one(ds["src.c.f"], "raise").when == ("in a callback passed to xs.filter(…).map", "c.a")


def test_csharp_declarations_and_object_initialisers(tmp_path):
    files = {"src/T.cs": "class T {\n  void F() {\n    string json = Make(150);\n"
                         "    var s = new Settings { MaxDepth = 3 };\n  }\n  string Make(int n) { return \"\"; }\n}\n"}
    _b, ds = digests(tmp_path, files)
    d = ds["src.T.T.F"]
    assigns = [f.text for f in facts(d, "assign")]
    assert assigns == ["json = Make(150)", "s = new Settings { MaxDepth = 3 }"]


def test_csharp_using_alias_and_nested_static_classes_are_in_the_index(tmp_path):
    files = {"src/Base.cs": "namespace Tests {\n  public class Fixture {\n    public static class StringAssert {\n"
                            "      public static void AreEqual(string a, string b) {}\n    }\n  }\n"
                            "  public class XUnitAssert {\n    public static void AreEqual(object a, object b) {}\n  }\n}\n",
             "src/Use.cs": "using Assert = Tests.XUnitAssert;\nnamespace Tests {\n  public class Use : Fixture {\n"
                           "    public void Go() {\n      Assert.AreEqual(1, 1);\n      StringAssert.AreEqual(\"a\", \"a\");\n"
                           "    }\n  }\n}\n"}
    _b, ds = digests(tmp_path, files)
    calls = {f.text: f.targets for f in facts(ds["Tests.Use.Go"], "call")}  # C# names follow the namespace
    assert calls["Assert.AreEqual(1, 1)"] == ("Tests.XUnitAssert.AreEqual",)
    assert not any("outside the index" in t for t in calls['StringAssert.AreEqual("a", "a")'])


def test_members_of_repository_objects_are_not_libraries(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/signals.py": "class Signal:\n    def send(self, x):\n        return x\n\n\npushed = Signal()\n",
             "pkg/use.py": "from pkg.signals import pushed\n\n\ndef go():\n    pushed.send(1)\n"}
    b, ds = digests(tmp_path, files)
    call = one(ds["pkg.use.go"], "call")
    assert not any(t.startswith("library ") for t in call.targets), call


# --------------------------------------------------------------------- #
# Regressions from the third fresh sample (see reports/digest/REPORT.md)
# --------------------------------------------------------------------- #
def test_imported_library_receiver_names_its_target(tmp_path):
    files = {"go.mod": "module example.com/r\n\ngo 1.21\n",
             "codec/json.go": "package codec\n\ntype jsonApi struct{}\n\nfunc (j jsonApi) Marshal(v any) ([]byte, error) { return nil, nil }\n",
             "render/yaml.go": "package render\n\nimport \"gopkg.in/yaml.v3\"\n\nfunc Render(d any) error {\n"
                               "\tyaml.Marshal(d)\n\t_, err := yaml.Marshal(d)\n\treturn err\n}\n"}
    _b, ds = digests(tmp_path, files)
    d = ds["render.yaml.Render"]
    assert one(d, "call").targets == ("library gopkg.in.yaml.v3.Marshal",)  # Go names the package `yaml`
    assert "jsonApi" not in d.render()  # not the repository's same-named method


def test_tagless_switch_cases_are_conditions(tmp_path):
    files = {"go.mod": "module example.com/s\n\ngo 1.21\n",
             "s.go": "package s\n\nfunc f(r int) int {\n\tswitch {\n\tcase r > 1:\n\t\treturn 1\n\tdefault:\n\t\treturn 0\n\t}\n}\n"}
    _b, ds = digests(tmp_path, files)
    whens = {f.text: f.when for f in facts(ds["s.f"], "return")}
    assert whens == {"1": ("r > 1",), "0": ("no other case holds",)}


def test_fully_qualified_type_does_not_match_a_same_named_index_class(tmp_path):
    files = {"src/JsonParser.java": "package com.google.gson;\nclass JsonParser { void parse() {} }\n",
             "src/Bench.java": "package com.google.gson.metrics;\nclass Bench { void run() {\n"
                               "  com.fasterxml.jackson.core.JsonParser jp = make();\n  jp.close();\n} }\n"}
    _b, ds = digests(tmp_path, files)
    call = one(next(d for q, d in ds.items() if q.endswith("Bench.run")), "call", "jp.close")
    assert call.targets == ("method of JsonParser, a type outside the index",)


def test_increment_inside_an_assignment_is_stated_once(tmp_path):
    files = {"src/f.ts": "export class S {\n  i = 0\n  n = 3\n  step() {\n    this.i = ++this.i % this.n\n  }\n}\n"}
    _b, ds = digests(tmp_path, files)
    assert [f.text for f in facts(ds["src.f.S.step"], "state")] == ["this.i = ++this.i % this.n (own state)"]


def test_csharp_lambda_argument_is_labelled_by_its_call(tmp_path):
    files = {"src/T.cs": "class T {\n  void F() {\n    Run(() => {\n      throw new System.Exception(\"x\");\n    });\n  }\n"
                         "  void Run(System.Action a) { a(); }\n}\n"}
    _b, ds = digests(tmp_path, files)
    assert one(ds["src.T.T.F"], "raise").when == ("in a callback passed to Run",)


def test_coverage_harness_checks_and_samples_a_repo(tmp_path):
    from harness.experiments.digest import coverage
    root = tmp_path / "repo"
    for rel, text in {**PY, **DYN}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    assert coverage.main(["check", str(tmp_path / "out"), f"toy={root}"]) == 0
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())[0]
    assert summary["functions"] == summary["stats"]["digested"] >= 30
    assert summary["crashes"] == summary["violations"] == summary["nondeterministic"] == 0
    assert coverage.main(["sample", str(tmp_path / "s"), "--seed", "1", f"toy={root}"]) == 0
    text = (tmp_path / "s" / "sample_1.txt").read_text()
    assert text.count("########## toy [") >= 1 and "---- digest" in text
