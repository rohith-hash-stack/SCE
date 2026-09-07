"""Stage 4: AST-level compression from L0 (full source) down to L3 (alias).

Python gets exact, `ast`-driven compression (HLD section 4.3): a real
`ast.NodeTransformer` skeletonizes control flow for L1, and L2/L3 render the
interface contract straight from the parsed signature. Other supported
languages fall back to conservative, textual approximations - documented
inline - since we don't have a native AST module for them the way we do for
Python.
"""
from __future__ import annotations

import ast
import copy
import textwrap
from dataclasses import dataclass, field

from sce.parser.tree_sitter_loader import LanguageID


@dataclass
class CompressionContext:
    """Everything the compressor needs beyond the raw source text."""

    tags: set[str] = field(default_factory=set)
    callees: list[str] = field(default_factory=list)


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


def _render_contract_block(tags: set[str], raises: list[str], callees: list[str], mutates: list[str]) -> str:
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
        return "\n".join([f"{header} ...", _render_contract_block(context.tags, [], context.callees, [])])

    if resolution == 1:
        working = copy.deepcopy(node)
        skeleton = ControlFlowSkeletonizer().visit(working)
        ast.fix_missing_locations(skeleton)
        return ast.unparse(skeleton)

    signature = _stub_signature(node)
    if resolution == 3:
        return signature

    raises = _extract_raises(node)
    mutates = _extract_mutates(node) if "#state_mutation" in context.tags else []
    return "\n".join([signature, _render_contract_block(context.tags, raises, context.callees, mutates)])


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


class ASTCompressor:
    """Language-dispatching entry point used by the knapsack packer."""

    def compress(self, language_id: str, source: str, name: str, line_range: tuple[int, int], resolution: int, context: CompressionContext | None = None) -> str:
        context = context or CompressionContext()
        resolution = max(0, min(resolution, 3))
        if language_id == LanguageID.PYTHON:
            return compress_python(source, name, line_range, resolution, context)
        return compress_generic(source, line_range, resolution, context)
