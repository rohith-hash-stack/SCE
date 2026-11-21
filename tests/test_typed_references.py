"""Variable-type tracking in the reference pass (Phase 2, Phase B; `harness/experiments/graph_coverage/PROGRESS.md`).

A use through a typed variable - `self.client.send` passed as a callback, `client.send` read on a module-level
instance, `c.send` on an annotated parameter, `self.cfg.retries` - names a definition the import resolver cannot
reach. The reference pass resolves these with call resolution's own resolver and instance-type maps (2b), keeping
only clean resolutions: an untyped parameter's attribute stays unlinked.
"""
import os
import subprocess

import pytest

FILES = {
    "pkg/__init__.py": "",
    "pkg/client.py": (
        "class Client:\n"
        "    def send(self, msg):\n        return msg\n\n"
        "    def close(self):\n        pass\n\n\n"
        "class Config:\n    def retries(self):\n        return 3\n"
    ),
    "pkg/service.py": (
        "from pkg.client import Client, Config\n\n"
        "default_client = Client()\n"
        "HANDLERS = [default_client.send]\n\n\n"
        "def register(callback):\n    return callback\n\n\n"
        "class Service:\n"
        "    def __init__(self):\n"
        "        self.client = Client()\n"
        "        self.cfg = Config()\n\n"
        "    def start(self):\n"
        "        register(self.client.send)\n"
        "        on_close = self.client.close\n"
        "        return on_close, self.cfg.retries\n\n\n"
        "def use_param(c: Client):\n"
        "    handler = c.send\n"
        "    return handler\n\n\n"
        "def untyped(c):\n"
        "    return c.send\n\n\n"
        "def module_instance():\n"
        "    return default_client.close\n"
    ),
}


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    root = str(tmp_path_factory.mktemp("typed") / "r")
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)), exist_ok=True)
        open(os.path.join(root, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    b, _ = build_pipeline(root, LANGUAGE_TIER_PERMISSIVE, use_cache=False)
    return b


def kinds(b, src, dst):
    g = b.reference_graph
    return set(g.edges[src, dst]["ref_kinds"]) if g.has_edge(src, dst) else set()


C, S = "pkg.client", "pkg.service"


def test_self_attribute_method_passed_as_a_callback(builder):
    assert "value" in kinds(builder, f"{S}.Service.start", f"{C}.Client.send")     # register(self.client.send)
    assert "value" in kinds(builder, f"{S}.Service.start", f"{C}.Client.close")    # on_close = self.client.close
    assert kinds(builder, f"{S}.Service.start", f"{C}.Config.retries")             # self.cfg.retries


def test_annotated_parameter(builder):
    assert "value" in kinds(builder, f"{S}.use_param", f"{C}.Client.send")


def test_module_level_instance(builder):
    assert "value" in kinds(builder, S, f"{C}.Client.send")                         # HANDLERS = [default_client.send]
    assert kinds(builder, f"{S}.module_instance", f"{C}.Client.close")


def test_untyped_parameter_stays_unlinked(builder):
    assert not kinds(builder, f"{S}.untyped", f"{C}.Client.send")


def test_main_graph_unchanged(builder):
    assert not any(d.get("relation") == "REFERENCES" for _u, _v, d in builder.graph.edges(data=True))
