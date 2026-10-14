"""The PROXY tier: module-level runtime proxies.

A module-level instance of a class that forwards attribute access
(`__getattr__` / `__getattribute__`) is a runtime proxy: what a call
through it reaches is decided at run time. Summaries record such instances
(and the names they are re-exported under); host calls through one become
PROXY boundary nodes - bits 0x0, capability unknown, scored by the triad's
action leg like OPAQUE.

- `proxylib` mimics werkzeug: `LocalProxy` forwards through an *assigned*
  `__getattr__ = _ProxyLookup(getattr)`, which the dependency's header keeps.
- `webapp` mimics flask: `request = LocalProxy(...)` in `globals.py`,
  re-exported from `__init__.py`; `config` is an ordinary instance.
- `dbkit` mimics django: its own `ConnectionProxy` forwards
  `__getattribute__`; `connection = ConnectionProxy()`.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline
from prism.deps import DependencyIndex, SummaryStore
from prism.deps.discover import find_distribution
from prism.deps.host import TIER_PROXY
from prism.deps.summarizer import summarize_package
from prism.semantics.capabilities import compute_capability_profiles
from prism.semantics.triad import DATABASE_IO, action_tokens, database_io, triad_rerank

PACKAGES = {
    "proxylib": ({
        "__init__.py": "from .local import LocalProxy\n",
        "local.py": """
            class _ProxyLookup:
                def __init__(self, f):
                    self.f = f


            class LocalProxy:
                def __init__(self, getter):
                    object.__setattr__(self, "_get", getter)

                __getattr__ = _ProxyLookup(getattr)
        """,
    }, ()),
    "webapp": ({
        "__init__.py": "from .globals import config, request\n",
        "globals.py": """
            from proxylib import LocalProxy


            class Config(dict):
                pass


            def _current_request():
                raise RuntimeError("no request")


            request = LocalProxy(_current_request)
            config = Config()
        """,
    }, ("proxylib",)),
    "dbkit": ({
        "__init__.py": """
            class ConnectionProxy:
                def __getattribute__(self, item):
                    return getattr(connections["default"], item)


            connections = {}
            connection = ConnectionProxy()
        """,
    }, ()),
}

HOST = """
import dbkit
from webapp import config, request


def read_param(name):
    return request.args.get(name)


def run_report_query(sql):
    dbkit.connection.cursor()


def read_debug_flag():
    return config.get("debug")


def format_report(rows):
    return "\\n".join(rows)
"""


def _install(site, name, files, requires=()):
    pkg = site / name
    pkg.mkdir(parents=True)
    for rel, text in files.items():
        (pkg / rel).write_text(textwrap.dedent(text))
    info = site / f"{name}-1.0.dist-info"
    info.mkdir()
    meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n" + "".join(f"Requires-Dist: {r}\n" for r in requires)
    (info / "METADATA").write_text(meta)
    (info / "top_level.txt").write_text(name + "\n")
    (info / "RECORD").write_text("\n".join(f"{name}/{rel},sha256=x,1" for rel in files) + "\n")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    site = tmp_path_factory.mktemp("site")
    for name, (files, requires) in PACKAGES.items():
        _install(site, name, files, requires)
    return site


@pytest.fixture(scope="module")
def store_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("store") / "store.db")


@pytest.fixture(scope="module")
def host(site, store_path, tmp_path_factory):
    repo = tmp_path_factory.mktemp("host")
    (repo / "app").mkdir()
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "main.py").write_text(HOST)
    builder, _ = build_pipeline(str(repo), dependencies=DependencyIndex(SummaryStore(store_path), paths=[str(site)]))
    return builder, compute_capability_profiles(builder)


def _targets(builder, fn):
    return {t for _, t in builder.graph.out_edges(f"app.main.{fn}")}


# --------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------- #
def test_dependency_proxy_class_instances_and_their_reexports(site, store_path):
    index = DependencyIndex(SummaryStore(store_path), paths=[str(site)])
    webapp = index.ensure("webapp")
    assert webapp.proxies == {"webapp.globals.request": "proxylib.local.LocalProxy",
                              "webapp.request": "proxylib.local.LocalProxy"}
    assert not any("config" in name for name in webapp.proxies)  # an ordinary instance


def test_header_keeps_assigned_forwarders_only():
    from prism.deps.headers import header_source
    header = header_source(textwrap.dedent("""
        class P:
            __getattr__ = lookup(getattr)
            __slots__ = ("a",)
            x, __getattribute__ = 1, 2
    """))
    assert "__getattr__ = ..." in header and "__slots__" not in header and "__getattribute__ = ..." not in header


def test_own_proxy_class_with_getattribute(site):
    summary = summarize_package(find_distribution("dbkit", paths=[str(site)]))
    assert summary.proxies == {"dbkit.connection": "dbkit.ConnectionProxy"}
    assert "dbkit.connections" not in summary.proxies


def test_proxies_survive_the_store(site, tmp_path):
    summary = summarize_package(find_distribution("dbkit", paths=[str(site)]))
    store = SummaryStore(str(tmp_path / "s.db"))
    store.put(summary)
    assert store.get(summary.ref).proxies == summary.proxies


# --------------------------------------------------------------------- #
# Host boundary nodes and profiles
# --------------------------------------------------------------------- #
def test_calls_through_proxies_are_proxy_boundaries(host):
    builder, profiles = host
    for fn, node, proxy in (("read_param", "webapp.request.args.get", "webapp.request"),
                            ("run_report_query", "dbkit.connection.cursor", "dbkit.connection")):
        assert node in _targets(builder, fn), fn
        attrs = builder.graph.nodes[node]
        assert attrs["boundary_tier"] == TIER_PROXY and attrs["boundary_bits"] == 0 and attrs["boundary_proxy"] == proxy
        p = profiles[f"app.main.{fn}"]
        assert p.proxy == (node,) and p.opaque == () and p.within(2) == 0  # unknown, never invented


def test_ordinary_module_level_instances_are_not_proxies(host):
    builder, profiles = host
    assert not any(builder.graph.nodes[t].get("boundary_tier") == TIER_PROXY for t in _targets(builder, "read_debug_flag"))
    assert profiles["app.main.read_debug_flag"].proxy == ()


def test_default_build_has_no_proxy_boundaries(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "main.py").write_text(HOST)
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    assert not any(a.get("boundary_proxy") for _, a in builder.graph.nodes(data=True))


# --------------------------------------------------------------------- #
# Triad: unknown, scored by the action leg
# --------------------------------------------------------------------- #
def test_proxy_is_scored_like_opaque_with_its_own_provenance(host):
    builder, profiles = host
    q = "app.main.run_report_query"
    assert database_io(profiles[q], action_tokens(builder, q)) == (0.0, "runtime-proxy")  # `cursor` is a DB verb
    candidates = ["app.main.format_report", q]
    tokens = {c: action_tokens(builder, c) for c in candidates}
    ranked = triad_rerank(candidates, {c: 1.0 for c in candidates}, profiles, tokens, target=DATABASE_IO, action="EXECUTE")
    assert ranked[0].qname == q and ranked[0].capability_source == "runtime-proxy"
    assert ranked[0].proxy == ("dbkit.connection.cursor",) and ranked[0].score == pytest.approx(1.40)
    assert ranked[1].score == 1.0
