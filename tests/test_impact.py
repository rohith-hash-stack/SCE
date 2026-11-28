"""Change context (`prism.impact`): how callers depend on a function (layer 3), what it relies on in its
callees, the style to follow (siblings, conventions), its group and codebase (layers 4-5), and the
assembled package (`prism impact`, `prism.change_context`). Small self-contained repos, no model."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from prism.cli import build_pipeline
from prism.cli import main as cli_main
from prism.impact.context import ChangeContextBuilder, render
from prism.impact.dependencies import DependencyAnalyzer
from prism.impact.groups import GroupIndex
from prism.impact.style import conventions, siblings
from prism.summaries.digest import CERTAIN, POSSIBLE, DigestBuilder

PORTS = '''import asyncio


class PortError(ValueError):
    pass


def normalize_port(port, scheme):
    if port is None or port == "":
        return None
    try:
        value = int(port)
    except ValueError:
        raise PortError("bad port")
    return None if value == {"http": 80}.get(scheme) else value


def build_url(host, port, scheme="http"):
    p = normalize_port(port, scheme=scheme)
    if p is None:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{p.real}"


def safe_port(port):
    try:
        return normalize_port(port, "http")
    except PortError:
        return None


def any_port(port):
    try:
        normalize_port(port, "http")
    except Exception:
        pass


def has_port(port, scheme):
    if normalize_port(port, scheme):
        return True
    return False


def all_ports(ports):
    for p in normalize_ports(ports):
        print(p)
    log_port(normalize_port(ports[0], "http"))


def normalize_ports(ports):
    return [normalize_port(p, "http") for p in ports]


def log_port(p):
    print(p)


async def fetch(port):
    return port


async def run(port):
    a = await fetch(port)
    b = fetch(port)
    return a, b
'''

TESTS = '''from app.ports import normalize_port


def test_normalize_port():
    content = normalize_port("8080", "http")
    assert content == 8080
'''


def build(tmp_path, files):
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return str(root), build_pipeline(str(root), use_cache=False)[0]


@pytest.fixture()
def ports(tmp_path):
    root, b = build(tmp_path, {"app/__init__.py": "", "app/ports.py": PORTS, "tests/test_ports.py": TESTS})
    return root, b, DependencyAnalyzer(b, repo_root=root)


def deps_of(reports, caller):
    report = next(r for r in reports if r.caller == caller)
    return {d.aspect: [x.text for x in report.dependencies if x.aspect == d.aspect] for d in report.dependencies}


# --------------------------------------------------------------------- #
# Layer 3: how callers depend on the anchor
# --------------------------------------------------------------------- #
def test_result_use_is_read_at_each_call_site(ports):
    _root, _b, an = ports
    reports = an.callers("app.ports.normalize_port")
    assert deps_of(reports, "app.ports.build_url")["result"] == [
        "stores the result in `p`, then checks `p is None`; reads .real"]
    assert deps_of(reports, "app.ports.has_port")["result"] == [
        "branches on the result: `normalize_port(port, scheme)`"]
    assert deps_of(reports, "app.ports.safe_port")["result"] == ["returns the result to its own callers"]
    assert deps_of(reports, "app.ports.any_port")["result"] == ["ignores the return value"]
    assert deps_of(reports, "app.ports.all_ports")["result"] == ["passes the result to `log_port()`"]


def test_errors_caught_or_propagated(ports):
    _root, _b, an = ports
    reports = an.callers("app.ports.normalize_port")
    assert deps_of(reports, "app.ports.safe_port")["errors"] == [
        "catches `PortError` raised by it (around the call: PortError)"]
    assert deps_of(reports, "app.ports.any_port")["errors"] == [
        "catches `PortError` raised by it (around the call: Exception)"]
    assert deps_of(reports, "app.ports.build_url")["errors"] == [
        "does not catch `PortError` raised by it: the error propagates to this function's callers"]


def test_arguments_and_await(ports):
    _root, _b, an = ports
    reports = an.callers("app.ports.normalize_port")
    assert deps_of(reports, "app.ports.build_url")["arguments"] == ["passes 1 positional argument, keywords `scheme`"]
    run = deps_of(an.callers("app.ports.fetch"), "app.ports.run")
    assert run["async"] == ["awaits it", "calls it without awaiting: it receives a coroutine / promise"]


def test_production_callers_come_before_tests_and_attribute_names_are_not_variables(ports):
    _root, _b, an = ports
    reports = an.callers("app.ports.normalize_port")
    assert [r.is_test for r in reports] == sorted(r.is_test for r in reports)
    test = deps_of(reports, "tests.test_ports.test_normalize_port")
    assert test["result"] == ["stores the result in `content`, then checks `content == 8080`"]


def test_name_only_links_are_possible(tmp_path):
    files = {"m.py": "class A:\n    def close(self):\n        raise IOError('x')\n\n\nclass B:\n    def close(self):\n"
                     "        return 1\n\n\ndef shut(thing):\n    thing.close()\n"}
    root, b = build(tmp_path, files)
    reports = DependencyAnalyzer(b, repo_root=root).callers("m.A.close")
    shut = next(r for r in reports if r.caller == "m.shut")
    assert shut.certainty == POSSIBLE and all(d.certainty == POSSIBLE for d in shut.dependencies)


def test_typescript_callers(tmp_path):
    files = {"src/a.ts": "export function parse(s: string): number {\n  if (!s) { throw new RangeError('empty') }\n"
                         "  return Number(s)\n}\n\nexport function useIt(s: string) {\n  try {\n    const n = parse(s)\n"
                         "    if (n > 3) { return n.toFixed(1) }\n  } catch (e) {\n    return null\n  }\n}\n"}
    root, b = build(tmp_path, files)
    use = deps_of(DependencyAnalyzer(b, repo_root=root).callers("src.a.parse"), "src.a.useIt")
    assert use["result"] == ["stores the result in `n`, then checks `n > 3`; reads .toFixed"]
    assert use["errors"] == ["catches `RangeError` raised by it (around the call: any error (e))"]


def test_callee_dependencies(ports):
    _root, _b, an = ports
    texts = [d.text for d in an.callees("app.ports.build_url")]
    assert "`app.ports.normalize_port` can raise `PortError`; this function does not catch it" in texts
    assert '`app.ports.normalize_port` returns None when port is None or port == ""' in texts
    assert "`app.ports.normalize_port` can raise `PortError`; this function catches it" in \
        [d.text for d in an.callees("app.ports.safe_port")]


# --------------------------------------------------------------------- #
# Style and groups
# --------------------------------------------------------------------- #
STYLE = {
    "pkg/__init__.py": "",
    "pkg/errors.py": "class AppError(Exception):\n    pass\n",
    "pkg/util.py": "def clean(x):\n    return x.strip()\n",
    "pkg/api.py": '''from pkg.errors import AppError
from pkg.util import clean


def get_user(name):
    if not name:
        raise AppError("no name")
    return clean(name)


def get_group(name):
    if not name:
        raise AppError("no group")
    return clean(name)


def get_role(name):
    if not name:
        raise AppError("no role")
    return clean(name).lower()


def list_items(items):
    return [i.strip() for i in items]
''',
    "other/__init__.py": "",
    "other/use.py": "from pkg.api import get_user\n\n\ndef show(n):\n    return get_user(n)\n",
}


def test_conventions_come_with_evidence(tmp_path):
    _root, b = build(tmp_path, STYLE)
    fns = [q for q in b._def_nodes if q.startswith("pkg.api.")]
    texts = [c.text for c in conventions(b, fns, DigestBuilder(b))]
    assert "errors are raised as `AppError` (3 of 3 raises here)" in texts
    assert "`pkg.util.clean` is a shared helper here: called by 3 of 4 functions" in texts
    assert "function names are snake_case (4 of 4 multi-word names)" in texts


def test_siblings_prefer_shared_callees_and_skip_excluded(tmp_path):
    root, b = build(tmp_path, STYLE)
    groups = GroupIndex(b, root)
    found = siblings(b, "pkg.api.get_user", groups.of)
    assert [s.qname for s in found] == ["pkg.api.get_group", "pkg.api.get_role"]
    assert "shares 1 callee" in found[0].reason and "def get_group" in found[0].code
    assert [s.qname for s in siblings(b, "pkg.api.get_user", groups.of, exclude={"pkg.api.get_group"})][0] == \
        "pkg.api.get_role"


def test_group_and_codebase_summaries(tmp_path):
    root, b = build(tmp_path, STYLE)
    groups = GroupIndex(b, root)
    pkg = groups.summary("pkg")
    assert ("pkg.api.get_user", 1) in pkg.api and ("other", 1) in pkg.used_by
    assert groups.summary("other").depends_on == [("pkg", 1)]
    cb = groups.codebase()
    assert cb["functions"] == 6 and ("other", "pkg", 1) in cb["flows"]


# --------------------------------------------------------------------- #
# The package
# --------------------------------------------------------------------- #
def test_change_context_has_every_layer(ports):
    root, b, _an = ports
    ctx = ChangeContextBuilder(b, root).build("app.ports.normalize_port")
    text = render(ctx)
    for heading in ("## 1. Codebase", "## 2. Group `app`", "## 3. Anchor", "## 4. Who depends on it",
                    "## 5. What it depends on", "## 6. Style to follow", "## 7. Callers' code", "## 8. Wider impact"):
        assert heading in text
    assert "def normalize_port(port, scheme):" in text and "L14 raise PortError" in text
    assert ctx.impact["tests"] == ["tests.test_ports.test_normalize_port"]
    assert all(s.qname not in {r.caller for r in ctx.callers} for s in ctx.siblings)  # no repeats
    assert render(ChangeContextBuilder(b, root).build("app.ports.normalize_port")) == text  # deterministic


def test_budget_moves_callers_to_digests_then_counts_them(ports):
    root, b, _an = ports
    full = ChangeContextBuilder(b, root).build("app.ports.normalize_port", budget_tokens=20000)
    tight = ChangeContextBuilder(b, root).build("app.ports.normalize_port", budget_tokens=1000)
    assert len(full.caller_code) == len(full.callers) and not full.omitted
    assert len(tight.caller_code) < len(full.caller_code)
    assert len(tight.caller_code) + len(tight.caller_digests) + sum(tight.omitted.values()) == len(tight.callers)


def test_long_callers_show_the_call_site(tmp_path):
    body = "".join(f"    v{i} = {i}\n" for i in range(120))
    files = {"m.py": f"def target(x):\n    return x\n\n\ndef long_caller(x):\n{body}    y = target(x)\n    return y\n"}
    root, b = build(tmp_path, files)
    ctx = ChangeContextBuilder(b, root).build("m.target")
    code = ctx.caller_code[0][2]
    assert "y = target(x)" in code and "not shown" in code and len(code.splitlines()) < 40


def test_cli_impact(ports):
    root, _b, _an = ports
    out = CliRunner().invoke(cli_main, ["impact", "app.ports.normalize_port", "--repo", root])
    assert out.exit_code == 0, out.output
    assert out.output.startswith("# Change context: `app.ports.normalize_port`")
    miss = CliRunner().invoke(cli_main, ["impact", "normalize_port", "--repo", root])
    assert miss.exit_code != 0 and "did you mean: app.ports.normalize_port" in miss.output


def test_mcp_change_context(ports):
    from prism.mcp import server
    root, _b, _an = ports
    server._cache.clear()
    try:
        out = server.prism_change_context(repo_path=root, symbol="app.ports.normalize_port", budget_tokens=8000)
    finally:
        server._cache.clear()
    assert out["symbol"] == "app.ports.normalize_port" and "## 4. Who depends on it" in out["context"]
    assert "app.ports.build_url" in out["callers"] and out["transitive_callers"] >= len(out["callers"])
    assert out["token_count"] <= 8000 + 500
    assert CERTAIN == "certain"


# --------------------------------------------------------------------- #
# Code the reader already has open is not sent again
# --------------------------------------------------------------------- #
def test_without_visible_the_package_is_unchanged(ports):
    root, b, _an = ports
    cb = ChangeContextBuilder(b, root)
    assert render(cb.build("app.ports.normalize_port")) == render(cb.build("app.ports.normalize_port", visible={}))


def test_visible_code_becomes_a_pointer(ports):
    root, b, _an = ports
    cb = ChangeContextBuilder(b, root)
    full = cb.build("app.ports.normalize_port")
    trimmed = cb.build("app.ports.normalize_port", visible={"app/ports.py": None})
    text = render(trimmed)
    assert trimmed.anchor_in_view and "Code: already in view (app/ports.py, lines 8-15)." in text
    assert "def normalize_port(port, scheme):" not in text  # the anchor's code is not sent twice
    assert "L14 raise PortError" in text  # its digest stays
    assert "`app.ports.build_url`: already in view (app/ports.py" in text
    assert [q for q, _f, _c in trimmed.caller_code] == ["tests.test_ports.test_normalize_port"]  # not in view
    assert "## 4. Who depends on it" in text and "checks `p is None`" in text  # statements stay
    assert trimmed.token_count < full.token_count


def test_visible_ranges_only_cover_what_they_name(ports):
    root, b, _an = ports
    ctx = ChangeContextBuilder(b, root).build("app.ports.normalize_port", visible={"app/ports.py": [(8, 15)]})
    assert ctx.anchor_in_view and [q for q, _f, _l in ctx.in_view] == ["app.ports.normalize_port"]
    assert {q for q, _f, _c in ctx.caller_code} >= {"app.ports.build_url"}


def test_parse_visible():
    from prism.impact.context import parse_visible
    assert parse_visible(["a.py", "b.py:10-80", "b.py:90-99", "c:d.py"]) == {
        "a.py": None, "b.py": [(10, 80), (90, 99)], "c:d.py": None}


def test_cli_and_mcp_accept_visible(ports):
    from prism.mcp import server
    root, _b, _an = ports
    out = CliRunner().invoke(cli_main, ["impact", "app.ports.normalize_port", "--repo", root, "--visible", "app/ports.py"])
    assert out.exit_code == 0 and "already in view" in out.output
    server._cache.clear()
    try:
        res = server.prism_change_context(repo_path=root, symbol="app.ports.normalize_port", visible=["app/ports.py:8-15"])
    finally:
        server._cache.clear()
    assert "Code: already in view (app/ports.py, lines 8-15)." in res["context"]


# --------------------------------------------------------------------- #
# Map and second ring (prism.impact.overview)
# --------------------------------------------------------------------- #
class FakeOverviewLLM:
    def __init__(self):
        self.prompts = []

    def json(self, prompt, max_out, seed=42):
        self.prompts.append(prompt)
        if "Qualified Name" in prompt:
            names = [ln.split(":", 1)[1].strip() for ln in prompt.splitlines() if ln.startswith("Qualified Name:")]
            return {q: "Builds a web address from a host and an optional port" for q in names}
        return {"summary": "Turns raw port values into numbers and builds addresses."}


LAYERS = {
    "app/__init__.py": "",
    "app/ports.py": PORTS,
    "app/web/__init__.py": "",
    "app/web/views.py": "from app.ports import build_url\n\n\ndef home_link(host):\n    return build_url(host, 80)\n\n\n"
                        "def page(host):\n    return home_link(host)\n",
    "README.md": "# Toy\n\nA toy project that builds URLs.\n",
}


def test_overview_plan_picks_callers_of_callers_and_similar_functions(tmp_path):
    from prism.impact.overview import OverviewPlan, plan_overview
    root, b = build(tmp_path, LAYERS)
    cb = ChangeContextBuilder(b, root)
    ctx = cb.build("app.ports.build_url", include_tests=False)
    plan = plan_overview(cb, "app.ports.build_url", ctx)
    up = [(r.qname, r.via) for r in plan.ring if r.relation == "caller of a caller"]
    assert up == [("app.web.views.page", "app.web.views.home_link")]
    assert all("shares" in r.via or "name words" in r.via for r in plan.ring if r.relation == "similar")
    assert {r.qname for r in plan.ring}.isdisjoint({r.caller for r in ctx.callers} | {ctx.anchor})
    assert "A toy project that builds URLs." in plan.readme
    assert plan.files[0] == "app/ports.py" and "app/web/views.py" in plan.files
    assert "function build_url" in plan.outlines["app/ports.py"]
    assert OverviewPlan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan  # plain JSON round trip
    assert plan_overview(cb, "app.ports.build_url", ctx, repo_name="toy").repo == "toy"


def test_overview_similarity_needs_shared_callees_or_a_specific_name_word():
    from prism.impact.overview import _similar
    assert _similar("same directory, shares 2 callees")
    assert _similar("same directory, name words: get, size")
    assert _similar("same file, name words: terminal, same decorators: cached")
    assert not _similar("same directory, name words: get")
    assert not _similar("same class")


def test_overview_is_written_bottom_up_and_rendered(tmp_path):
    from prism.impact.overview import plan_overview, render_overview, write_overview
    root, b = build(tmp_path, LAYERS)
    cb = ChangeContextBuilder(b, root)
    plan = plan_overview(cb, "app.ports.build_url", cb.build("app.ports.build_url", include_tests=False))
    llm = FakeOverviewLLM()
    text = write_overview(plan, llm)
    assert set(text["files"]) == set(plan.outlines) and set(text["ring"]) == {r.qname for r in plan.ring}
    folder_prompts = [p for p in llm.prompts if "contains these files" in p]
    assert folder_prompts and "Turns raw port values" in folder_prompts[0]  # folders built from file sentences
    m, r = render_overview(plan, text)
    assert m.startswith("## 0. Map") and "Repository `repo`:" in m and "Files near the change:" in m
    assert r.startswith("## 9. Second ring") and "`app.web.views.page` (app/web/views.py:8), calls " \
        "`app.web.views.home_link`: Builds a web address" in r
