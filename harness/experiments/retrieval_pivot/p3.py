"""Phase P3: the payload arms in front of a model, through the Layer 4 tournament runner.

P3 adds ten arms to `harness.experiments.layer4` - five payload designs at two budgets - and
changes nothing else: same tasks, prompts, model call, leak checks and scoring.

| P3 arm | reach | anchor | ring 1 (direct helpers) | ring >= 2 (outer) | P1 rule |
|---|---|---|---|---|---|
| R0_A0  | R0 | full | full               | full                 | A0  |
| R1_A0  | R1 | full | full               | full                 | A0  |
| R1_A1n | R1 | full | full               | signature + contract | A1n |
| R1_A4  | R1 | full | full + 4-axis prof | full                 | A6  |
| R1_A5  | R1 | full | full + 4-axis prof | signature + contract | A4  |

(P3's A4 / A5 are named in the P3 plan; they map to P1's representation rules A6 / A4.)
Arm ids: `p3_<arm>@4k`, `p3_<arm>@8k`.

Context = the seed (the task's focal symbol, which the task prompt names) packed by the production
packer with P1 costs (`p1.repriced`), extended by R1's ring fill, rendered by `p1.Renderer`. Then,
as Layer 4 does for every arm (`context.mask_package`): nodes from test or gating files are
dropped; for a backward task the focal definition is replaced by its masked form and nodes nested
inside it are dropped; and the final text goes through `context.context_leaks` (a leaking cell is
never sent). No summaries are used, so P3 needs no summarization run.

    PYTHONPATH=.:src python -m harness.experiments.layer4.runner run --arms p3 --seeds ... --out ...
    PYTHONPATH=.:src python -m harness.experiments.retrieval_pivot.p3 dryrun OUT   # contexts, no model
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DESIGNS = {"R0_A0": ("R0", "A0"), "R1_A0": ("R1", "A0"), "R1_A1n": ("R1", "A1n"),
           "R1_A4": ("R1", "A6"), "R1_A5": ("R1", "A4")}
BUDGETS = (4000, 8000)
ARMS = {f"p3_{d}@{b // 1000}k": (reach, rule, b) for d, (reach, rule) in DESIGNS.items() for b in BUDGETS}
ENGINE_ID = "p3_payload"
SECTION_SEP = "\n\n"


def _rel(builder, path: str) -> str:
    return os.path.relpath(path, builder.repo_root) if os.path.isabs(path) else path


def build_context(corpus, arm: str, task: dict, seed, cfg, client, log, record) -> dict:
    """`harness.experiments.layer4.context.build_context`'s contract, for a P3 arm."""
    from harness.experiments.layer4 import context as X
    from harness.experiments.layer4 import tasks as T
    from harness.experiments.retrieval_pivot import p1
    from prism.slicer.tokenizer import count_tokens

    reach, rule, budget = ARMS[arm]
    b, contracts = corpus.state.builder, corpus.state.contracts
    focal = task["focal"]["qname"]
    if b.symbol_table.get(focal) is None:
        raise ValueError(f"{task['task_id']}: focal {focal} is not indexed")
    backward = task["family"] == "backward_adaptation"
    with corpus.lock:
        definition = X.focal_definition(task, corpus.clone_root) if backward else None
        common = X.corpus_common(corpus, task, definition) if definition else frozenset()
        span = X.focal_span(task, corpus.clone_root, definition) if definition else None
        ring = p1.rings(b, focal)
        r = p1.Renderer(b, contracts, {}, seed=focal)
        with p1.repriced(r, focal, rule, ring):
            items = p1.pack_reach(b, contracts, focal, budget, reach, ring)
        focal_file = task["focal"].get("index_file", task["focal"]["file"])
        sections, packed, dropped, nested, masked_ids = [], [], [], [], []
        for item in items:
            q = item.symbol
            info = b.symbol_table.get(q)
            rel = _rel(b, info.file) if info else ""
            if info is not None and (b._in_test_file(info.file) or X.is_excluded(rel, task, lambda _f: False)):
                dropped.append(q)
                continue
            if span and q != focal and info is not None and rel.replace(os.sep, "/") == focal_file \
                    and span[0] < info.line_range[0] and info.line_range[1] <= span[1]:
                nested.append(q)
                continue
            rep = "full" if q == focal else p1.arm_rep(rule, ring.get(q, 99))
            if item.compression == "L2_skeleton" and rep.startswith("full"):
                rep = "sig" + rep[4:]
            text, _used = r.render(q, rep)
            if q == focal and definition:
                masked = T.masked_definition(definition, task["focal"]["language"])
                text = f"{r.header(q)}\n{masked}\n{p1.contract_text(contracts.get(q))}".rstrip()
                masked_ids.append(q)
            sections.append(text)
            packed.append(q)
    rendered = SECTION_SEP.join(sections)
    tokens_before = count_tokens(rendered)
    trimmed = 0
    # node costs are counted per node; the joined text can tokenize slightly larger: drop the
    # last-packed non-focal sections (the farthest fill first) until the whole context fits
    while count_tokens(rendered) > budget and len(sections) > 1:
        i = max(j for j, q in enumerate(packed) if q != focal)
        sections.pop(i)
        packed.pop(i)
        trimmed += 1
        rendered = SECTION_SEP.join(sections)
    leak = X.context_leaks(rendered, task, definition, common)
    tokens = count_tokens(rendered)
    return {"rendered": rendered, "leak_check": X.LEAK_CHECK_VERSION, "manifest_direction": None,
            "selected_symbols": sorted(packed), "context_tokens": tokens, "context_tokens_pre_ceiling": tokens_before,
            "n_ceiling_dropped": trimmed, "leak": leak, "leaked": X.leaked(leak), "engine_id": f"{ENGINE_ID}/{arm}",
            "dropped_test_nodes": dropped, "masked_nodes": masked_ids, "dropped_nested_nodes": nested,
            "focal_in_context": focal in packed}


def dryrun(out: Path, corpora=("fastapi", "django")) -> list[dict]:
    """Every P3 context for the Layer 4 tasks of `corpora`, no model: tokens, leaks, gold coverage."""
    from harness.experiments.layer4 import context as X
    from harness.experiments.layer4.runner import TASKS_DIR
    rows = []
    for corpus_name in corpora:
        corpus = X.Corpus.load(corpus_name)
        for path in sorted(TASKS_DIR.glob(f"l4_*_{corpus_name}_*.json")):
            task = json.loads(path.read_text())
            gold = set((task.get("gold") or {}).get("affected_symbols") or [])
            for arm in ARMS:
                ctx = build_context(corpus, arm, task, None, None, None, None, None)
                sel = set(ctx["selected_symbols"])
                rows.append({"task": task["task_id"], "family": task["family"], "arm": arm,
                             "tokens": ctx["context_tokens"], "trimmed": ctx["n_ceiling_dropped"],
                             "nodes": len(sel), "leaked": ctx["leaked"],
                             "focal_in_context": ctx["focal_in_context"], "gold_callers": len(gold),
                             "gold_callers_in_context": len(gold & sel)})
    out.mkdir(parents=True, exist_ok=True)
    (out / f"p3_dryrun_{'_'.join(corpora)}.json").write_text(json.dumps(rows, indent=1) + "\n")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("dryrun",))
    ap.add_argument("out", type=Path)
    ap.add_argument("--corpora", default="fastapi,django", help="comma-separated Layer 4 corpora")
    a = ap.parse_args(argv)
    rows = dryrun(a.out, tuple(a.corpora.split(",")))
    for r in rows:
        print(f"{r['task']:22} {r['arm']:16} tokens {r['tokens']:5}  nodes {r['nodes']:3}  leaked {r['leaked']!s:5}  "
              f"gold callers {r['gold_callers_in_context']}/{r['gold_callers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
