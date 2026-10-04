"""Arm 3 — Copilot-style Pyright LSP (fidelity MEDIUM).

The context is what a language server knows about the code around the
seed symbol, fetched over LSP from `pyright-langserver --stdio` through the
raw JSON-RPC client in `harness.pyright_client` (no multilspy).

Index (`index()`), once per repository:
1. Editable-install check (charter step 1): the repository's top-level
   module must import from the checkout itself, so imports resolve to the
   code under test. On by default (`ARM3_REQUIRE_EDITABLE_INSTALL`).
2. Launch the server, then the strict handshake (`PyrightClient.handshake`):
   initialize -> initialized -> didChangeConfiguration (basic) -> readiness
   -> at least 5 workspace/symbol probes (the module name, `__init__`, and
   three top-level definitions of the package). All-zero probes abort.

Retrieval (`retrieve()`), seed-anchored and bounded to two hops:
- Hop 1, the seed file: didOpen; documentSymbol (the file outline: top-level
  symbols and class members); hover on the seed symbol; definition at each
  reference in the seed symbol's body (calls, annotations, base classes; at
  most ARM3_MAX_DEFINITIONS distinct names, in source order). A definition
  in the seed file itself gets a hover.
- Hop 2, every other repository file a definition lands in: didOpen; hover
  on the defined symbol; documentSymbol (top-level outline only). Then
  stop: no definition request is ever made from a hop-2 file. Definitions
  outside the repository (typeshed, site-packages) are not followed.

Items, in rank order: seed hover, seed-file outline, same-file hovers,
then per hop-2 file its hover(s) and outline. `lsp_hover` names the symbol
it describes; `lsp_symbol` (an outline) names every symbol it lists. Every
LSP location is mapped to a fully-qualified name by file (module path) and
line (the innermost enclosing outline symbol). Items are added greedily in
rank order while they fit the budget; under-filling it is expected.
"""
from __future__ import annotations

import ast
import atexit
import os
import shutil
import subprocess
import sys
from pathlib import Path

from harness import config as C
from harness.arms.base import RetrievalArm, item_header
from harness.ast_splitter import iter_python_files, module_name
from harness.pyright_client import LspError, PyrightClient, ReadyReport
from harness.scoring.canonical import DeliveredContext, DeliveredItem
from harness.scoring.latency import timed

#: LSP SymbolKind numbers -> short labels used in outlines
SYMBOL_KINDS = {1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class", 6: "method", 7: "property",
                8: "field", 9: "constructor", 10: "enum", 11: "interface", 12: "function", 13: "variable",
                14: "constant", 15: "string", 16: "number", 17: "boolean", 18: "array", 22: "enum_member",
                23: "struct", 26: "type_parameter"}
_CLASS = 5


class PyrightNotFound(LspError):
    pass


class EditableInstallError(LspError):
    pass


def utf16_col(line: str, byte_col: int) -> int:
    """LSP positions count UTF-16 code units; `ast` columns count UTF-8 bytes."""
    prefix = line.encode("utf-8")[:byte_col].decode("utf-8", errors="replace")
    return len(prefix.encode("utf-16-le")) // 2


def module_origin(module: str, python: str = sys.executable) -> str | None:
    """Where `module` imports from, in a fresh interpreter started outside
    any checkout (so the current directory does not shadow the install)."""
    code = ("import importlib.util,sys\ns=importlib.util.find_spec(sys.argv[1])\n"
            "print((s.submodule_search_locations and list(s.submodule_search_locations)[0]) or s.origin if s else '')")
    out = subprocess.run([python, "-c", code, module], capture_output=True, text=True, cwd="/", timeout=60)
    return out.stdout.strip() or None


def reference_positions(tree: ast.AST) -> list[tuple[str, int, int]]:
    """(name, line0, byte_col) of each referenced name in `tree`, in source
    order: call targets, annotations and base classes. For `a.b.c(...)` the
    position is that of `c`, which is what the definition is asked for."""
    found: list[tuple[str, int, int]] = []

    def ref(node: ast.AST | None) -> None:
        if isinstance(node, ast.Name):
            found.append((node.id, node.lineno - 1, node.col_offset))
        elif isinstance(node, ast.Attribute) and node.end_lineno is not None and node.end_col_offset is not None:
            found.append((node.attr, node.end_lineno - 1, node.end_col_offset - len(node.attr)))
        elif isinstance(node, ast.Subscript):          # Annotated[X, ...], list[X]
            ref(node.value)
            ref(node.slice)
        elif isinstance(node, ast.Tuple):
            for elt in node.elts:
                ref(elt)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            ref(node.func)
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                ref(base)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None:
                    ref(arg.annotation)
            ref(node.returns)
    return sorted(found, key=lambda f: (f[1], f[2]))


def _find_ast(tree: ast.Module, names: list[str]) -> ast.AST | None:
    node: ast.AST = tree
    for name in names:
        body = getattr(node, "body", [])
        nxt = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and n.name == name), None)
        if nxt is None:
            return node if node is not tree else None
        node = nxt
    return node


class Arm3PyrightLSP(RetrievalArm):
    arm_id = "arm3"

    def __init__(self, tokenizer=None, budget: int = C.RETRIEVAL_BUDGET, cmd: list[str] | None = None,
                 require_editable: bool | None = None) -> None:
        super().__init__()
        self._tok = tokenizer
        self.budget = budget
        self.cmd = cmd
        self.require_editable = C.ARM3_REQUIRE_EDITABLE_INSTALL if require_editable is None else require_editable
        self.client: PyrightClient | None = None
        self.ready: ReadyReport | None = None
        self.index_latency: dict[str, list[float]] = {}
        self.simplifications = [
            "readiness: Pyright sends no experimental/serverStatus (quiescent=true); the arm waits for its "
            "'Found N source files' log message, then repeats the workspace/symbol probes until two rounds agree",
            f"definitions are requested at references in the seed symbol's body (calls, annotations, base "
            f"classes), at most {C.ARM3_MAX_DEFINITIONS} distinct names, located with Python's ast",
            "definitions outside the repository (typeshed, site-packages) are not followed",
            "an outline item (lsp_symbol) names every symbol it lists",
            "Python files only (FastAPI and Django)",
        ]

    @property
    def tok(self):
        if self._tok is None:
            from harness.tokenizer import get_tokenizer
            self._tok = get_tokenizer()
        return self._tok

    # ---------------------------------------------------------------- index
    def probes(self, repo_path: str, package: str) -> list[str]:
        """The module name, `__init__`, and the first three top-level
        definitions found in the package (by file path): never task data."""
        names: list[str] = []
        pkg_dir = os.path.join(repo_path, package)
        files = sorted(iter_python_files(pkg_dir)) if os.path.isdir(pkg_dir) else []
        for f in files:
            try:
                tree = ast.parse(Path(f).read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for n in tree.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name not in names \
                        and not n.name.startswith("_"):
                    names.append(n.name)
            if len(names) >= 3:
                break
        return [package, "__init__"] + names[:3]

    def index(self, repo_path: str, config: dict | None = None) -> None:
        config = config or {}
        self.repo_root = str(Path(repo_path).resolve())
        self.package = config.get("package") or Path(self.repo_root).name
        cmd = self.cmd or list(config.get("cmd") or ["pyright-langserver", "--stdio"])
        with timed(self.index_latency, "L_index"):
            if shutil.which(cmd[0]) is None and not os.path.exists(cmd[0]):
                raise PyrightNotFound(f"{cmd[0]} not found: pyright-langserver not found (npm install -g pyright)")
            if self.require_editable:     # charter step 1, before the server starts
                origin = module_origin(self.package)
                if not origin or not str(Path(origin).resolve()).startswith(self.repo_root + os.sep):
                    raise EditableInstallError(
                        f"{self.package!r} imports from {origin!r}, not from {self.repo_root}: "
                        f"run `pip install -e {self.repo_root}` before Arm 3 indexes it")
            self.client = PyrightClient(self.repo_root, cmd=cmd)
            self.client.start()
            atexit.register(self.close)
            probes = config.get("probes") or self.probes(self.repo_root, self.package)
            self.ready = self.client.handshake(probes, ready_timeout=config.get("ready_timeout", C.ARM3_READY_TIMEOUT_S))
        self._modules = {module_name(os.path.relpath(f, self.repo_root)): f for f in iter_python_files(self.repo_root)}
        self._outline: dict[str, list[dict]] = {}
        self._source: dict[str, list[str]] = {}

    def close(self) -> None:
        if self.client is not None:
            self.client.shutdown()
            self.client = None

    # ----------------------------------------------------------- lsp parts
    def _open(self, path: str, lat: dict) -> None:
        assert self.client is not None
        if path not in self.client.opened:
            with timed(lat, "L_didOpen"):
                self.client.did_open(path)

    def _symbols(self, path: str, lat: dict) -> list[dict]:
        if path not in self._outline:
            self._open(path, lat)
            assert self.client is not None
            with timed(lat, "L_lsp_documentSymbol"):
                self._outline[path] = self.client.document_symbols(path)
        return self._outline[path]

    def _lines(self, path: str) -> list[str]:
        if path not in self._source:
            self._source[path] = Path(path).read_text(encoding="utf-8", errors="replace").split("\n")
        return self._source[path]

    def _hover(self, path: str, line: int, character: int, lat: dict) -> str:
        self._open(path, lat)
        assert self.client is not None
        with timed(lat, "L_lsp_hover"):
            return self.client.hover(path, line, character).strip()

    def _definition(self, path: str, line: int, character: int, lat: dict) -> list[dict]:
        assert self.client is not None
        with timed(lat, "L_lsp_definition"):
            return self.client.definition(path, line, character)

    def _module_of(self, path: str) -> str:
        return module_name(os.path.relpath(path, self.repo_root))

    def _in_repo(self, path: str) -> bool:
        p = str(Path(path).resolve())
        return p.startswith(self.repo_root + os.sep) and p.endswith(".py")

    @staticmethod
    def _contains(sym: dict, line: int) -> bool:
        rng = sym.get("range") or {}
        return rng.get("start", {}).get("line", -1) <= line <= rng.get("end", {}).get("line", -2)

    def fqn_at(self, path: str, line: int, lat: dict) -> tuple[str, dict | None]:
        """The fully-qualified name of the innermost outline symbol whose
        range contains `line` in `path` (the module itself if none), and
        that symbol."""
        parts, node, best = [self._module_of(path)], self._symbols(path, lat), None
        while True:
            hit = next((s for s in node if self._contains(s, line)), None)
            if hit is None:
                break
            parts.append(hit["name"])
            best = hit
            node = hit.get("children") or []
        return ".".join(p for p in parts if p), best

    def _resolve_seed(self, seed_symbol: str) -> tuple[str | None, list[str]]:
        """(file, names inside it) for a seed FQN: the longest module prefix
        that is a file of the repository."""
        parts = seed_symbol.split(".")
        for k in range(len(parts), 0, -1):
            path = self._modules.get(".".join(parts[:k]))
            if path:
                return path, parts[k:]
        return None, []

    def _find_symbol(self, outline: list[dict], names: list[str]) -> dict | None:
        node, found = outline, None
        for name in names:
            hit = next((s for s in node if s.get("name") == name), None)
            if hit is None:
                break
            found, node = hit, hit.get("children") or []
        return found

    def _outline_item(self, path: str, lat: dict, members: bool) -> tuple[str, list[str]]:
        """Rendered outline and the FQNs it lists: top-level symbols, plus
        class members when `members` (functions' children are locals and
        are never listed)."""
        mod = self._module_of(path)
        lines, fqns = [], []

        def walk(syms: list[dict], prefix: str, depth: int) -> None:
            for s in syms:
                kind = SYMBOL_KINDS.get(int(s.get("kind", 0)), "symbol")
                start = s.get("selectionRange", s.get("range", {})).get("start", {}).get("line", 0) + 1
                fq = f"{prefix}.{s['name']}"
                lines.append(f"{'    ' * depth}{kind} {s['name']}  (line {start})")
                fqns.append(fq)
                if members and depth == 0 and int(s.get("kind", 0)) == _CLASS:
                    walk(s.get("children") or [], fq, depth + 1)

        walk(self._symbols(path, lat), mod, 0)
        return "\n".join(lines), fqns

    # ---------------------------------------------------------------- arm
    def retrieve(self, query: str, seed: dict) -> DeliveredContext:
        if self.client is None:
            raise LspError("arm3: index() has not started the language server")
        lat: dict[str, list[float]] = {}
        cands: list[dict] = []        # in rank order
        stats = {"hop1_definitions": 0, "hop2_files": 0, "external_definitions": 0, "unresolved_definitions": 0,
                 "same_file_definitions": 0}
        seed_symbol = seed.get("seed_symbol") or ""
        with timed(lat, "L_retrieve"):
            seed_path, inner = self._resolve_seed(seed_symbol) if seed_symbol else (None, [])
            if seed_path is not None:
                self._hop(seed_path, inner, seed_symbol, cands, stats, lat)
            items = self._pack(cands)
        meta = {
            **self.fidelity_meta(), "query": query, "task_type": seed.get("task_type"), "turn_count": 1,
            "over_budget": False, "ranking_method": "lsp_hop_order",
            "seed_file": os.path.relpath(seed_path, self.repo_root) if seed_path else None,
            "n_candidates": len(cands), "n_delivered": len(items), **stats,
            "ready": {"seconds": round(self.ready.seconds, 2), "source_files": self.ready.source_files,
                      "probes": self.ready.probes, "rounds": self.ready.rounds} if self.ready else None,
            "latency_ms": lat,
        }
        return DeliveredContext("arm3", seed["task_id"], items, sum(i.token_count for i in items), self.budget, meta)

    def _hop(self, seed_path: str, inner: list[str], seed_symbol: str, cands: list[dict], stats: dict,
             lat: dict) -> None:
        rel = os.path.relpath(seed_path, self.repo_root)
        outline = self._symbols(seed_path, lat)                         # hop 1: didOpen + documentSymbol
        target = self._find_symbol(outline, inner) if inner else None
        if target is not None:
            pos = target.get("selectionRange", target["range"])["start"]
            text = self._hover(seed_path, pos["line"], pos["character"], lat)
            fq, _ = self.fqn_at(seed_path, pos["line"], lat)
            if text:
                cands.append(self._cand(f"{rel}:{pos['line'] + 1}", fq, text, "lsp_hover", [fq], 1, "seed"))
        body, fqns = self._outline_item(seed_path, lat, members=True)
        if body:
            cands.append(self._cand(f"{rel}#outline", self._module_of(seed_path) + " outline", body, "lsp_symbol",
                                    fqns, 1, "seed_outline"))
        # definitions at the references in the seed symbol's body
        try:
            tree = ast.parse("\n".join(self._lines(seed_path)))
        except SyntaxError:
            return
        node = _find_ast(tree, inner) if inner else None
        if node is None:
            return
        refs, seen = [], set()
        for name, line, col in reference_positions(node):
            if name not in seen:
                seen.add(name)
                refs.append((name, line, col))
        refs = refs[:C.ARM3_MAX_DEFINITIONS]
        same_file: list[dict] = []
        hop2: dict[str, list[dict]] = {}
        seen_defs: set[tuple[str, int]] = set()
        if target is not None:
            seen_defs.add((seed_path, target.get("selectionRange", target["range"])["start"]["line"]))
        lines = self._lines(seed_path)
        for _name, line, col in refs:
            stats["hop1_definitions"] += 1
            locs = self._definition(seed_path, line, utf16_col(lines[line], col), lat)
            if not locs:
                stats["unresolved_definitions"] += 1
            for loc in locs[:1]:
                path = str(Path(loc["path"]).resolve())
                if not self._in_repo(path):
                    stats["external_definitions"] += 1
                    continue
                key = (path, loc["line"])
                if key in seen_defs:
                    continue
                seen_defs.add(key)
                (same_file if path == str(Path(seed_path).resolve()) else hop2.setdefault(path, [])).append(loc)
        for loc in same_file:
            stats["same_file_definitions"] += 1
            self._def_hover(loc, cands, lat, hop=1, origin="same_file_definition")
        for path, locs in hop2.items():                                  # hop 2, then stop
            stats["hop2_files"] += 1
            for loc in locs:
                self._def_hover(loc, cands, lat, hop=2, origin="hop2_definition")
            body, fqns = self._outline_item(path, lat, members=False)
            if body:
                cands.append(self._cand(f"{os.path.relpath(path, self.repo_root)}#outline",
                                        self._module_of(path) + " outline", body, "lsp_symbol", fqns, 2,
                                        "hop2_outline"))

    def _def_hover(self, loc: dict, cands: list[dict], lat: dict, hop: int, origin: str) -> None:
        path, line, char = loc["path"], loc["line"], loc["character"]
        text = self._hover(path, line, char, lat)
        fq, _ = self.fqn_at(path, line, lat)
        if text:
            cands.append(self._cand(f"{os.path.relpath(path, self.repo_root)}:{line + 1}", fq, text, "lsp_hover",
                                    [fq], hop, origin))

    @staticmethod
    def _cand(source_id: str, label: str, body: str, kind: str, symbols: list[str], hop: int, origin: str) -> dict:
        return {"source_id": source_id, "content": f"{item_header(source_id, label)}\n{body}", "kind": kind,
                "symbols": symbols, "hop": hop, "origin": origin}

    def _pack(self, cands: list[dict]) -> list[DeliveredItem]:
        """Greedy in rank order: each candidate that still fits the budget is
        delivered; one that does not is skipped (it is never truncated)."""
        items: list[DeliveredItem] = []
        used = 0
        for c in cands:
            n = self.tok.count(c["content"])
            if used + n > self.budget:
                continue
            used += n
            items.append(DeliveredItem(c["source_id"], c["content"], n, len(items) + 1, c["kind"], c["symbols"],
                                       {"hop": c["hop"], "origin": c["origin"]}))
        return items

    def build_prompt(self, ctx: DeliveredContext, tokenizer=None) -> str:
        return self.build_prompt_default(ctx)


__all__ = ["Arm3PyrightLSP", "EditableInstallError", "PyrightNotFound", "reference_positions", "utf16_col"]
