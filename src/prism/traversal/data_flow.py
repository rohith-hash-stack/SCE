"""AST data-flow graph synthesis for async/await bindings and container subscripts."""
import ast
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator


class EdgeKind(str, Enum):
    DATA_FLOW = "data_flow"


@dataclass(frozen=True)
class SymbolRef:
    name: str
    file_path: str


@dataclass
class FlowGraph:
    edges: set[tuple[SymbolRef, SymbolRef, EdgeKind]] = field(default_factory=set)

    def add_edge(self, src: SymbolRef, dst: SymbolRef, kind: EdgeKind) -> None:
        self.edges.add((src, dst, kind))


def extract_arg_identifiers(call_node: ast.Call) -> Iterator[str]:
    """Extract local variable identifiers passed as arguments.

    Invariant: only bare ast.Name nodes count as local variables. Attribute
    accesses (self.client) yield nothing — receiver pollution is prevented.
    """
    for arg in call_node.args:
        if isinstance(arg, ast.Name):
            yield arg.id

    for kw in call_node.keywords:
        if isinstance(kw.value, ast.Name):
            yield kw.value.id


def process_async_assignment(
    targets: list[ast.expr],
    rhs_call: ast.Call,
    producer: SymbolRef,
    env: dict[str, SymbolRef],
    graph: FlowGraph,
) -> None:
    """Connect upstream producers to current producer and bind LHS targets.

    Handles: x = f(); a, b = f(); val += f().
    """
    # 1. Connect upstream producers of argument variables to current producer
    for ident in extract_arg_identifiers(rhs_call):
        if ident in env:
            graph.add_edge(env[ident], producer, EdgeKind.DATA_FLOW)

    # 2. Bind all LHS targets (handles Name, Tuple, List)
    for target in targets:
        if isinstance(target, ast.Name):
            env[target.id] = producer
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    env[elt.id] = producer


def process_subscript_binding(
    target: ast.Subscript,
    producer: SymbolRef,
    container_env: dict[tuple[str, str], SymbolRef],
) -> None:
    """Bind container field writes d['k'] = f().

    Slices (d[1:5], d[:], d[::2]) are ignored — no binding, no edge.
    Only string literal keys are tracked.
    """
    if not isinstance(target.value, ast.Name):
        return

    container_var = target.value.id
    slice_node = target.slice

    # Python 3.9+ parses d[1] as Subscript(slice=Constant(1))
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        container_env[(container_var, slice_node.value)] = producer
