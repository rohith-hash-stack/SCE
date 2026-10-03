"""Hallucination detection: how many code entities the answer names that do
not exist in the repository.

Candidates are G*_universe, the task's ground-truth universe, for EVERY arm
(cross-engine scoring). The arm's own retrieved set (`ctx.delivered_symbols`)
is never used as the candidate set: an engine that retrieved little would
otherwise be judged against less, and engine-relative scores are not
comparable across engines.

An identifier the answer names is NOT hallucinated if it is in G*_universe
or resolves in the repository:
  1. file path (contains "/"): `repo_root / ident` exists.
  2. dotted name (contains "."), in order:
     a. symbol cache: it equals a known fully-qualified name or one of its
        dotted suffixes (`utils.get_dependant` hits);
     b. module binding: its longest prefix that is a repository module binds
        the last name at module level, by import / re-export
        (`from fastapi.utils import get_path_param_names` makes
        `fastapi.dependencies.utils.get_path_param_names` real;
        `from starlette.responses import JSONResponse as JSONResponse`
        makes `fastapi.responses.JSONResponse` real) or by a top-level
        def / class / assignment, including inside top-level if/try blocks
        (an AST index per module, cached);
     c. inheritance: `Class.member` where `member` is defined on an
        ancestor of `Class` (class hierarchy from the code graph):
        `RequestValidationError.errors` via `ValidationException.errors`.
     Otherwise it is unresolved. Its last component alone never resolves
     it: `solve_dependencies.values` (a local of a function) and
     `exception_handlers.exc.errors` stay hallucinations although "values"
     and "errors" occur in the code. The pipeline always supplies a symbol
     cache (the PRISM symbol table; M2 adds Pyright's workspace/symbol
     results) and the class hierarchy.
  3. plain name (no dot): the symbol cache, else ripgrep
     (`rg --word-regexp --fixed-strings -l`, timeout 5 s).
Identifiers shorter than 4 characters are excluded (x, i, id, ...).
"""
from __future__ import annotations

import ast
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from harness import config as C

_BACKTICK = re.compile(r"`([^`\n]{1,200})`")
_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.\w{1,5})(?![\w/])")
_DOTTED = re.compile(r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?:\(\))?")
_SNAKE = re.compile(r"(?<![\w.])([a-z_][a-z0-9]*_[a-z0-9_]+)(?![\w.])")
_CAMEL = re.compile(r"(?<![\w.])([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+)(?![\w.])")
_IDENT_IN_TICKS = re.compile(r"^[A-Za-z_][\w./-]*(?:\(\))?$")
#: Dotted tokens that are prose, not code (e.g. "e.g", "i.e").
_PROSE_DOTTED = {"e.g", "i.e", "etc.", "vs."}

CATEGORIES = ("file_path", "class_name", "method_name", "module_import")


def extract_identifiers(text: str) -> list[str]:
    """Code-like identifiers in `text`, in first-appearance order, without
    duplicates: backticked identifiers, file paths, dotted names,
    snake_case and CamelCase words. Plain English words are not extracted."""
    found: list[tuple[int, str]] = []
    for m in _BACKTICK.finditer(text):
        inner = m.group(1).strip()
        if _IDENT_IN_TICKS.match(inner):
            found.append((m.start(), inner.removesuffix("()")))
    for rx in (_PATH, _DOTTED, _SNAKE, _CAMEL):
        for m in rx.finditer(text):
            tok = m.group(1).rstrip(".")
            if tok.lower() in _PROSE_DOTTED:
                continue
            found.append((m.start(1), tok))
    seen: set[str] = set()
    out = []
    for _, tok in sorted(found, key=lambda x: x[0]):
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    # a token that is a strict part of a longer extracted one at the same
    # place (e.g. `utils.get_dependant` inside a full FQN) is kept: it is
    # resolved on its own, and both resolve or neither does.
    return out


def categorize(ident: str) -> str:
    if "/" in ident:
        return "file_path"
    if "." in ident:
        return "module_import"
    if re.fullmatch(r"[A-Z][A-Za-z0-9]*", ident) and re.search(r"[a-z]", ident):
        return "class_name"
    return "method_name"


def build_symbol_cache(qualified_names) -> frozenset[str]:
    """Every fully-qualified name plus each of its dotted suffixes, so
    `utils.get_dependant` and `get_dependant` both hit."""
    out: set[str] = set()
    for q in qualified_names:
        parts = q.split(".")
        for i in range(len(parts)):
            out.add(".".join(parts[i:]))
    return frozenset(out)


@lru_cache(maxsize=65536)
def _rg_finds(word: str, repo_root: str) -> bool:
    try:
        proc = subprocess.run(
            ["rg", "--word-regexp", "--fixed-strings", "-l", "-m", "1", "--", word, repo_root],
            capture_output=True, text=True, timeout=C.RIPGREP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # Unknown, not hallucinated: a slow search is not evidence of absence.
        return True
    except FileNotFoundError as exc:
        raise RuntimeError("ripgrep (rg) is required for hallucination detection") from exc
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _module_file(repo_root: str, parts: list[str]) -> Path | None:
    base = Path(repo_root).joinpath(*parts)
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _bound_names(body: list) -> set[str]:
    """Names a module binds at top level: imports (`as` names included),
    defs, classes, assignment targets; recursing into top-level if / try /
    with blocks, never into function or class bodies."""
    out: set[str] = set()
    for node in body:
        if isinstance(node, ast.Import):
            out.update((a.asname or a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.update((a.asname or a.name) for a in node.names if a.name != "*")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                out.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, ast.If):
            out |= _bound_names(node.body) | _bound_names(node.orelse)
        elif isinstance(node, ast.Try):
            out |= _bound_names(node.body) | _bound_names(node.orelse) | _bound_names(node.finalbody)
            for h in node.handlers:
                out |= _bound_names(h.body)
        elif isinstance(node, ast.With):
            out |= _bound_names(node.body)
    return out


@lru_cache(maxsize=4096)
def module_bindings(repo_root: str, module: str) -> frozenset[str] | None:
    """Top-level names bound by repository module `module`, or None when
    no such module file exists (or it does not parse)."""
    path = _module_file(repo_root, module.split("."))
    if path is None:
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None
    return frozenset(_bound_names(tree.body))


def _resolves_by_module_binding(ident: str, repo_root: str) -> bool:
    parts = ident.split(".")
    for k in range(len(parts) - 1, 0, -1):
        bound = module_bindings(repo_root, ".".join(parts[:k]))
        if bound is None:
            continue
        # the longest module prefix decides: exactly one name may follow it
        return len(parts) - k == 1 and parts[-1] in bound
    return False


def _resolves_by_inheritance(ident: str, symbol_cache: frozenset[str] | None, ancestors: dict | None) -> bool:
    if not symbol_cache or not ancestors:
        return False
    cls_part, member = ident.rsplit(".", 1)
    for cls, anc in ancestors.items():
        if cls == cls_part or cls.endswith("." + cls_part):
            if any(f"{a}.{member}" in symbol_cache for a in anc):
                return True
    return False


def resolve_identifier(ident: str, repo_root: str, symbol_cache: frozenset[str] | None = None,
                       ancestors: dict | None = None) -> bool:
    if symbol_cache and ident in symbol_cache:
        return True
    root = Path(repo_root)
    if "/" in ident:
        return (root / ident).exists()
    if "." in ident:
        # never by the last component alone: by full / dotted-suffix symbol
        # (above), module binding, or inheritance
        if root.is_dir() and _resolves_by_module_binding(ident, str(root)):
            return True
        return _resolves_by_inheritance(ident, symbol_cache, ancestors)
    if not root.is_dir():
        return False
    return _rg_finds(ident, str(root))


def universe_candidates(task) -> set[str]:
    """G*_universe: the one candidate set for every arm."""
    return task.ground_truth.universe_symbols()


def hallucination_rate(answer_symbols: list[str], task, ctx=None, symbol_cache: frozenset[str] | None = None,
                       ancestors: dict | None = None) -> tuple[float, dict[str, list[str]]]:
    """(rate, breakdown). rate = unresolved / candidates, NaN when the answer
    names no identifier of >= 4 characters. `ctx` is accepted for interface
    symmetry and deliberately unused (see module docstring)."""
    universe = universe_candidates(task)
    # Fairness rule 1: candidates are G*_universe, never the arm's own set.
    assert universe == task.ground_truth.universe_symbols()
    assert ctx is None or universe is not getattr(ctx, "delivered_symbols", None)
    breakdown: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    candidates = [s for s in answer_symbols if len(s) >= C.HALLUCINATION_MIN_IDENT_LEN]
    if not candidates:
        return float("nan"), breakdown
    universe_cache = build_symbol_cache(universe)
    unresolved = [
        c for c in candidates
        if c not in universe_cache and not resolve_identifier(c, task.repo_root, symbol_cache, ancestors)
    ]
    for c in unresolved:
        breakdown[categorize(c)].append(c)
    return len(unresolved) / len(candidates), breakdown
