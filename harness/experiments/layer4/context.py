"""Layer 4 context construction: each Stage 2 arm's context for a Layer 4
task, built by the Stage 2 builders unchanged (`benchmarks.final_sweep.arms`),
then made safe for Layer 4:

1. **filter** - nodes from test files (Prism's own `_in_test_file`) and the
   task's gating files (`arm_view.exclude_paths`) are dropped, with the
   edges that touch them;
2. **mask** (backward tasks) - the focal node's body becomes its masked
   definition (signature, Python docstring, `raise NotImplementedError` /
   `throw new Error`); any other node that embeds the definition (a class
   or module chunk) gets the same replacement;
3. the token ceiling, rendering and breadcrumbs as in Stage 2;
4. **leak check** on the final text: distinctive original body lines,
   gating file paths, trace-manifest keys; and on the prompt: no gold
   symbol named. A leaking cell is never sent to the model.

The retrieval itself (rankings, Turn 1 of prism_full) sees the unmasked
corpus: what an arm *shows* never contains the body, what it *ranks* may
favour the focal function (stated in LAYER4.md).
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from harness.experiments.layer4 import tasks as T
from harness.experiments.layer4.source_edit import locate

#: Layer 4 arm name -> Stage 2 engine id (`benchmarks.final_sweep.config.STAGE2_ARMS`)
ARMS = ("arm0_no_retrieval", "arm1_hybrid_rag", "repomap_pagerank", "prism_full", "prism_ask", "normal_oracle",
        "prism_oracle")
#: strings that only trace manifests and gate records contain
TRACE_MARKERS = ("ancestor_depth", "descendant_depth", "focal_calls", "oracle_callers", "sql_dropped_tables",
                 "STUB_IMPLEMENTATION")
TRPC_PREFIX = "packages/server/src"

FORWARD_SYSTEM = (
    "You are a senior software engineer assessing the impact of a planned change in a codebase. Use the repository "
    "context you are given (it may be empty or incomplete) and your knowledge of the codebase. Name functions and "
    "methods by their fully qualified names as they appear in the context.")
BACKWARD_SYSTEM = (
    "You are a senior software engineer. A function's body has been removed from a codebase; you will write it "
    "again so that the code that calls it keeps working. Use the repository context you are given (it may be empty "
    "or incomplete) and your knowledge of the codebase.")


def clone_root(corpus: str, index_root: str) -> str:
    """The corpus clone's top level (tRPC is indexed at TRPC_PREFIX below it)."""
    if corpus == "trpc" and index_root.rstrip("/").endswith(TRPC_PREFIX):
        return index_root.rstrip("/")[: -len(TRPC_PREFIX) - 1]
    return index_root


def index_prefix(corpus: str) -> str:
    return TRPC_PREFIX + "/" if corpus == "trpc" else ""


@dataclass
class Corpus:
    """One corpus's Stage 2 state plus what Layer 4 needs around it."""
    name: str
    state: object  # benchmarks.final_sweep.arms.CorpusState
    index_root: str
    clone_root: str
    known: set[str] = field(default_factory=set)  # gold-eligible names: application functions/methods
    lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def load(cls, name: str) -> "Corpus":
        from benchmarks.corpora.resolver import resolve
        from benchmarks.final_sweep.arms import CorpusState
        from prism.graph.entry_points import example_zone
        index_root = str(resolve(name))
        state = CorpusState(name, index_root)
        b = state.builder
        known = set()
        for s in b.symbol_table.values() if isinstance(b.symbol_table, dict) else b.symbol_table:
            sym = s if hasattr(s, "kind") else b.symbol_table[s]
            if sym.kind in ("function", "method"):
                rel = os.path.relpath(sym.file, b.repo_root) if os.path.isabs(sym.file) else sym.file
                if not b._in_test_file(sym.file) and not example_zone(rel):
                    known.add(sym.qualified_name)
        return cls(name, state, index_root, clone_root(name, index_root), known)


# ---------------------------------------------------------------- focal definition
def focal_definition(task: dict, clone: str) -> str:
    """The focal function's original definition text, checked against the
    hash frozen in the task (the corpus checkout must not have drifted)."""
    path = os.path.join(clone, task["focal"]["file"])
    span = locate(path, task["focal"]["line"])
    text = Path(path).read_bytes()[span.start:span.end].decode()
    want = task["focal"].get("definition_sha256")
    if want and hashlib.sha256(text.encode()).hexdigest() != want:
        raise RuntimeError(f"{task['task_id']}: focal definition changed since authoring ({task['focal']['file']})")
    return text


# ---------------------------------------------------------------- filter + mask
def is_excluded(node_file: str, task: dict, in_test_file) -> bool:
    """A node from a test file (Prism's rule, index-relative path) or from
    one of the task's gating files (clone-relative paths)."""
    rel = node_file.replace(os.sep, "/")
    if in_test_file(node_file):
        return True
    clone_rel = index_prefix(task["corpus"]) + rel
    return any(clone_rel == p or clone_rel.startswith(p.rstrip("/") + "/") for p in task["arm_view"]["exclude_paths"])


def mask_package(pkg, task: dict, definition: str | None, in_test_file):
    """The package with test/gating-file nodes dropped and, for a backward
    task, the focal definition masked everywhere it appears verbatim.
    Returns (package, report)."""
    focal = task["focal"]["qname"]
    masked = T.masked_definition(definition, task["focal"]["language"]) if definition else None
    keep, dropped, masked_ids = [], [], []
    for node in pkg.nodes:
        if is_excluded(node.file, task, in_test_file):
            dropped.append(node.id)
            continue
        if masked is not None:
            if node.id == focal:
                node = node.model_copy(update={"body": masked})
                masked_ids.append(node.id)
            elif definition in node.body:
                node = node.model_copy(update={"body": node.body.replace(definition, masked)})
                masked_ids.append(node.id)
            elif definition.strip() in node.body:
                node = node.model_copy(update={"body": node.body.replace(definition.strip(), masked.strip())})
                masked_ids.append(node.id)
        keep.append(node)
    ids = {n.id for n in keep}
    edges = [e for e in pkg.edges if e.from_node in ids and e.to_node in ids]
    return pkg.model_copy(update={"nodes": keep, "edges": edges}), {"dropped_test_nodes": dropped,
                                                                    "masked_nodes": masked_ids}


# ---------------------------------------------------------------- leak checks
def context_leaks(rendered: str, task: dict, definition: str | None) -> dict:
    """What the final context shows that it must not: distinctive focal
    body lines (backward), gating files, trace-manifest keys."""
    text = html.unescape(rendered)
    leak = T.leak_check(text, task, definition)
    leak["trace_markers"] = [m for m in TRACE_MARKERS if m in text]
    return leak


def prompt_leaks(prompt: str, task: dict) -> list[str]:
    """Gold names the task prompt itself gives away (forward: affected
    symbols other than the focal; any family: trace keys)."""
    out = [m for m in TRACE_MARKERS if m in prompt]
    if task["family"] == "forward_blast":
        for name in task["gold"]["affected_symbols"]:  # whole names: get_openapi is not inside get_openapi_path
            if name != task["focal"]["qname"] and re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", prompt):
                out.append(name)
    return out


def leaked(leak: dict) -> bool:
    return bool(leak["focal_body_lines"] or leak["test_files"] or leak["trace_markers"])


# ---------------------------------------------------------------- one arm's context
def build_context(corpus: Corpus, arm: str, task: dict, seed: int | None, cfg, client, log, record) -> dict:
    """Context for `arm` on `task` with the Stage 2 builders, filtered and
    masked. `record` is a Stage 2 CellRecord (its two-pass bookkeeping),
    `log` a Stage 2 _CallLog (prism_full's Turn 1 is counted there)."""
    from benchmarks.engines.base import selected_symbols
    from benchmarks.final_sweep import config as C
    from benchmarks.final_sweep.arms import (
        build_ask, build_hybrid, build_none, build_oracle, build_pagerank, render_breadcrumbs,
    )
    from benchmarks.final_sweep.context_ops import enforce_token_ceiling, redact_package_axes, render_context
    from benchmarks.final_sweep.runner import NO_CONTEXT_TEXT, _two_pass_context
    from prism.slicer.tokenizer import count_tokens

    state = corpus.state
    spec = C.resolve_arm(C.STAGE2_ARMS[arm])
    et = T.to_evaluation_task(task)
    definition = focal_definition(task, corpus.clone_root) if task["family"] == "backward_adaptation" else None
    with corpus.lock:
        if spec.kind == "oracle":
            base = ("scaffolded_oracle" if spec.scaffolded else "pragmatic_oracle") if spec.breadcrumbs else spec.engine_id
            built = build_oracle(state, et, cfg.budget, spec.scaffolded, base)
        elif spec.kind == "pagerank":
            built = build_pagerank(state, et)
        elif spec.kind == "none":
            built = build_none(state, et, cfg.budget, spec.engine_id)
        elif spec.kind == "hybrid":
            built = build_hybrid(state, et, cfg.budget, spec.engine_id)
        elif spec.kind == "ask":
            built = build_ask(state, et, cfg.budget, spec.engine_id)
        elif spec.kind == "two_pass":
            built, _distractors = _two_pass_context(state, client, spec, et, seed, cfg, record, log, {})
        else:
            raise ValueError(f"arm {arm}: unsupported kind {spec.kind}")
        pkg, mask_report = mask_package(built.pkg, task, definition, state.builder._in_test_file)
        pkg = redact_package_axes(pkg, spec.axes if spec.kind == "two_pass" else "all")
        pkg, ceiling_dropped, tokens_before, tokens_after = enforce_token_ceiling(pkg, cfg.token_ceiling, set())
        rendered = render_context(pkg)
        packed = sorted(selected_symbols(pkg))
        if spec.kind == "none":
            rendered, packed = NO_CONTEXT_TEXT, []
        if spec.breadcrumbs:
            rendered, _placed = render_breadcrumbs(state, [n.id for n in pkg.nodes], rendered)
    leak = context_leaks(rendered, task, definition)
    return {"rendered": rendered, "selected_symbols": packed, "context_tokens": count_tokens(rendered),
            "context_tokens_pre_ceiling": tokens_before, "n_ceiling_dropped": len(ceiling_dropped),
            "leak": leak, "leaked": leaked(leak), "engine_id": spec.engine_id, **mask_report,
            "focal_in_context": task["focal"]["qname"] in packed}


def build_prompt(task: dict, rendered: str) -> tuple[str, str]:
    from benchmarks.tsr.client import build_prompt as stage2_build_prompt
    system = FORWARD_SYSTEM if task["family"] == "forward_blast" else BACKWARD_SYSTEM
    return stage2_build_prompt(system, rendered, task["prompt"] + T.answer_contract(task))
