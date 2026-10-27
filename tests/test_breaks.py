"""The break check (`prism.impact.breaks`): a proposed new version of a function against its call sites."""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.impact.breaks import CHECK, FAILS, MEANING, BreakChecker, render_breaks, signature
from prism.impact.context import ChangeContextBuilder, render

PARSER = '''class ParseError(Exception):
    pass


class Parser:
    def parse_tuple(self, simplified=False, no_condexpr=False):
        return (simplified, no_condexpr)

    def parse_for(self):
        return self.parse_tuple(no_condexpr=True)

    def parse_target(self):
        return self.parse_tuple(True)

    def pull(self, frame, no_indent=False):
        return frame

    def visit(self, frame):
        self.pull(frame, True)

    def signature(self, node, frame, have_comma=True, extra=None):
        return node

    def visit_call(self, node, frame):
        self.signature(node, frame, False, None)

    def lookup(self, key):
        return {"a": 1}[key]

    def use_lookup(self):
        value = self.lookup("a")
        return value + 1

    def safe_lookup(self):
        try:
            return self.lookup("a")
        except ParseError:
            return None


def context(request, verify, cert):
    return (request, verify, cert)


def build(request):
    return context(request, True, None)


def renamed(resp, req, stream=False):
    return resp


def send(r, req):
    return renamed(r, req, stream=True)
'''

ROUTER = '''function use(path, fn) {
  return [path, fn];
}

function mount(app) {
  return use("/", app);
}
'''


def _checker(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    b = build_pipeline(str(tmp_path), use_cache=False)[0]
    cb = ChangeContextBuilder(b, str(tmp_path))
    return cb, BreakChecker(cb.deps)


def _found(checker, anchor, new_code):
    return {(x.caller.rsplit(".", 1)[-1], x.severity) for x in checker.check(anchor, new_code)}, \
        checker.check(anchor, new_code)


def test_signature_reads_python_and_javascript_parameters():
    s = signature("    def f(self, a, b=(1, 2), *args, c, d='x,y', **kw):\n        pass\n", "python")
    assert s.is_method and [(p.name, p.kind, p.default) for p in s.params] == [
        ("a", "positional", None), ("b", "positional", "(1, 2)"), ("args", "var_positional", None),
        ("c", "keyword_only", None), ("d", "keyword_only", "'x,y'"), ("kw", "var_keyword", None)]
    j = signature("function use(path, fn = noop, ...rest) {\n}", "javascript")
    assert j.name == "use" and [(p.name, p.kind) for p in j.params] == [
        ("path", "positional"), ("fn", "positional"), ("rest", "var_positional")]
    assert signature("function(name, fn){\n}", "javascript").name is None
    assert signature("x = 1", "python") is None


def test_removed_keyword_fails_and_renamed_flag_with_flipped_default_changes_meaning(tmp_path):
    _cb, ch = _checker(tmp_path, {"p.py": PARSER})
    found, breaks = _found(ch, "p.Parser.parse_tuple",
                           "    def parse_tuple(self, simplified=False, with_condexpr=True):\n        return 1\n")
    assert ("parse_for", FAILS) in found
    assert not any(x.caller.endswith("parse_target") for x in breaks)  # position 1 is unchanged
    found, breaks = _found(ch, "p.Parser.pull", "    def pull(self, frame, indent=True):\n        return frame\n")
    assert found == {("visit", MEANING)}
    assert "`no_indent=False` and is now `indent=True`" in breaks[0].problem


def test_removed_middle_parameter_shifts_arguments_and_too_many_fails(tmp_path):
    _cb, ch = _checker(tmp_path, {"p.py": PARSER})
    found, breaks = _found(ch, "p.Parser.signature",
                           "    def signature(self, node, frame, extra=None):\n        return node\n")
    assert found == {("visit_call", FAILS), ("visit_call", MEANING)}
    assert any("went to `have_comma`; it now goes to `extra`" in x.problem for x in breaks)


def test_new_required_parameter_fails_and_a_pure_rename_is_not_reported(tmp_path):
    _cb, ch = _checker(tmp_path, {"p.py": PARSER})
    found, _ = _found(ch, "p.context", "def context(request, verify, cert, poolmanager):\n    return 1\n")
    assert found == {("build", FAILS)}
    found, _ = _found(ch, "p.renamed", "def renamed(resp, prepared_request, stream=False):\n    return resp\n")
    assert found == set()


def test_new_exception_and_empty_return_are_checks_where_the_call_does_not_handle_them(tmp_path):
    _cb, ch = _checker(tmp_path, {"p.py": PARSER})
    new = ("    def lookup(self, key):\n        if key is None:\n            return None\n"
           "        if key == 'b':\n            raise ParseError(key)\n        return 1\n")
    found, breaks = _found(ch, "p.Parser.lookup", new)
    assert ("use_lookup", CHECK) in found
    assert not any(x.caller.endswith("safe_lookup") and "ParseError" in x.problem for x in breaks)  # caught
    assert not any(x.caller.endswith("safe_lookup") and "return nothing" in x.problem for x in breaks)  # returned


def test_javascript_shift_and_renamed_function(tmp_path):
    _cb, ch = _checker(tmp_path, {"r.js": ROUTER})
    found, breaks = _found(ch, "r.use", "function use(fn) {\n  return fn;\n}\n")
    assert ("mount", MEANING) in found and any("now goes to `fn`" in x.problem for x in breaks)
    assert not any("ignored" in x.problem for x in ch.check("r.use", "function use(fn) {\n  return arguments;\n}\n"))
    found, _ = _found(ch, "r.use", "function mountAt(path, fn) {\n  return 1;\n}\n")
    assert found == {("mount", FAILS)}


def test_context_with_new_code_adds_only_the_break_section(tmp_path):
    cb, _ch = _checker(tmp_path, {"p.py": PARSER})
    plain = render(cb.build("p.Parser.pull", budget_tokens=6000, include_tests=False))
    ctx = cb.build("p.Parser.pull", budget_tokens=6000, include_tests=False,
                   new_code="    def pull(self, frame, indent=True):\n        return frame\n")
    text = render(ctx)
    section = render_breaks(ctx.breaks)
    assert section in text and text.replace(section + "\n", "", 1) == plain
    assert "**changes meaning**" in section and "must change with it" in section


def test_cli_and_mcp_accept_new_code(tmp_path):
    from click.testing import CliRunner

    from prism.cli import main as cli_main
    from prism.mcp import server
    (tmp_path / "p.py").write_text(PARSER)
    new = tmp_path / "new.txt"
    new.write_text("    def pull(self, frame, indent=True):\n        return frame\n")
    out = CliRunner().invoke(cli_main, ["impact", "p.Parser.pull", "--repo", str(tmp_path), "--new-code", str(new)])
    assert out.exit_code == 0, out.output
    assert "## Calls this change breaks" in out.output and "**changes meaning**: `p.Parser.visit`" in out.output
    server._cache.clear()
    try:
        res = server.prism_change_context(repo_path=str(tmp_path), symbol="p.Parser.pull", new_code=new.read_text())
        plain = server.prism_change_context(repo_path=str(tmp_path), symbol="p.Parser.pull")
    finally:
        server._cache.clear()
    assert [(x["caller"], x["severity"]) for x in res["breaks"]] == [("p.Parser.visit", "changes meaning")]
    assert "breaks" not in plain and "## Calls this change breaks" not in plain["context"]
