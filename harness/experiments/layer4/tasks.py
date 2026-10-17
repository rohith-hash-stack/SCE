"""Layer 4 task contract: validation, the arm-facing view, answer parsing,
scoring, masking and aggregation for the 16-task suite (8 forward blast
radius, 8 backward contract adaptation; 2 + 2 per corpus).

Schemas: `task.schema.json` (one task, `layer4.task.v1`) and
`cell.schema.json` (one task x arm x seed result, `layer4.cell.v1`).
Rules JSON Schema cannot state are checked by `validate_task` /
`validate_suite`. The plan is in LAYER4.md ("Layer 4 run").

Every Stage 2 arm runs unchanged: `to_evaluation_task` turns a task into
the `EvaluationTask` the arms already consume (`seed_symbol` = the focal
function, `prompt`, oracle slices as `pipeline_symbols` /
`required_context`). Gold is never Prism's: forward gold is the runtime
trace (`gold_from_manifest`), backward gold is the corpus's own tests
(`splice.evaluate`).
"""
from __future__ import annotations

import ast
import json
import re
import tempfile
import textwrap
from collections import defaultdict
from pathlib import Path

from harness.experiments.layer4.source_edit import locate, replace_body

SCHEMA_DIR = Path(__file__).parent
TASK_SCHEMA = SCHEMA_DIR / "task.schema.json"
CELL_SCHEMA = SCHEMA_DIR / "cell.schema.json"
CORPORA = ("fastapi", "django", "express", "trpc")
FAMILIES = ("forward_blast", "backward_adaptation")
PER_CORPUS_PER_FAMILY = 2
MAX_ANSWER_SYMBOLS = 15
CONTAMINATION_THRESHOLD = 0.5
FORWARD_CONTAMINATION_F1 = 0.5
STAGE2_ARMS = ("prism_full", "normal_oracle", "prism_oracle", "prism_ask", "arm1_hybrid_rag", "repomap_pagerank",
               "arm0_no_retrieval")
SQL_TAGS = {"SELECT": "DB_READ", "INSERT": "DB_WRITE", "UPDATE": "DB_WRITE", "DELETE": "DB_WRITE",
            "CREATE": "DB_SCHEMA", "DROP": "DB_SCHEMA", "ALTER": "DB_SCHEMA"}
_MASK_PLACEHOLDER = {"python": "...  # body withheld: implement it", "javascript": "/* body withheld: implement it */",
                     "typescript": "/* body withheld: implement it */"}

FORWARD_CONTRACT = """

Answer with one JSON object and nothing else:
{"affected_symbols": [<at most 15 fully qualified function or method names>],
 "side_effects": [<any of "DB_READ", "DB_WRITE", "DB_SCHEMA", "DB_TRANSACTION">],
 "tables": [<database table names>]}
Use the qualified names exactly as they appear in the context when you have one."""

BACKWARD_CONTRACT = """

Reply with the complete implementation of the function in one fenced code block (the whole definition, or only
its body). Do not change its signature."""


# ---------------------------------------------------------------- validation
def _schema(path: Path) -> dict:
    return json.loads(path.read_text())


def schema_errors(obj: dict, schema_path: Path = TASK_SCHEMA) -> list[str]:
    """JSON Schema errors (empty when valid). Needs `jsonschema`."""
    import jsonschema
    validator = jsonschema.Draft202012Validator(_schema(schema_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(validator.iter_errors(obj), key=lambda e: list(map(str, e.absolute_path)))]


def validate_task(task: dict, known: set[str] | None = None) -> list[str]:
    """Schema errors plus the contract's semantic rules. `known`: the
    corpus's Prism function/method names, when the index is at hand."""
    errors = schema_errors(task)
    if errors:
        return errors
    focal, view, gold = task["focal"], task["arm_view"], task["gold"]
    forward = task["family"] == "forward_blast"
    if not task["task_id"].split("_")[2] == task["corpus"]:
        errors.append("task_id corpus does not match `corpus`")
    if view["seed_symbol"] != focal["qname"]:
        errors.append("arm_view.seed_symbol must be the focal qname (prism_full's anchor)")
    short = focal["qname"].rsplit(".", 1)[-1]
    if short not in task["prompt"]:
        errors.append(f"prompt does not name the focal function `{short}`")
    if forward:
        affected = set(gold["affected_symbols"])
        if focal["qname"] in affected:
            errors.append("gold.affected_symbols contains the focal function itself")
        if not affected <= set(view["oracle_symbols"]):
            errors.append("forward oracle_symbols must contain every gold affected symbol (sanity ceiling)")
        if task["corpus"] == "django" and gold["tables"] is None:
            errors.append("Django forward gold needs `tables` from the SQL logger ([] when none)")
        if task["corpus"] == "django" and gold["side_effects"] is None:
            errors.append("Django forward gold needs `side_effects` from the SQL logger ([] when none)")
    else:
        if focal["qname"] not in view["mask"]["symbols"]:
            errors.append("backward tasks must mask the focal body (arm_view.mask.symbols)")
        if not view["exclude_paths"]:
            errors.append("backward tasks must list the gating test files in arm_view.exclude_paths")
        if not task["validity"]["negative_gate"].get("stub_seen", True):
            errors.append("negative gate failed for another reason than the STUB_IMPLEMENTATION stub")
    if task["validity"]["trace_exit_code"] != 0:
        errors.append("the trace run did not exit 0")
    if known is not None:
        names = {focal["qname"], *view["oracle_symbols"], *view["oracle_required_context"]}
        if forward:
            names |= set(gold["affected_symbols"])
        errors += [f"unknown to the Prism index: {n}" for n in sorted(names - known)]
    return errors


def validate_suite(tasks: list[dict], corpora: tuple[str, ...] = CORPORA) -> list[str]:
    """The 16-task design: 2 forward + 2 backward per corpus, unique ids
    and focal functions per corpus and family. `corpora` restricts the
    check to the corpora authored so far (no task may come from another)."""
    errors = []
    counts = defaultdict(int)
    ids, focals = set(), set()
    for t in tasks:
        counts[(t["corpus"], t["family"])] += 1
        if t["task_id"] in ids:
            errors.append(f"duplicate task_id {t['task_id']}")
        ids.add(t["task_id"])
        key = (t["corpus"], t["family"], t["focal"]["qname"])
        if key in focals:
            errors.append(f"duplicate focal {key}")
        focals.add(key)
    errors += [f"{t['task_id']}: corpus {t['corpus']} outside {corpora}" for t in tasks if t["corpus"] not in corpora]
    for corpus in corpora:
        for family in FAMILIES:
            if counts[(corpus, family)] != PER_CORPUS_PER_FAMILY:
                errors.append(f"{corpus}/{family}: {counts[(corpus, family)]} tasks, expected {PER_CORPUS_PER_FAMILY}")
    return errors


def load_suite(directory: str | Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(Path(directory).glob("l4_*.json"))]


# ---------------------------------------------------------------- arm view
def to_evaluation_task(task: dict):
    """The `EvaluationTask` every Stage 2 arm consumes (one annotation,
    used as a, b and adjudicated; kappa 1.0: the gold is mechanical)."""
    from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
    view = task["arm_view"]
    ann = GroundTruthAnnotation(annotator_id="layer4", pipeline_symbols=list(view["oracle_symbols"]),
                                required_context=set(view["oracle_required_context"]),
                                expected_solution=task["answer_contract"])
    return EvaluationTask(task_id=task["task_id"], repo=task["corpus"], pinned_commit=task["pinned_commit"],
                          seed_symbol=view["seed_symbol"], task_type=view["task_type"], prompt=task["prompt"],
                          annotation_a=ann, annotation_b=ann, adjudicated=ann, cohen_kappa=1.0,
                          root_imports=list(view["root_imports"]))


def answer_contract(task: dict) -> str:
    return FORWARD_CONTRACT if task["answer_contract"] == "forward_json_v1" else BACKWARD_CONTRACT


# ---------------------------------------------------------------- masking + leaks
def masked_definition(definition: str, language: str) -> str:
    """The definition with its body replaced by a placeholder (Python
    docstring kept): what every arm shows for a backward task's focal."""
    suffix = {"python": ".py", "javascript": ".js", "typescript": ".ts"}[language]
    lines = definition.splitlines(keepends=True)
    indent = re.match(r"[ \t]*", lines[0]).group(0) if lines else ""
    flat = textwrap.dedent(definition) if indent else definition
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"focal{suffix}"
        path.write_text(flat)
        span = locate(str(path), 1 + flat[:len(flat) - len(flat.lstrip())].count("\n"))
        body = _MASK_PLACEHOLDER[language]
        if language == "python":
            node = ast.parse(flat).body[0]
            doc = ast.get_docstring(node, clean=False) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
            if doc:
                body = f'"""{doc}"""\n{body}'
        replace_body(span, body)
        masked = path.read_text()
    return textwrap.indent(masked, indent) if indent else masked


def distinctive_lines(definition: str, signature: str, min_len: int = 20) -> list[str]:
    """Body lines long and specific enough that finding one in a context
    means the body leaked."""
    sig = {s.strip() for s in signature.splitlines()}
    out = []
    for line in definition.splitlines()[1:]:
        s = line.strip()
        if len(s) >= min_len and s not in sig and not s.startswith(("#", "//", "*", "/*", '"""', "'''")):
            out.append(s)
    return out


def mask_context(rendered: str, definition: str, masked: str) -> str:
    """Replace the focal definition wherever the rendered context carries
    it verbatim (arms render bodies as indexed: exact text)."""
    return rendered.replace(definition, masked) if definition in rendered else rendered


def leak_check(rendered: str, task: dict, definition: str | None) -> dict:
    """What of the answer a context shows: distinctive focal body lines
    (backward) and gating test files (any reference to their path)."""
    found = 0
    if definition is not None and task["family"] == "backward_adaptation":
        found = sum(1 for line in distinctive_lines(definition, task["focal"]["signature"]) if line in rendered)
    files = [p for p in task["arm_view"]["exclude_paths"] if p in rendered or Path(p).name in rendered]
    return {"focal_body_lines": found, "test_files": files}


# ---------------------------------------------------------------- gold
def side_effects_from_sql(sql: dict) -> list[str]:
    """Tags from the SQL logger's `{table: {VERB: n}}` (trace_py / trace_django)."""
    tags = set()
    for table, verbs in sql.items():
        for verb in verbs:
            if table == "_transaction":
                tags.add("DB_TRANSACTION")
            elif verb in SQL_TAGS:
                tags.add(SQL_TAGS[verb])
    return sorted(tags)


def tables_from_sql(sql: dict) -> list[str]:
    return sorted(t for t in sql if t not in ("_transaction", "?"))


def gold_from_manifest(manifest: dict, direction: str, min_count: int = 1, known: set[str] | None = None,
                       focal: str | None = None, max_depth: int | None = None) -> list[str]:
    """Gold affected symbols from a mapped trace manifest: ancestors
    (upstream), descendants (downstream) or both, at >= `min_count` calls,
    ancestors within `max_depth` call hops of the focal function
    (`ancestor_depth`; None = any depth), restricted to names the corpus
    index knows (others cannot be answered by any arm), ordered by count
    then name."""
    keys = {"upstream": ("ancestors",), "downstream": ("descendants",), "both": ("ancestors", "descendants")}[direction]
    counts: dict[str, int] = defaultdict(int)
    for key in keys:
        for name, n in manifest.get(key, {}).items():
            counts[name] += n
    depth = manifest.get("ancestor_depth", {})
    if max_depth is not None and "ancestors" in keys:
        ancestors = set(manifest.get("ancestors", {}))
        for name in list(counts):
            if name in ancestors and depth.get(name, 1 << 30) > max_depth:
                del counts[name]
    names = [n for n, c in counts.items() if c >= min_count and n != focal and (known is None or n in known)]
    return sorted(names, key=lambda n: (-counts[n], n))


# ---------------------------------------------------------------- answers + scoring
def parse_forward(reply: str) -> dict | None:
    """The last JSON object in the reply (fenced or bare), else None."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.S) or re.findall(r"\{.*\}", reply, re.S)
    for text in reversed(candidates):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def resolve_name(name: str, known: set[str]) -> str | None:
    """A model's name for a symbol -> the index's qualified name: exact,
    else separators normalized (`::`, `/`, `#`, `:` -> `.`) and a unique
    dotted-suffix match of at least two components (`Model.save`), else
    None (counts against precision)."""
    name = name.strip().strip("`").removesuffix("()")
    if name in known:
        return name
    norm = re.sub(r"(::|/|#|:)", ".", name).strip(".")
    if norm in known:
        return norm
    parts = norm.split(".")
    for k in range(len(parts), 1, -1):
        suffix = "." + ".".join(parts[-k:])
        hits = [q for q in known if q.endswith(suffix) or q == suffix[1:]]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None
    return None


def score_forward(reply: str, gold: dict, known: set[str]) -> dict:
    """Set P/R/F1 over affected symbols (answers capped at 15, unknown
    names count against precision); exact set match of side-effect tags
    and tables where the gold has them (None = not scored)."""
    parsed = parse_forward(reply)
    if parsed is None:
        return {"kind": "forward", "parsed": False, "precision": 0.0, "recall": 0.0, "f1": 0.0, "unknown_names": [],
                "truncated": False, "side_effects_exact": None if gold["side_effects"] is None else False,
                "tables_exact": None if gold["tables"] is None else False}
    names = [str(n) for n in parsed.get("affected_symbols") or [] if isinstance(n, (str, int))]
    truncated = len(names) > MAX_ANSWER_SYMBOLS
    names = names[:MAX_ANSWER_SYMBOLS]
    resolved, unknown = [], []
    for n in names:
        q = resolve_name(n, known)
        (resolved.append(q) if q else unknown.append(n))
    answer = set(resolved)
    gold_set = set(gold["affected_symbols"])
    tp = len(answer & gold_set)
    denom_p = len(answer) + len(unknown)
    precision = tp / denom_p if denom_p else 0.0
    recall = tp / len(gold_set) if gold_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    def exact(field):
        if gold[field] is None:
            return None
        got = parsed.get(field) or []
        got = {str(x).strip().upper() if field == "side_effects" else str(x).strip().strip('"`') for x in got}
        return got == set(gold[field])
    return {"kind": "forward", "parsed": True, "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "unknown_names": unknown, "truncated": truncated,
            "side_effects_exact": exact("side_effects"), "tables_exact": exact("tables")}


def score_backward(result: dict) -> dict:
    """`splice.evaluate`'s result as a cell score: binary pass."""
    return {"kind": "backward", "passed": bool(result.get("passed")), "exit_code": result.get("exit_code"),
            "mode": result.get("mode"), "seconds": result.get("seconds", 0.0)}


# ---------------------------------------------------------------- aggregation
def contamination(cells: list[dict], tasks: dict[str, dict]) -> dict[str, dict]:
    """Per task: arm0_no_retrieval's pass rate (backward) or mean F1
    (forward) over its seeds, flagged at >= the family's threshold."""
    by_task: dict[str, list[float]] = defaultdict(list)
    for c in cells:
        if c["arm"] == "arm0_no_retrieval" and c["status"] == "ok" and c["score"]:
            s = c["score"]
            by_task[c["task_id"]].append(float(s["passed"]) if s["kind"] == "backward" else s["f1"])
    out = {}
    for tid, scores in by_task.items():
        threshold = CONTAMINATION_THRESHOLD if tasks[tid]["family"] == "backward_adaptation" else FORWARD_CONTAMINATION_F1
        rate = sum(scores) / len(scores)
        out[tid] = {"arm0_score": round(rate, 4), "flagged": rate >= threshold, "seeds": len(scores)}
    return out


def summarize(cells: list[dict], tasks: dict[str, dict]) -> dict:
    """Per family and arm, over all tasks and over unflagged tasks only:
    forward mean P/R/F1 and tag/table exact-match rates; backward pass
    rate, mean context tokens and tokens per pass. Leak / error cells are
    counted and excluded."""
    flags = contamination(cells, tasks)
    flagged = {t for t, f in flags.items() if f["flagged"]}
    out: dict = {"contamination": flags, "flagged_tasks": sorted(flagged), "by_family": {}}
    for family in FAMILIES:
        fam = {}
        for arm in STAGE2_ARMS:
            rows = [c for c in cells if c["arm"] == arm and c["family"] == family]
            if not rows:
                continue
            entry = {"cells": len(rows), "excluded": {s: sum(c["status"] == s for c in rows) for s in ("leak", "error", "timeout")}}
            for label, keep in (("all", lambda c: True), ("unflagged", lambda c: c["task_id"] not in flagged)):
                ok = [c for c in rows if c["status"] == "ok" and c["score"] and keep(c)]
                if not ok:
                    continue
                tokens = [c["tokens"]["prompt"] + c["tokens"]["completion"] for c in ok]
                stats = {"n": len(ok), "mean_context_tokens": round(sum(c["tokens"]["context"] for c in ok) / len(ok), 1)}
                if family == "forward_blast":
                    for k in ("precision", "recall", "f1"):
                        stats[k] = round(sum(c["score"][k] for c in ok) / len(ok), 4)
                    for k in ("side_effects_exact", "tables_exact"):
                        scored = [c["score"][k] for c in ok if c["score"].get(k) is not None]
                        stats[k] = round(sum(scored) / len(scored), 4) if scored else None
                else:
                    passes = sum(c["score"]["passed"] for c in ok)
                    stats["pass_rate"] = round(passes / len(ok), 4)
                    stats["tokens_per_pass"] = round(sum(tokens) / passes, 1) if passes else None
                    stats["passes_per_1k_tokens"] = round(1000 * passes / sum(tokens), 4) if sum(tokens) else None
                entry[label] = stats
            fam[arm] = entry
        out["by_family"][family] = fam
    return out

