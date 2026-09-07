"""The "standard whole-file dumping" baseline SCE is benchmarked against.

For a target symbol, the raw baseline is the full, unmodified text of every
source file that the target's call chain transitively touches - i.e. every
file containing a symbol reachable from the seed via `CALLS` edges in the
concrete graph `G_C`. This is the realistic alternative to SCE: instead of
a variable-resolution slice, an agent (or a human) pastes in whole files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import networkx as nx

from sce.graph.concrete_builder import ConcreteGraphBuilder

FILE_HEADER_TEMPLATE = "# ==== FILE: {relative_path} ====\n"


class RawContextError(Exception):
    """Raised when a raw-context baseline cannot be constructed for a seed."""


@dataclass(frozen=True)
class RawContextResult:
    seed: str
    files: tuple[str, ...]  # absolute paths, sorted
    reachable_symbols: frozenset[str] = field(repr=False)  # seed + everything it transitively calls
    text: str = field(repr=False)


def dump_files(builder: ConcreteGraphBuilder, files: list[str] | tuple[str, ...]) -> str:
    """Concatenate the full text of `files` (absolute paths), each preceded
    by a `# ==== FILE: <relative path> ====` header. The low-level building
    block behind `build_raw_context`'s call-chain closure; also used
    directly by callers that need a whole-repo dump instead (e.g. a task
    whose fix lives outside the target's own call graph - see
    `benchmarks/tasks.py`).
    """
    parts: list[str] = []
    for path in sorted(files):
        parsed = builder.parsed_file(path)
        if parsed is not None:
            source = parsed.source.decode("utf-8", errors="replace")
        else:
            # Defensive fallback: shouldn't happen for a file that produced
            # at least one indexed symbol, but never let a stale cache
            # entry crash the benchmark run.
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        relative_path = os.path.relpath(path, builder.repo_root)
        parts.append(FILE_HEADER_TEMPLATE.format(relative_path=relative_path) + source)
    return "\n\n".join(parts)


def build_raw_context(builder: ConcreteGraphBuilder, seed: str) -> RawContextResult:
    """Concatenate every file reachable (transitively, via CALLS edges) from
    `seed`, in a stable file order, mirroring what a developer would paste
    in if they whole-file-dumped the target's dependency closure by hand.
    """
    if seed not in builder.graph:
        raise RawContextError(f"seed symbol '{seed}' is not a known node in the concrete graph")

    reachable = set(nx.descendants(builder.graph, seed))
    reachable.add(seed)

    files: set[str] = set()
    for node in reachable:
        symbol = builder.symbol_table.get(node)
        if symbol is not None:
            files.add(symbol.file)

    if not files:
        raise RawContextError(
            f"seed symbol '{seed}' resolved to zero source files "
            "(it may be an external/unresolved symbol rather than one SCE indexed)"
        )

    sorted_files = tuple(sorted(files))
    return RawContextResult(
        seed=seed,
        files=sorted_files,
        reachable_symbols=frozenset(reachable),
        text=dump_files(builder, sorted_files),
    )
