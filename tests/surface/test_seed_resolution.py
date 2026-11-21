"""Seed resolution in the MCP surface (`prism.mcp.server._resolve_seed_or_raise`):
exact qualified names, bare and partially qualified names that exactly one
symbol matches, names several symbols match (never guessed; truncation
explicit), and unknown names - through the real `prism.slice`,
`prism.explain` and `prism.blast_radius` tools."""
from __future__ import annotations

import re

import pytest
from mcp.shared.exceptions import MCPError

_FILES = {
    "router.py": (
        "def helper(x):\n    return x + 1\n\n\n"
        "class Router:\n"
        "    def include_router(self, other):\n        return helper(other)\n\n"
        "    def mount(self, other):\n        return self.include_router(other)\n"
    ),
    "jobs_a.py": "def execute(job):\n    return job\n",
    "jobs_b.py": "def execute(job):\n    return job * 2\n",
    "svc_a.py": "class Worker:\n    def start(self):\n        return 1\n",
    "svc_b.py": "class Worker:\n    def start(self):\n        return 2\n",
}


@pytest.fixture
def repo(tmp_path):
    from prism.mcp import server
    for name, text in _FILES.items():
        (tmp_path / name).write_text(text)
    server._cache.clear()
    yield str(tmp_path), server
    server._cache.clear()


def _seed_of(envelope: str) -> str:
    return re.search(r'<seed [^>]*symbol="([^"]+)"', envelope).group(1)


def test_exact_qualified_name_is_unchanged(repo):
    path, server = repo
    out = server.prism_slice(repo_path=path, seed_symbol="router.Router.include_router")
    assert _seed_of(out["envelope"]) == "router.Router.include_router"
    blast = server.prism_blast_radius(repo_path=path, seed_symbol="router.Router.include_router")
    assert blast["seed"] == "router.Router.include_router"


@pytest.mark.parametrize("tool", ["prism_slice", "prism_explain", "prism_blast_radius"])
def test_unique_bare_name_resolves_to_its_qualified_name(repo, tool):
    path, server = repo
    out = getattr(server, tool)(repo_path=path, seed_symbol="include_router")
    assert _seed_of(out["envelope"]) == "router.Router.include_router"
    if tool == "prism_blast_radius":
        assert out["seed"] == "router.Router.include_router"
        assert [c["symbol"] for c in out["callers"]] == ["router.Router.mount"]


def test_bare_name_resolution_matches_the_qualified_call(repo):
    path, server = repo
    bare = server.prism_slice(repo_path=path, seed_symbol="include_router")
    qualified = server.prism_slice(repo_path=path, seed_symbol="router.Router.include_router")
    assert bare["envelope"] == qualified["envelope"]


@pytest.mark.parametrize("tool", ["prism_slice", "prism_explain", "prism_blast_radius"])
def test_ambiguous_bare_name_is_never_guessed(repo, tool):
    path, server = repo
    with pytest.raises(MCPError) as exc:
        getattr(server, tool)(repo_path=path, seed_symbol="execute")
    err = exc.value.error
    assert err.code == -32002 and "ambiguous" in err.message
    assert err.data == {"ambiguous": True, "match_count": 2, "truncated": False,
                        "candidates": ["jobs_a.execute", "jobs_b.execute"]}


def test_ambiguity_error_is_deterministic(repo):
    path, server = repo
    errors = []
    for _ in range(2):
        with pytest.raises(MCPError) as exc:
            server.prism_slice(repo_path=path, seed_symbol="execute")
        errors.append((exc.value.error.message, exc.value.error.data))
    assert errors[0] == errors[1]


def test_unknown_name_keeps_the_not_found_contract(repo):
    path, server = repo
    with pytest.raises(MCPError) as exc:
        server.prism_slice(repo_path=path, seed_symbol="router.Router.include_routerr")
    err = exc.value.error
    assert err.code == -32002 and "was not found" in err.message
    # unchanged "did you mean": difflib over every indexed qualified name
    import difflib
    names = [s.qualified_name for s in server._repo_context_for_surface(path).symbol_table]
    assert err.data == {"candidates": difflib.get_close_matches("router.Router.include_routerr", names, n=5, cutoff=0.5)}
    assert err.data["candidates"][0] == "router.Router.include_router"
    with pytest.raises(MCPError) as exc:
        server.prism_blast_radius(repo_path=path, seed_symbol="no_such_symbol_anywhere")
    assert exc.value.error.code == -32002 and "ambiguous" not in exc.value.error.message


# ---- partially qualified names -------------------------------------------
@pytest.mark.parametrize("tool", ["prism_slice", "prism_explain", "prism_blast_radius"])
def test_unique_partially_qualified_name_resolves(repo, tool):
    path, server = repo
    out = getattr(server, tool)(repo_path=path, seed_symbol="Router.include_router")
    assert _seed_of(out["envelope"]) == "router.Router.include_router"
    qualified = getattr(server, tool)(repo_path=path, seed_symbol="router.Router.include_router")
    assert out["envelope"] == qualified["envelope"]


def test_ambiguous_partially_qualified_name_is_never_guessed(repo):
    path, server = repo
    with pytest.raises(MCPError) as exc:
        server.prism_blast_radius(repo_path=path, seed_symbol="Worker.start")
    assert exc.value.error.code == -32002
    assert exc.value.error.data == {"ambiguous": True, "match_count": 2, "truncated": False,
                                    "candidates": ["svc_a.Worker.start", "svc_b.Worker.start"]}
    # a longer suffix disambiguates
    out = server.prism_blast_radius(repo_path=path, seed_symbol="svc_b.Worker.start")
    assert out["seed"] == "svc_b.Worker.start"


@pytest.mark.parametrize("name", ["ter.include_router", "outer.include_router", "Router.include", ".include_router",
                                  "Router..include_router", "include_router."])
def test_partial_names_match_whole_segments_only(repo, name):
    path, server = repo
    with pytest.raises(MCPError) as exc:
        server.prism_slice(repo_path=path, seed_symbol=name)
    assert exc.value.error.code == -32002 and "was not found" in exc.value.error.message


# ---- explicit truncation ---------------------------------------------------
def test_ambiguity_truncation_is_explicit(tmp_path):
    from prism.mcp import server
    for i in range(22):
        (tmp_path / f"m{i:02d}.py").write_text("def run(x):\n    return x\n")
    server._cache.clear()
    try:
        with pytest.raises(MCPError) as exc:
            server.prism_slice(repo_path=str(tmp_path), seed_symbol="run")
    finally:
        server._cache.clear()
    data = exc.value.error.data
    assert data["ambiguous"] is True and data["match_count"] == 22 and data["truncated"] is True
    assert data["candidates"] == [f"m{i:02d}.run" for i in range(20)]           # sorted, first 20


# ---- resolver helper and the miss path ------------------------------------
def test_qualified_name_matches_generalises_bare_name_matching(repo):
    from prism.query.locate import locate_symbol_by_name, qualified_name_matches
    path, server = repo
    b = server._repo_context_for_surface(path).builder
    for name in ("include_router", "execute", "start", "helper", "Worker", "nope"):
        leaf = sorted(s.qualified_name for s in b.symbol_table if s.qualified_name.rsplit(".", 1)[-1] == name)
        assert qualified_name_matches(b, name) == leaf                      # bare names: the old leaf match
    assert qualified_name_matches(b, "Router.include_router") == ["router.Router.include_router"]
    assert locate_symbol_by_name(b, "Router.include_router") == "router.Router.include_router"
    assert qualified_name_matches(b, "") == [] and qualified_name_matches(b, "a..b") == []


def test_miss_path_no_longer_runs_the_discarded_fuzzy_search(repo, monkeypatch):
    import prism.packer.submodular_knapsack as knapsack
    import prism.query.locate as locate
    path, server = repo
    calls = []
    real = knapsack.suggest_similar_seeds
    monkeypatch.setattr(locate, "suggest_similar_seeds", lambda *a, **k: calls.append(1) or real(*a, **k))
    with pytest.raises(MCPError) as exc:
        server.prism_slice(repo_path=path, seed_symbol="router.Router.include_routerr")
    assert calls == []                                                       # the MCP miss path is difflib only
    import difflib
    names = [s.qualified_name for s in server._repo_context_for_surface(path).symbol_table]
    assert exc.value.error.data == {"candidates": difflib.get_close_matches("router.Router.include_routerr", names,
                                                                            n=5, cutoff=0.5)}
