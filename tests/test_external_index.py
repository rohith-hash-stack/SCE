"""Phase C, Step 1 (`docs/phase_c_architecture_spec.md` Section 2):
`prism.external.index` locates a real installed package's source on
disk, parses it through Prism's own tree-sitter loader, and extracts a
signature-only stub via `ContractExtractor.extract_symbol` - no bespoke
per-language parser of its own.

Exercised against Starlette's *real, installed* source (not a synthetic
fixture) since the whole point of this module is correctly handling a
real third-party package that ships no `.pyi` stubs at all (Section
2.1's own stated Starlette case) - `pytest.importorskip` skips cleanly
in an environment where it isn't installed rather than failing the
whole suite over an incidental dependency Prism itself doesn't declare.
"""
from __future__ import annotations

import pytest

starlette = pytest.importorskip("starlette")

from prism.external.index import (  # noqa: E402
    ExternalSymbolInfo,
    PythonSourceLocator,
    extract_external_symbol,
    external_symbol_to_node_entry,
)
from prism.surface.models import NodeEntry  # noqa: E402


# --------------------------------------------------------------------- #
# PythonSourceLocator
# --------------------------------------------------------------------- #
def test_locator_finds_starlette_py_files_when_no_stubs_shipped():
    """Starlette ships no `.pyi` stubs at all - `locate` must fall back
    to its real `.py` source (Section 2.1's own named case), not return
    empty just because the preferred stub form doesn't exist."""
    files = PythonSourceLocator().locate("starlette")

    assert files, "expected at least one located file for an installed package"
    assert all(p.suffix == ".py" for p in files)
    assert any(p.name == "routing.py" for p in files)
    assert any(p.name == "responses.py" for p in files)


def test_locator_returns_empty_for_unknown_package():
    files = PythonSourceLocator().locate("this_package_does_not_exist_anywhere_xyz")
    assert files == []


# --------------------------------------------------------------------- #
# extract_external_symbol: Router.add_route (a method, real params, no docstring)
# --------------------------------------------------------------------- #
def test_extracts_add_route_signature_without_body_bloat():
    info = extract_external_symbol("starlette", "Router.add_route")

    assert info is not None
    assert isinstance(info, ExternalSymbolInfo)
    assert info.qualified_name == "starlette.routing.Router.add_route"
    assert info.module_origin == "starlette"
    assert info.language == "python"
    assert info.kind == "method"

    # The real, non-fabricated parameter names from Starlette's own
    # source (verified directly against the installed package).
    for real_param in ("path", "endpoint", "methods", "name", "include_in_schema"):
        assert real_param in info.signature_text

    # Zero body bloat: none of `add_route`'s real body statements leak
    # into the extracted signature text.
    assert "self.routes.append" not in info.signature_text
    assert "route = Route(" not in info.signature_text


def test_add_route_node_entry_has_external_role_and_no_body_bloat():
    info = extract_external_symbol("starlette", "Router.add_route")
    assert info is not None

    node = external_symbol_to_node_entry(info)

    assert isinstance(node, NodeEntry)
    assert node.role == "external"
    assert node.compression == "L2_skeleton"
    assert node.contract is None
    assert node.id == "starlette.routing.Router.add_route"
    assert node.symbol_name == "add_route"
    assert node.symbol_kind == "method"
    assert node.language == "python"

    # The rendered body is the signature stub, not Starlette's real
    # method body - this is the whole point of Section 2.3's "zero
    # source body bloat" requirement.
    assert "self.routes.append" not in node.body
    assert "route = Route(" not in node.body
    assert "def add_route" in node.body


# --------------------------------------------------------------------- #
# extract_external_symbol: Response (a class, bare-name lookup)
# --------------------------------------------------------------------- #
def test_extracts_response_class_without_body_bloat():
    info = extract_external_symbol("starlette", "Response")

    assert info is not None
    assert info.qualified_name == "starlette.responses.Response"
    assert info.kind == "class"
    assert info.signature_text == "class Response:"

    # None of Response's real method bodies (init_headers, render, ...)
    # leak into the class-level stub.
    assert "self.status_code = status_code" not in info.signature_text
    assert "def render(self" not in info.signature_text


def test_response_node_entry_role_and_compression():
    info = extract_external_symbol("starlette", "Response")
    assert info is not None

    node = external_symbol_to_node_entry(info)

    assert node.role == "external"
    assert node.compression == "L2_skeleton"
    assert node.contract is None
    assert node.symbol_kind == "class"
    assert node.body == "class Response:\n    ..."


# --------------------------------------------------------------------- #
# Fail-closed behavior
# --------------------------------------------------------------------- #
def test_extract_external_symbol_returns_none_for_unresolvable_package():
    assert extract_external_symbol("this_package_does_not_exist_anywhere_xyz", "anything") is None


def test_extract_external_symbol_returns_none_for_unresolvable_symbol():
    assert extract_external_symbol("starlette", "this_symbol_does_not_exist_anywhere_xyz") is None
