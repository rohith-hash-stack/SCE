"""External-dependency symbol extraction (Phase C, Step 1 -
`docs/phase_c_architecture_spec.md` Section 2).

Locates a real source or stub file for an installed package on disk
(the one genuinely per-ecosystem piece - `ExternalSourceLocator`), then
reuses Prism's own existing machinery for the rest: `prism.parser.
tree_sitter_loader` to parse it (the exact same grammar dispatch every
in-repo file already goes through) and `prism.graph.contracts.
ContractExtractor.extract_symbol` to pull its signature and docstring
(the same per-language extraction every in-repo function/method
already gets). No bespoke per-language parser, no stdlib `ast` - an
earlier draft of this module proposed exactly that and was rejected
(see the spec's "Superseded design note") because nothing else in
Prism parses this way.

Only the symbol's *signature* is ever extracted or rendered - the
located file's real body is read only far enough for tree-sitter to
produce a parse tree; nothing downstream of this module ever asks for
more than `ContractExtractor.extract_symbol` returns, so there is no
separate "strip the body" pass to get wrong.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from tree_sitter import Node

from prism.graph.contracts import BehavioralContract, ContractExtractor
from prism.parser.lang_config import CLASS_NODE_TYPES, FUNCTION_NODE_TYPES
from prism.parser.tree_sitter_loader import ParsedFile, node_text, parse_file, parse_source
from prism.slicer.tokenizer import count_tokens
from prism.surface.models import NodeEntry, NodeFeatures, NodeSignature

#: `role="external"`'s fixed `NodeFeatures` - none of the four feature
#: axes (substance/form/output/role) are computed for external symbols
#: (Non-goal, Section 0: no data-flow/tagging pass runs on code Prism
#: never indexes as a repo file), matching `prism.surface.build.
#: _axis_labels`'s own "NONE" convention for an axis that contributes no
#: bit, rather than inventing a second empty-state sentinel.
_EXTERNAL_FEATURES = NodeFeatures(substance="NONE", form="NONE", output="NONE", role="NONE")


@dataclass(frozen=True)
class ExternalSymbolInfo:
    """One resolved external symbol - the output of `extract_external_symbol`,
    the input to `external_symbol_to_node_entry`. Every field here is real,
    derived from an actual parse of an actual file on disk; nothing is
    fabricated when a field can't be determined (`docstring` is `None`,
    never an invented summary)."""

    qualified_name: str  # e.g. "starlette.routing.Router.add_route"
    module_origin: str  # e.g. "starlette" - the top-level package name
    language: str  # a real prism.parser.tree_sitter_loader.LanguageID value
    signature_text: str  # rendered from ContractExtractor's own params/return_type
    docstring: str | None
    kind: str  # "function" | "method" | "class"
    file: str  # the located file's own path on disk
    line: int
    end_line: int


class ExternalSourceLocator(Protocol):
    """The one genuinely per-ecosystem piece - a thin adapter that finds
    where a package's real source or stub files live, not a parser. One
    real implementation per ecosystem Prism's tree-sitter loader already
    supports (Python here; TypeScript/Go are future implementations of
    this same Protocol, not a design change - see the spec's Rust gap
    note for the one ecosystem this can't reach at all)."""

    def locate(self, package_name: str, package_version: str | None = None) -> list[Path]: ...


class PythonSourceLocator:
    """Resolves an installed Python package's real files via `importlib.
    util.find_spec` - real files on disk, never fabricated or fetched
    over the network. Prefers `.pyi` stub files when the package (or a
    PEP 561 inline-stub distribution of it) ships any, since a stub is
    already exactly the signature-only shape Phase C wants; falls back
    to the package's own `.py` source when it ships no stubs at all
    (e.g. Starlette), which tree-sitter parses exactly as it would any
    other in-repo Python file - `ContractExtractor` never needs to know
    which case it's looking at.
    """

    def locate(self, package_name: str, package_version: str | None = None) -> list[Path]:
        top_level = package_name.split(".")[0]
        stub_only_files = self._locate_stub_only_distribution(top_level)
        if stub_only_files:
            return stub_only_files

        try:
            spec = importlib.util.find_spec(top_level)
        except (ImportError, ValueError, ModuleNotFoundError):
            return []
        if spec is None or spec.origin is None:
            return []
        origin = Path(spec.origin)
        if not spec.submodule_search_locations:
            # A single-module distribution (`spec.origin` is the module's
            # own file, not a package `__init__.py`) - nothing to search
            # under, the file itself is the whole surface. A compiled
            # extension module (`.so`/`.pyd`, e.g. `ujson` itself) has no
            # real Python source at all here - if it ships types, they
            # live in the stub-only distribution already checked above,
            # not this file.
            return [origin] if origin.suffix in (".py", ".pyi") else []
        pkg_dir = origin.parent
        stub_files = sorted(p for p in pkg_dir.rglob("*.pyi") if "__pycache__" not in p.parts)
        if stub_files:
            return stub_files
        return sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)

    @staticmethod
    def _locate_stub_only_distribution(top_level: str) -> list[Path]:
        """Real bug, found and fixed while testing the `ujson.dumps`
        receiver-based resolution path (Phase C Step 4): `ujson` itself
        installs as a single compiled extension module (`ujson.*.so`,
        no real Python source, no bundled `.pyi`) with its real type
        information shipped *separately*, as a PEP 561 stub-only
        distribution - a sibling `ujson-stubs` directory. That name is
        deliberately not a valid Python module (`import ujson-stubs` is
        a `SyntaxError`), specifically so type checkers locate it by
        walking each import root for the `<package>-stubs` directory
        itself, not through `importlib`'s ordinary module-spec
        resolution - exactly what this method does, checked before
        `locate`'s own `find_spec`-based lookup (a stub-only
        distribution is the authoritative source of types for a package
        that ships none of its own, so it takes priority when both
        exist). Returns `[]`, never raises, when no such directory is on
        `sys.path` - the ordinary, far more common case (`orjson`, e.g.,
        ships its own inline `.pyi` and has no `-stubs` sibling at all).
        """
        for root in sys.path:
            candidate = Path(root) / f"{top_level}-stubs"
            if candidate.is_dir():
                return sorted(p for p in candidate.rglob("*.pyi") if "__pycache__" not in p.parts)
        return []


def _iter_definitions(node: Node, lang: str, source: bytes, enclosing_class: str | None = None) -> Iterator[tuple[Node, str | None, str, str]]:
    """Yields `(def_node, enclosing_class, qualified_name, kind)` for
    every function/class definition reachable from `node` - a single-file
    walk against the same `CLASS_NODE_TYPES`/`FUNCTION_NODE_TYPES` tables
    `prism.graph.contracts` and `prism.graph.concrete_builder` already use,
    not a reimplementation of `ConcreteGraphBuilder`'s own multi-file
    symbol-table resolution (Section 2.1: a locator + one file's tree is
    small enough to walk directly, and Phase C's leaf-only design never
    needs cross-file symbol resolution for an external package - Non-goal,
    Section 0). A Python `@decorator`-wrapped definition is found without
    any special-casing: `decorated_definition` isn't itself a function/class
    node type, so the walk simply recurses into its children and finds the
    real `function_definition` inside, with its parent still the
    `decorated_definition` `ContractExtractor._is_async` already expects.
    """
    class_types = CLASS_NODE_TYPES.get(lang, set())
    func_types = FUNCTION_NODE_TYPES.get(lang, set())
    for child in node.children:
        if child.type in class_types:
            name_node = child.child_by_field_name("name")
            class_name = node_text(name_node, source) if name_node is not None else None
            qualified = f"{enclosing_class}.{class_name}" if enclosing_class and class_name else class_name
            if qualified:
                yield child, enclosing_class, qualified, "class"
            body = child.child_by_field_name("body")
            if body is not None:
                yield from _iter_definitions(body, lang, source, qualified or enclosing_class)
        elif child.type in func_types:
            name_node = child.child_by_field_name("name")
            func_name = node_text(name_node, source) if name_node is not None else None
            if func_name:
                qualified = f"{enclosing_class}.{func_name}" if enclosing_class else func_name
                yield child, enclosing_class, qualified, "method" if enclosing_class else "function"
        else:
            yield from _iter_definitions(child, lang, source, enclosing_class)


def _find_definition(parsed: ParsedFile, symbol_name: str) -> tuple[Node, str | None, str, str] | None:
    """First definition in `parsed` matching `symbol_name` - an exact
    match against the local qualified name (`"Router.add_route"`) when
    `symbol_name` is itself dotted, otherwise a match against the bare
    leaf name (`"Response"`) against ANY definition (top-level or
    nested) with that name. Ambiguous bare names across a large package
    (Section 2.1's own scope: leaf resolution, not full symbol
    disambiguation) should be requested in dotted form by the caller -
    the same discipline `ExternalSymbolInfo.qualified_name`'s own
    `"starlette.routing.Router.add_route"` example already models.
    """
    dotted = "." in symbol_name
    for def_node, enclosing_class, qualified, kind in _iter_definitions(parsed.root_node, parsed.language_id, parsed.source):
        if dotted:
            if qualified == symbol_name:
                return def_node, enclosing_class, qualified, kind
        elif qualified.rsplit(".", 1)[-1] == symbol_name:
            return def_node, enclosing_class, qualified, kind
    return None


def _module_name_for_file(top_level: str, file_path: Path) -> str:
    """`starlette.routing` for `.../dist-packages/starlette/routing.py`,
    `starlette` for that same package's own `__init__.py` - derived from
    the file's own path components (finds `top_level` in them and takes
    everything from there), not from a separately-plumbed package root,
    since `ExternalSourceLocator.locate` returns a flat `list[Path]`
    (Section 2.2's own Protocol shape) rather than a `(root, files)` pair.
    """
    parts = file_path.with_suffix("").parts
    if top_level in parts:
        rel_parts = parts[parts.index(top_level):]
    else:
        rel_parts = (top_level, file_path.stem)
    if rel_parts and rel_parts[-1] == "__init__":
        rel_parts = rel_parts[:-1]
    return ".".join(rel_parts) if rel_parts else top_level


def _render_signature_text(local_qualified_name: str, kind: str, contract: BehavioralContract) -> str:
    """The real declaration line, rendered from `ContractExtractor`'s own
    structured `params`/`return_type`/`is_async` - never the raw source
    text (which would drag the real body's own indentation/formatting
    along with it), and never fabricated when a field is absent (a class
    with no meaningful `__init__` signature to show renders as a bare
    `class Name:`, not a guessed constructor)."""
    simple_name = local_qualified_name.rsplit(".", 1)[-1]
    if kind == "class":
        return f"class {simple_name}:"
    prefix = "async def" if contract.is_async else "def"
    params_text = ", ".join(p.render() for p in contract.params)
    header = f"{prefix} {simple_name}({params_text})"
    if contract.return_type:
        header += f" -> {contract.return_type}"
    return header + ":"


def _parse_external_file(file_path: Path) -> ParsedFile | None:
    """Parses `file_path` via Prism's own tree-sitter loader - real bug,
    found and fixed while testing the `orjson.dumps` receiver-based
    resolution path (Phase C Step 4): `.pyi` stub files use the
    identical Python grammar `.py` files do (PEP 484 stub syntax, e.g. a
    body-less `def f(...) -> T: ...`, is valid Python syntax by
    construction, not a distinct language) - but `prism.parser.
    tree_sitter_loader.EXTENSION_LANGUAGE_MAP` has no `.pyi` entry, so
    `parse_file` silently returned `None` for every `.pyi` file, making
    `PythonSourceLocator`'s own documented ".pyi preferred" behavior
    completely non-functional for any package (like `orjson`) that ships
    only a `.pyi` stub with no real `.py` source at all.

    Not fixed by adding `.pyi` to `EXTENSION_LANGUAGE_MAP` itself - that
    map is shared with `prism.cli`'s own main repo-indexing scanner
    (`prism.traversal._cache_keys`'s file-discovery walk); adding `.pyi`
    there would make Prism's real indexing pipeline start picking up
    stub files inside a *target* repo too, a real, unintended change to
    what gets indexed, for a fix this module doesn't need to make
    globally. Instead, the grammar dispatch is forced locally, for this
    module's own external-file reads only: a `.pyi` path is read as
    bytes and parsed under the extension `parse_source` would dispatch
    to Python for. The resulting `ParsedFile.path` carries that
    substituted extension, harmlessly - neither this module nor
    `ContractExtractor` ever reads `ParsedFile.path`, only its
    `.source`/`.language_id`/`.root_node`.
    """
    if file_path.suffix == ".pyi":
        try:
            source = file_path.read_bytes()
        except OSError:
            return None
        return parse_source(str(file_path.with_suffix(".py")), source)
    return parse_file(str(file_path))


def _extract_from_file(file_path: Path, top_level: str, symbol_name: str) -> ExternalSymbolInfo | None:
    """The shared locate-result -> `ExternalSymbolInfo` step for one
    already-located file - factored out so `extract_external_symbol`
    (first match wins) and `extract_external_symbol_all` (every real
    match, Section 2.1's own bare-name-ambiguity case) share one
    parse+extract implementation rather than two independently-drifting
    copies."""
    parsed = _parse_external_file(file_path)
    if parsed is None:
        return None
    match = _find_definition(parsed, symbol_name)
    if match is None:
        return None
    def_node, enclosing_class, local_qualified_name, kind = match
    contract = ContractExtractor().extract_symbol(def_node, parsed, enclosing_class, local_qualified_name)
    module_name = _module_name_for_file(top_level, file_path)
    return ExternalSymbolInfo(
        qualified_name=f"{module_name}.{local_qualified_name}",
        module_origin=top_level,
        language=parsed.language_id,
        signature_text=_render_signature_text(local_qualified_name, kind, contract),
        docstring=contract.docstring,
        kind=kind,
        file=str(file_path),
        line=def_node.start_point[0] + 1,
        end_line=def_node.end_point[0] + 1,
    )


def extract_external_symbol(
    package_name: str, symbol_name: str, locator: ExternalSourceLocator | None = None,
) -> ExternalSymbolInfo | None:
    """Locate -> parse -> extract (Section 2.1's three steps) for one
    named external symbol - the first real match across `locator`'s own
    file ordering. Returns `None` under any real "can't resolve this"
    condition - package not installed, file not parseable by any
    grammar Prism's tree-sitter loader has, symbol name not found in any
    located file - mirroring `prism.slicer.tokenizer`'s own fail-closed
    pattern (Section 2.1): a caller that can't resolve an external
    symbol should silently fall back to its Turn-2 internal-only result,
    never raise.

    A dotted `symbol_name` (`"Router.add_route"`) already disambiguates
    a bare name that exists in more than one file (Starlette itself
    ships both `Router.add_route` and a distinct, delegating
    `Starlette.add_route`) - a *bare* name request that happens to match
    in more than one file returns only whichever one `locator.locate`
    happens to order first, which a caller that genuinely needs every
    real match (Section 1's own Turn 2a, resolving an ambiguous
    `self.<name>(...)` call with no way to know which class it means)
    should use `extract_external_symbol_all` for instead.
    """
    locator = locator or PythonSourceLocator()
    top_level = package_name.split(".")[0]
    for file_path in locator.locate(package_name):
        info = _extract_from_file(file_path, top_level, symbol_name)
        if info is not None:
            return info
    return None


def extract_external_symbol_all(
    package_name: str, symbol_name: str, locator: ExternalSourceLocator | None = None,
) -> list[ExternalSymbolInfo]:
    """Every real match for `symbol_name` across every file `locator`
    locates for `package_name`, in `locator`'s own file order - unlike
    `extract_external_symbol`, never stops at the first. Exists for the
    genuinely ambiguous bare-name case (Section 1's Turn 2a: a
    `self.<name>(...)` call whose receiver's real type isn't known,
    so which of several same-named real definitions it means isn't
    either) - offering every real candidate lets the caller's own Turn
    2b selection decide, rather than an arbitrary locator-ordering
    artifact silently deciding for it. Returns `[]` (never raises) under
    the same fail-closed conditions `extract_external_symbol` returns
    `None` for.
    """
    locator = locator or PythonSourceLocator()
    top_level = package_name.split(".")[0]
    results: list[ExternalSymbolInfo] = []
    for file_path in locator.locate(package_name):
        info = _extract_from_file(file_path, top_level, symbol_name)
        if info is not None:
            results.append(info)
    return results


def external_symbol_to_node_entry(info: ExternalSymbolInfo, distance: float = 1.0) -> NodeEntry:
    """`ExternalSymbolInfo` -> a real `role="external"` `NodeEntry`
    (schema_version 3 - Section 2.3). `body` is the signature line plus a
    `...` placeholder, the same signature-only render an in-repo
    `"L2_skeleton"` stub already uses (`_signature_stub` in
    `submodular_knapsack.py`) - never the located file's real body text,
    which is never read for anything beyond producing a parse tree.
    `compression` is always `"L2_skeleton"` and `contract` is always
    `None` (Section 2.3's own stated metadata boundary - a real, full
    body render is architecturally impossible for a symbol this system
    never AST-indexes as a repo file, not merely undesired here).
    `distance` defaults to `1.0` (a one-hop leaf resolution, Section 0's
    non-goal ruling out external-to-external chains) - the real Turn-3
    caller that resolves this against a specific in-repo call site
    (Section 1.4) is expected to pass the real computed value once that
    wiring lands; this default only matters for a caller (like this
    module's own tests) that never runs that pipeline stage.
    """
    return NodeEntry(
        id=info.qualified_name,
        role="external",
        distance=distance,
        compression="L2_skeleton",
        cost=count_tokens(info.signature_text),
        symbol_name=info.qualified_name.rsplit(".", 1)[-1],
        symbol_kind=info.kind,
        language=info.language,
        file=info.file,
        line=info.line,
        end_line=info.end_line,
        signature=NodeSignature(docstring=info.docstring),
        features=_EXTERNAL_FEATURES,
        contract=None,
        body=f"{info.signature_text}\n    ...",
    )
