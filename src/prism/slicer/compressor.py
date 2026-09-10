"""Stage 4: AST-level compression from L0 (full source) down to L3 (alias).

Python gets exact, `ast`-driven compression (HLD section 4.3): a real
`ast.NodeTransformer` skeletonizes control flow for L1, and L2/L3 render the
interface contract straight from the parsed signature. Every other language
`UniversalSlicer` supports (JS/TS/Go/Java/C#) routes through its own
CST-based byte-range pruning instead - the same precision as the Python
path, just derived from Tree-sitter's CST rather than a language-specific
AST module. A language with neither (or when the caller has no `def_node`
handy, e.g. an L0 raw-slice request, which needs none) falls back to
`compress_generic`'s conservative textual approximation.
"""
from __future__ import annotations

import ast
import copy
import textwrap
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from tree_sitter import Node

from prism.parser.tree_sitter_loader import LanguageID


#: `PackedItem.resolution`'s sentinel for a "compact infallible-leaf
#: dependency signature" - not a normal L0-L3 compression level at all.
#: Negative so it can never collide with a real resolution (0-3) or be
#: reached by `ASTCompressor.compress`'s own `max(0, min(resolution, 3))`
#: clamping, and so any code that still assumes "resolution is always
#: 0-3" fails loudly (an IndexError/KeyError) instead of silently
#: mis-rendering. See `render_infallible_signature` and
#: `prism.slicer.knapsack.ContextKnapsackPacker`'s fallibility-based
#: pruning (spec: "Fallibility-Based Knapsack Pruning").
INFALLIBLE_SIGNATURE_RESOLUTION = -1

#: `PackedItem.resolution` sentinels for the two Task-1/Task-2 sentinel
#: node kinds - `UnresolvedPolymorphicNode` and `DynamicEdgeSentinel` (see
#: `prism.graph.symbol_table`/`prism.graph.call_site`). Distinct negative
#: values from `INFALLIBLE_SIGNATURE_RESOLUTION` and from each other so a
#: reader can always tell which kind of non-source item it's looking at;
#: both are exempt from normal L0-L3 tier demotion in the same way an
#: infallible-leaf signature is (see
#: `prism.slicer.knapsack.ContextKnapsackPacker.pack`'s sentinel branch).
UNRESOLVED_POLYMORPHIC_RESOLUTION = -2
DYNAMIC_EDGE_SENTINEL_RESOLUTION = -3

#: `#io_boundary` is this benchmark-optimization spec's own name for the
#: role tag; the existing tagging engine's real equivalent boundary tags
#: (`#external_io`, `#db_write`, `#db_read`) are included too so this
#: reads real tag data rather than a tag name that doesn't otherwise exist
#: anywhere in the tagger's vocabulary. `DYNAMIC_HAZARD_TAG` (Task 2) is
#: included for the same "belt-and-suspenders" reason `is_infallible`'s own
#: docstring already gives for the others - purity is already forced
#: `"impure"` for any function carrying it (see
#: `prism.graph.contracts.ContractExtractor.extract_symbol`), so this is
#: defense in depth, not the only thing enforcing it.
_IO_BOUNDARY_TAGS = frozenset({"#io_boundary", "#external_io", "#db_write", "#db_read", "#dynamic_hazard"})

#: Complexity threshold above which a node is Fallible regardless of any
#: other signal - the spec's own explicit "cyclomatic complexity >= 3"
#: bullet, applied independently from (not merged into) the Infallible
#: test below (a node can fail the complexity>=3 Fallible test yet still
#: not qualify as Infallible for an entirely different reason - impure,
#: say - `is_infallible` alone is the authoritative single predicate this
#: module actually acts on).
FALLIBLE_COMPLEXITY_THRESHOLD = 3
INFALLIBLE_MAX_COMPLEXITY = 2


def is_infallible(contract, tags: set[str] | None = None) -> bool:
    """A node is Infallible if and only if it is provably pure, simple,
    and boundary-free - purity == "pure", cyclomatic_complexity <= 2, no
    thrown exceptions, and no recorded effects (`BehavioralContract.effects`
    - IO/DOM/mutation/assert sinks). `tags` additionally excludes anything
    carrying an IO-boundary role tag even if its contract otherwise looks
    pure (belt-and-suspenders against a contract-extraction gap, matching
    the spec's own "or #io_boundary roles" Fallible criterion). `None` (no
    contract at all - an external/unresolved symbol) is never Infallible:
    the whole point is a *proven* absence of failure modes, and "unknown"
    is not proof.
    """
    if contract is None:
        return False
    if tags and (tags & _IO_BOUNDARY_TAGS):
        return False
    return (
        contract.purity == "pure"
        and contract.cyclomatic_complexity <= INFALLIBLE_MAX_COMPLEXITY
        and not contract.thrown_exceptions
        and not contract.effects
    )


def render_infallible_signature(name: str, return_type: str | None) -> str:
    """The compact dependency-signature line an Infallible leaf gets
    instead of its full AST source - see `is_infallible`. Deliberately a
    single short line (no code fence, no per-field contract block): the
    entire point of this pruning is to spend as few tokens as possible on
    a node already proven to have nothing that could go wrong."""
    type_repr = return_type if return_type else "None"
    return f"- callee: {name} [infallible_pure_leaf, returns: {type_repr}]"


def render_unresolved_polymorphic(identifier: str, line: int, candidates: list[tuple[str, set[str]]]) -> str:
    """The compact diagnostic an `UnresolvedPolymorphicNode` renders as
    (Task 3.2) instead of ever going through normal L0-L3 tier demotion -
    a single short warning block naming every candidate the scorer
    considered and couldn't clear `POLYSEMY_THRESHOLD` on, plus each
    candidate's own tags (so a reader can immediately see, e.g., that one
    candidate is `#state_mutation` and another is not, without a second
    lookup)."""
    lines = [f"[!] AMBIGUOUS CALL at line {line}: '{identifier}'", "    Candidates:"]
    for name, tags in candidates:
        tag_repr = ", ".join(sorted(tags)) if tags else "no tags"
        lines.append(f"    - {name} ({tag_repr})")
    return "\n".join(lines)


def render_dynamic_edge_sentinel(expr: str, line: int, hazard_type: str, target_object: str | None) -> str:
    """The compact diagnostic a `DynamicEdgeSentinel` renders as (Task
    3.2) - a runtime-only dispatch target that this static pass cannot
    resolve, and whose blast radius therefore terminates right here."""
    target = target_object if target_object else "unknown"
    return (
        f"[!] DYNAMIC BOUNDARY at line {line}: '{expr}'\n"
        f"    Hazard: {hazard_type} (Target: {target})\n"
        f"    Blast radius stops at this boundary."
    )


@dataclass
class CompressionContext:
    """Everything the compressor needs beyond the raw source text."""

    tags: set[str] = field(default_factory=set)
    callees: list[str] = field(default_factory=list)
    # Adaptive Compact Scaffolding (see `ContextKnapsackPacker.pack`'s
    # small-context detection): when True, render an L2 contract as one
    # dense line instead of one line per field. Every resolution level's
    # *content* stays otherwise identical - this only changes how verbosely
    # the contract metadata is spelled out.
    compact: bool = False


def _raw_slice(source: str, line_range: tuple[int, int]) -> str:
    lines = source.splitlines()
    start, end = line_range
    start = max(start, 1)
    end = min(end, len(lines))
    raw = "\n".join(lines[start - 1 : end])
    # A method's line range is a literal slice of the file, so it carries
    # its original class-body indentation (e.g. 4 spaces). L1-L3 render from
    # a freshly unparsed AST and are always flush to column 0; dedenting L0
    # too keeps every resolution level independently valid, standalone
    # Python rather than only being parseable in its original file context.
    return textwrap.dedent(raw)


# ---------------------------------------------------------------------- #
# Python: exact ast.NodeTransformer compression
# ---------------------------------------------------------------------- #
class ControlFlowSkeletonizer(ast.NodeTransformer):
    """Transforms a function AST into a Level-1 Control Flow Skeleton.

    Retains: parameter list & return annotation, if/else guards, external
    call expressions (with arguments collapsed to `...`), raise statements,
    return statements, and try/except/for/while scaffolding (handled by the
    default `generic_visit` recursion into their body/orelse/handlers).

    Strips: intermediate variable math, logging/print/metric calls, and
    call arguments (replaced with a single `...` placeholder to preserve the
    call's shape without its data).
    """

    LOG_TOKENS = ("log", "print", "metric")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._skeletonize(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._skeletonize(node)

    def _skeletonize(self, node):
        new_body = []
        for stmt in node.body:
            transformed = self.visit(stmt)
            if transformed is None:
                continue
            if isinstance(transformed, list):
                new_body.extend(t for t in transformed if t is not None)
            else:
                new_body.append(transformed)
        node.body = new_body or [ast.Pass()]
        node.decorator_list = []
        return node

    @staticmethod
    def _strip_call_args(call_node: ast.Call) -> None:
        if call_node.args or call_node.keywords:
            call_node.args = [ast.Constant(value=Ellipsis)]
            call_node.keywords = []

    def visit_Assign(self, node: ast.Assign):
        # Keep an assignment only when its RHS is a call (external effect /
        # constructor); drop pure intermediate math/formatting.
        if isinstance(node.value, ast.Call):
            self._strip_call_args(node.value)
            return node
        return None

    def visit_Expr(self, node: ast.Expr):
        if isinstance(node.value, ast.Call):
            func_name = ast.unparse(node.value.func).lower()
            if any(token in func_name for token in self.LOG_TOKENS):
                return None
            self._strip_call_args(node.value)
            return node
        return None

    def visit_If(self, node: ast.If):
        self.generic_visit(node)
        if not node.body:
            node.body = [ast.Pass()]
        return node

    # For/While/With/Try all require a non-empty `body` per the grammar;
    # stripping every statement inside one (e.g. a loop that only did
    # intermediate math) must leave a `pass`, not an invalid empty suite.
    def visit_For(self, node: ast.For):
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        return node

    def visit_AsyncFor(self, node: ast.AsyncFor):
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        return node

    def visit_While(self, node: ast.While):
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        return node

    def visit_With(self, node: ast.With):
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        return node

    def visit_AsyncWith(self, node: ast.AsyncWith):
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        return node

    def visit_Try(self, node: ast.Try):
        self.generic_visit(node)
        node.body = node.body or [ast.Pass()]
        for handler in node.handlers:
            handler.body = handler.body or [ast.Pass()]
        return node

    def visit_Raise(self, node: ast.Raise):
        if isinstance(node.exc, ast.Call):
            self._strip_call_args(node.exc)
        return node

    def visit_Return(self, node: ast.Return):
        if isinstance(node.value, ast.Call):
            self._strip_call_args(node.value)
        return node


class ArgPreservingSkeletonizer(ControlFlowSkeletonizer):
    """Level 1 (Pruned) of the 4-tier compression model (Issue #10):
    identical control-flow/logging/docstring pruning to
    `ControlFlowSkeletonizer`, but never collapses a call's real
    arguments to a bare `...`.

    `ControlFlowSkeletonizer` (renamed Level 2 - Skeleton, below) erasing
    *every* call argument uniformly - loop bounds, off-by-one-prone index
    arithmetic, retry counts, status codes, literal flags - was a real,
    measured failure mode for LLM debugging agents: a 1-2 hop dependency
    close enough to matter for root-causing a bug still had its actual
    invocation data hidden, indistinguishable from a distant, irrelevant
    one. Only the argument-stripping behavior is overridden; every other
    pruning rule (docstrings, `LOG_TOKENS`-matched logging/print/metric
    calls, empty-suite `pass` synthesis) is inherited unchanged.
    """

    @staticmethod
    def _strip_call_args(call_node: ast.Call) -> None:
        return None  # no-op: arguments are preserved at this tier


def locate_python_definition(tree: ast.Module, name: str, line_range: tuple[int, int]) -> ast.AST | None:
    """Find the FunctionDef/AsyncFunctionDef/ClassDef matching `name` whose
    header line falls inside the tree-sitter-derived `line_range`.

    Matching by (name, line-containment) rather than walking a scope path
    keeps this robust to decorators (whose line tree-sitter includes in the
    range but Python's own `node.lineno` does not).
    """
    start, end = line_range
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            if start <= node.lineno <= end:
                return node
    return None


def _stub_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    stub_cls = ast.AsyncFunctionDef if isinstance(node, ast.AsyncFunctionDef) else ast.FunctionDef
    stub = stub_cls(
        name=node.name,
        args=copy.deepcopy(node.args),
        body=[ast.Expr(value=ast.Constant(value=Ellipsis))],
        decorator_list=[],
        returns=copy.deepcopy(node.returns) if node.returns else None,
        type_comment=None,
    )
    ast.fix_missing_locations(stub)
    text = ast.unparse(stub)
    header, _, _ = text.partition("\n")
    return f"{header} ..."


def _extract_raises(node: ast.AST) -> list[str]:
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Raise) and child.exc is not None:
            exc = child.exc
            name = ast.unparse(exc.func) if isinstance(exc, ast.Call) else ast.unparse(exc)
            if name not in names:
                names.append(name)
    return names


def _extract_mutates(node: ast.AST) -> list[str]:
    mutated: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            for target in child.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    label = f"self.{target.attr}"
                    if label not in mutated:
                        mutated.append(label)
    return mutated


def _render_contract_block(tags: set[str], raises: list[str], callees: list[str], mutates: list[str], compact: bool = False) -> str:
    if compact:
        # Adaptive Compact Scaffolding: one dense line instead of one line
        # per field - the same information, worth the extra token cost of
        # multi-line formatting only when the surrounding context is large
        # enough that a few saved tokens per item don't matter.
        parts = [f"tags=[{', '.join(sorted(tags))}]"]
        if mutates:
            parts.append(f"mutates=[{', '.join(mutates)}]")
        if raises:
            parts.append(f"raises=[{', '.join(raises)}]")
        if callees:
            parts.append(f"calls=[{', '.join(callees)}]")
        return "# " + " ".join(parts)

    lines = [f"# Tags: [{', '.join(sorted(tags))}]"]
    if mutates:
        lines.append(f"# Mutates: {', '.join(mutates)}")
    if raises:
        lines.append(f"# Raises: {', '.join(raises)}")
    if callees:
        lines.append(f"# Calls: {', '.join(callees)}")
    return "\n".join(lines)


def compress_python(source: str, name: str, line_range: tuple[int, int], resolution: int, context: CompressionContext) -> str:
    if resolution == 0:
        return _raw_slice(source, line_range)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # The file uses syntax this interpreter's `ast` module can't parse -
        # e.g. a PEP 695 `type` alias statement (Python 3.12+ grammar),
        # confirmed against a real file in django/django while indexing it
        # under Python 3.11. tree-sitter's more tolerant, version-agnostic
        # grammar already indexed this file fine at the graph-building
        # stage; only this stdlib-`ast`-based skeletonization step can't
        # re-parse it. Degrade to the same raw-slice fallback already used
        # just below when a definition can't be relocated post-parse,
        # rather than crashing the whole pack.
        return _raw_slice(source, line_range)
    except RecursionError:
        # Issue C2 (security audit): CPython's own parser already rejects
        # some pathologically deep shapes with a clean `SyntaxError`
        # (excess parenthesis/indentation nesting - both already covered
        # above), but not all of them - a long chain of unary operators
        # (`not not not ... True`) parses past those checks and then
        # blows the interpreter's call stack while `ast.parse` builds the
        # tree, confirmed directly (`RecursionError('maximum recursion
        # depth exceeded during ast construction')` on a 3000-deep chain).
        # Prism indexes arbitrary, potentially adversarial repositories,
        # so a single such file must degrade this one compression call,
        # not crash the whole `prism query`/`index` run - the same
        # contract `SyntaxError` above already gets.
        return _raw_slice(source, line_range)
    node = locate_python_definition(tree, name, line_range)
    if node is None:
        # Definition couldn't be relocated (e.g. syntax quirk) - degrade to raw slice.
        return _raw_slice(source, line_range)

    if isinstance(node, ast.ClassDef):
        # Classes don't skeletonize meaningfully below L0; expose their
        # signature line only for L1-L3.
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        header = f"class {node.name}({bases}):" if bases else f"class {node.name}:"
        if resolution == 3:
            return f"{header} ..."
        return "\n".join([f"{header} ...", _render_contract_block(context.tags, [], context.callees, [], context.compact)])

    # Issue C2 (security audit): `copy.deepcopy`, `NodeTransformer.visit`,
    # and `ast.unparse` are all pure-Python recursive walks - a
    # meaningfully *shallower* AST than the one that can defeat
    # `ast.parse` itself already crashes here (confirmed directly: a
    # 400-deep `not` chain parses fine but blows the stack in `.visit()`,
    # well below the ~3000 depth needed to make `ast.parse` itself raise).
    # Both tiers below share the same degrade-don't-crash contract as the
    # `ast.parse`/`SyntaxError` handling above.
    if resolution == 1:
        # Level 1 (Pruned): control flow, call expressions *with* their
        # real arguments, and assignments retained; docstrings/logging
        # stripped. See `ArgPreservingSkeletonizer`'s own docstring for
        # why this must never fall back to the arg-stripped Level 2 shape
        # (Issue #10).
        try:
            working = copy.deepcopy(node)
            skeleton = ArgPreservingSkeletonizer().visit(working)
            ast.fix_missing_locations(skeleton)
            return ast.unparse(skeleton)
        except RecursionError:
            return _raw_slice(source, line_range)

    if resolution == 2:
        # Level 2 (Skeleton): signature + control-flow boundaries only;
        # call arguments collapsed to `...`. This is what Level 1 itself
        # rendered before Issue #10 - moved here, one tier further out,
        # rather than changed in place, so a 1-2 hop dependency close
        # enough to matter for root-causing a bug still shows its real
        # invocation data (now at Level 1) while a 3+ hop one keeps
        # paying only for the control-flow *shape*, not real values.
        try:
            working = copy.deepcopy(node)
            skeleton = ControlFlowSkeletonizer().visit(working)
            ast.fix_missing_locations(skeleton)
            body = ast.unparse(skeleton)
        except RecursionError:
            return _raw_slice(source, line_range)
        # A `BehavioralContract` (when available) still fully supersedes
        # this at the serializer level (`markdown._CONTRACT_RESOLUTIONS`);
        # the tags/raises/calls annotation is appended here as trailing
        # comments purely for the no-contract fallback path (a language
        # without full contract support, or a caller that never computed
        # one), so that information isn't lost outright in that case.
        raises = _extract_raises(node)
        mutates = _extract_mutates(node) if "#state_mutation" in context.tags else []
        annotation = _render_contract_block(context.tags, raises, context.callees, mutates, context.compact)
        return f"{body}\n{annotation}"

    return _stub_signature(node)  # resolution == 3


# ---------------------------------------------------------------------- #
# Non-Python languages: conservative textual fallback
# ---------------------------------------------------------------------- #
def compress_generic(source: str, line_range: tuple[int, int], resolution: int, context: CompressionContext) -> str:
    raw = _raw_slice(source, line_range)
    if resolution == 0:
        return raw

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    signature_line = lines[0].rstrip("{ ").rstrip(":") if lines else ""

    if resolution == 3:
        return f"{signature_line} ..." if signature_line else raw

    if resolution == 2:
        block = [f"{signature_line} ..." if signature_line else raw, f"# Tags: [{', '.join(sorted(context.tags))}]"]
        if context.callees:
            block.append(f"# Calls: {', '.join(context.callees)}")
        return "\n".join(block)

    # L1 fallback: drop obviously non-structural lines (blank-ish logging).
    noisy_tokens = ("console.log", "logger.", "log.", "fmt.Println", "System.out")
    kept = [ln for ln in lines if not any(tok in ln for tok in noisy_tokens)]
    return "\n".join(kept) if kept else raw


def compress_universal(source: str, def_node: Node, language_id: str, resolution: int, context: CompressionContext) -> str:
    """L1/L2/L3 via `UniversalSlicer`'s CST byte-range pruning - the same
    approach `compress_python` uses, generalized to every language that
    doesn't have a native `ast` module. Re-encodes `source` (the decoded
    file text every other compressor entry point already takes) back to
    bytes, since `def_node`'s byte offsets were computed against the
    original file bytes; safe for any file that decoded losslessly to
    begin with (`errors="replace"` in the initial decode - a genuinely
    invalid-UTF-8 source file - is the one case this round-trip can't
    recover, an existing, pre-existing-elsewhere degradation, not a new
    one this introduces).
    """
    from prism.parser.lang_config import CLASS_NODE_TYPES
    from prism.slicer.universal_slicer import UniversalSlicer

    slicer = UniversalSlicer()
    source_bytes = source.encode("utf-8")
    # Classes don't skeletonize meaningfully below L0 (mirrors
    # compress_python's own `ast.ClassDef` special case exactly - L1 and L2
    # both render the same header+contract shape for a class; only L3 drops
    # the metadata block).
    is_class = def_node.type in CLASS_NODE_TYPES.get(language_id, frozenset())
    if resolution == 3:
        # Signature only, no contract metadata block - matches
        # compress_python's own L3 (`_stub_signature`, no tags/raises/calls).
        # `signature_line` (not `.splitlines()[0]` on the full L2 contract)
        # since an annotated/attributed header can itself span multiple
        # source lines (`@Override\npublic String toString()`) - taking
        # only the first line silently truncated it to just `@Override`,
        # a genuine syntax error caught by a live spring-petclinic run.
        return slicer.signature_line(source_bytes, def_node, language_id)
    if resolution == 1 and not is_class:
        return slicer.skeletonize(source_bytes, def_node, language_id)
    return slicer.extract_contract(source_bytes, def_node, language_id, tags=context.tags, callees=context.callees)


@runtime_checkable
class CompressionProvider(Protocol):
    """Standardized compression-engine interface (Issues #1/#2/#3): every
    AST/CST compression driver this codebase has - the native-`ast`-based
    Python path (`compress_python`) and the Tree-sitter-CST-based
    `UniversalSlicer` path other languages route through
    (`compress_universal`/`compress_generic`) - already converges on this
    exact single-method shape via `ASTCompressor.compress` below; this
    `Protocol` makes that convergence an explicit, checkable contract
    instead of an implicit one, so a caller (or a future third
    compression backend) can depend on `CompressionProvider` rather than
    on `ASTCompressor` specifically. `ASTCompressor` satisfies this
    structurally (a `Protocol` needs no explicit inheritance) - see
    `test_language_tiers.py`'s `isinstance(ASTCompressor(),
    CompressionProvider)` check.
    """

    def compress(
        self,
        language_id: str,
        source: str,
        name: str,
        line_range: tuple[int, int],
        resolution: int,
        context: CompressionContext | None = None,
        def_node: Node | None = None,
    ) -> str: ...


class ASTCompressor:
    """Language-dispatching entry point used by the knapsack packer -
    implements `CompressionProvider` (see that Protocol's own docstring)."""

    def compress(
        self,
        language_id: str,
        source: str,
        name: str,
        line_range: tuple[int, int],
        resolution: int,
        context: CompressionContext | None = None,
        def_node: Node | None = None,
    ) -> str:
        context = context or CompressionContext()
        resolution = max(0, min(resolution, 3))
        if language_id == LanguageID.PYTHON:
            return compress_python(source, name, line_range, resolution, context)
        if resolution != 0 and def_node is not None:
            from prism.slicer.universal_slicer import UniversalSlicer

            if language_id in UniversalSlicer.SUPPORTED_LANGUAGES:
                return compress_universal(source, def_node, language_id, resolution, context)
        return compress_generic(source, line_range, resolution, context)
